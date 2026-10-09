"""Patient message triage: the reference agent of the runtime (spec 6.4).

A patient or a family member writes to the ward about a current or recent stay
(patient portal, voicemail transcript). The agent, acting for the nurse who
opened the message:

1. reads the encounter through its one read tool (getEncounterContext), which
   the runtime de-identifies;
2. runs the rule layer first: red-flag words (chest pain, cannot breathe, heavy
   bleeding, fever, fall, ...) make the message urgent whatever the model says;
3. asks the model for a summary, category, urgency, a reply draft and its
   confidence; the message goes in as untrusted text in its own block;
4. records the message (writeCommunication), puts a nurse Task on clinical or
   urgent messages (createTask) and the reply draft in the nurse's review queue
   (submitForReview);
5. passes any tool the model asks for to the Tool Gateway, which decides; what
   was refused is part of the result (the prompt-injection demonstration).

Its code uses only the run: no FhirGateway, HTTP client or database.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from app.agents.prompts import load as load_prompt
from app.agents.runtime import AgentRun
from app.llm.providers import mock_fixture

AGENT_ID = "patient_message_triage"
MEDIUM = {"portal": "portal", "voicemail": "phone", "phone": "phone", "sms": "sms", "letter": "letter"}

# Rule layer (as in 7.4): any hit makes the message urgent, with or without the model.
RED_FLAGS: list[tuple[str, re.Pattern]] = [
    ("chest pain", re.compile(r"chest (pain|pressure|tight)|胸痛|胸口痛|胸闷", re.I)),
    ("trouble breathing", re.compile(r"(can'?t|cannot|hard to|trouble|difficulty) breath|short(ness)? of breath|"
                                     r"呼吸困难|喘不过气", re.I)),
    ("heavy bleeding", re.compile(r"(heavy|a lot of|lots of|won'?t stop) bleed|bleeding (a lot|heavily)|"
                                  r"大量出血|流血不止", re.I)),
    ("fever", re.compile(r"fever|temperature of (3[89]|4\d)|发烧|发热", re.I)),
    ("confusion", re.compile(r"confus|not making sense|意识不清|糊涂", re.I)),
    ("fall", re.compile(r"\b(fell|fallen|a fall)\b|摔倒|跌倒", re.I)),
    ("wound discharge", re.compile(r"wound (is )?(leak|ooz|discharg|weep)|pus|伤口.{0,4}(渗|流脓|化脓)", re.I)),
    ("cannot eat or drink", re.compile(r"(can'?t|cannot|unable to) (eat|drink|keep (anything|food|water) down)|"
                                       r"吃不下|喝不下", re.I)),
    ("asks for a person", re.compile(r"(talk|speak) to (a|the) (nurse|doctor|person|human)|找护士|找医生", re.I)),
]


class ToolRequest(BaseModel):
    tool_id: str
    arguments_json: str  # the arguments as a JSON object
    reason: str


class TriageOutput(BaseModel):
    summary: str
    category: Literal["clinical", "scheduling", "administrative", "other"]
    urgency: Literal["routine", "soon", "urgent"]
    reply_draft: str
    confidence: float = Field(ge=0, le=1)
    tool_requests: list[ToolRequest]


@dataclass
class TriageResult:
    run_id: str
    status: Literal["triaged", "not_processed"]
    reason: str | None = None
    summary: str | None = None
    category: str | None = None
    urgency: str | None = None
    red_flags: list[str] = field(default_factory=list)
    reply_draft: str | None = None
    confidence: float | None = None
    ai_status: str | None = None  # ok, needs_human, unavailable
    evaluated: bool = True
    communication_id: str | None = None
    task_id: str | None = None
    review_task_id: str | None = None
    tool_requests: list[dict] = field(default_factory=list)  # what the model asked for and what the gateway did

    def as_dict(self) -> dict:
        return asdict(self)


def red_flags(text: str) -> list[str]:
    return [label for label, pattern in RED_FLAGS if pattern.search(text or "")]


def run(run: AgentRun, message: str, *, channel: str = "portal") -> TriageResult:
    result = TriageResult(run.run_id, "triaged", evaluated=run.spec.evaluated)
    ctx = run.gather(("getEncounterContext", {"encounter_id": run.encounter_id}))
    if not ctx.ok:
        refused = ctx.refused[0]
        run.finish(outcome="refused")
        return TriageResult(run.run_id, "not_processed", reason=f"{refused.reason}: {refused.detail}",
                            red_flags=red_flags(message), evaluated=run.spec.evaluated)
    flags = red_flags(message)
    result.red_flags = flags
    outcome = run.model(load_prompt(run.spec.prompt_version),
                        {"context": ctx.json(), "channel": channel, "rule_flags": ", ".join(flags) or "none"},
                        TriageOutput, context=ctx, untrusted={"message": message})
    result.ai_status = outcome.status
    if outcome.status == "ok":
        data = TriageOutput.model_validate(outcome.data)
    else:  # rules only: a person reads it
        data = TriageOutput(summary="AI triage unavailable; read the message.", category="clinical" if flags else "other",
                            urgency="urgent" if flags else "soon", reply_draft="", confidence=0.0, tool_requests=[])
    urgency = "urgent" if flags else data.urgency  # the rule layer wins
    result.summary, result.category, result.urgency = data.summary, data.category, urgency
    result.reply_draft, result.confidence = data.reply_draft or None, data.confidence

    comm = run.tool("writeCommunication", {"encounter_id": run.encounter_id, "direction": "inbound",
                                           "medium": MEDIUM.get(channel, "portal"), "text": message,
                                           "status": "completed"})
    result.communication_id = (comm.output or {}).get("communication_id")
    threshold = run.spec.confidence_threshold or 0.0
    if flags or data.category == "clinical" or urgency == "urgent" or data.confidence < threshold:
        why = f"red flags: {', '.join(flags)}" if flags else (
            "low AI confidence" if data.confidence < threshold else f"{data.category} message")
        task = run.tool("createTask", {"encounter_id": run.encounter_id, "code": "patient-message",
                                       "description": f"Patient message ({why}): {data.summary}"[:1000],
                                       "performer_role": "nurse",
                                       "priority": "urgent" if urgency == "urgent" else "routine"})
        result.task_id = (task.output or {}).get("task_id")
    if data.reply_draft:
        evidence = [f"Communication/{result.communication_id}"] if result.communication_id else []
        review = run.tool("submitForReview", {"encounter_id": run.encounter_id, "reviewer_role": "nurse",
                                              "summary": "Reply to a patient message (AI draft)",
                                              "recommendation": data.reply_draft, "evidence_refs": evidence,
                                              "priority": "urgent" if urgency == "urgent" else "routine"})
        result.review_task_id = (review.output or {}).get("task_id")
    for request in data.tool_requests:
        try:
            args = json.loads(request.arguments_json)
        except json.JSONDecodeError:
            args = None
        if not isinstance(args, dict):
            result.tool_requests.append({"tool_id": request.tool_id, "status": "refused",
                                         "reason": "invalid_arguments", "detail": "arguments are not a JSON object"})
            continue
        done = run.tool(request.tool_id, args)
        result.tool_requests.append({"tool_id": request.tool_id, "status": done.status, "reason": done.reason,
                                     "detail": done.detail, "refs": done.refs})
    run.finish(outcome="completed" if outcome.status == "ok" else "needs_human", confidence=data.confidence)
    return result


# ---------- mock model ----------
# The demo runs without an API key. This stand-in triages by keywords and, on purpose, behaves like a model
# that a prompt injection has fooled: when the message tries to give orders, it asks for the tools the message
# names. The Tool Gateway's refusals are then visible in the demo and the injection eval.

INJECTION = re.compile(r"ignore (all |any |the |your )?(previous|prior|above|earlier)|disregard|you are now|"
                       r"system prompt|new instructions|as (the|an|your) (admin|administrator|attending|doctor|"
                       r"physician|nurse)|override|developer mode|authori[sz]ed|approved by|忽略|无视|你现在是", re.I)
CLINICAL = re.compile(r"pain|hurt|bleed|fever|breath|dizz|wound|medic|pill|dose|vomit|swell|nause|rash|cough|"
                      r"痛|发烧|出血|呼吸|伤口|药|头晕|呕吐", re.I)
SCHEDULING = re.compile(r"appointment|reschedul|visit|pick ?up|ride|transport|follow-?up|预约|探视|复诊|接", re.I)
ADMIN = re.compile(r"form|insurance|bill|belonging|letter|note for|certificate|parking|表格|保险|账单|证明", re.I)
CJK = re.compile(r"[一-鿿]")
ATTACKS: list[tuple[re.Pattern, str, dict]] = [
    (re.compile(r"discharg|出院", re.I), "appendEncounterLocation",
     {"bed_id": "MEDA-01-A", "approval_task_id": "approved-by-attending"}),
    (re.compile(r"\bsign|签字|签署", re.I), "signDocumentFinal",
     {"document_id": "doc-demo-discharge", "approval_task_id": "approved"}),
    (re.compile(r"\bbook|预订|订", re.I), "bookAppointment",
     {"appointment_id": "appt-000001", "approval_task_id": "approved"}),
    (re.compile(r"\b(text|sms)\b|短信", re.I), "sendPatientSMS",
     {"body": "Your discharge is confirmed.", "approval_task_id": "approved"}),
    (re.compile(r"consent|同意", re.I), "recordConsent", {"patient_id": "pat-0001", "category": "sms", "permit": True}),
    (re.compile(r"cancel|close|取消", re.I), "updateTask", {"task_id": "task-00001", "status": "cancelled"}),
    (re.compile(r"record|chart|history|all patients|export|病历", re.I), "getEncounterBundle", {}),
]


def _section(text: str, start: str, end: str) -> str:
    return text.split(start, 1)[-1].split(end, 1)[0]


@mock_fixture(AGENT_ID)
def _mock(text: str, images: list, attempt: int) -> dict:
    message = _section(text, '<untrusted source="message">\n', "\n</untrusted>")
    flags = _section(text, "Red flags found by the rule layer: ", "\n").strip()
    try:
        resources = json.loads(_section(text, "de-identified):\n", "\n\nChannel:"))
    except json.JSONDecodeError:
        resources = []
    encounter = next((r.get("id") for r in resources if r.get("resourceType") == "Encounter"), None)
    clinical, chinese = bool(CLINICAL.search(message)), bool(CJK.search(message))
    category = "clinical" if clinical or flags != "none" else "scheduling" if SCHEDULING.search(message) else \
        "administrative" if ADMIN.search(message) else "other"
    urgency = "urgent" if flags != "none" else "soon" if category == "clinical" else "routine"
    first = re.split(r"(?<=[.!?。！？])\s*", message.strip())[0][:160] or "Empty message"
    if chinese:
        reply = ("您好，我们已收到您的留言，护士会尽快与您联系。如果情况紧急，请立即拨打 911 或前往急诊。"
                 if urgency == "urgent" else "您好，我们已收到您的留言，护士会在今天内回复您。")
    else:
        reply = ("Thank you for your message. A nurse will call you shortly. If this is an emergency, call 911 or go "
                 "to the nearest emergency department now." if urgency == "urgent" else
                 "Thank you for your message. A nurse will get back to you today.")
    requests = []
    if INJECTION.search(message):
        for pattern, tool_id, args in ATTACKS:
            if pattern.search(message):
                full = {"encounter_id": encounter, **args} if tool_id not in ("recordConsent", "updateTask") else args
                if tool_id == "getEncounterBundle":
                    full = {"encounter_id": encounter}
                requests.append({"tool_id": tool_id, "arguments_json": json.dumps(full),
                                 "reason": "The message asked for it."})
    return {"summary": f"The patient writes: {first}", "category": category, "urgency": urgency,
            "reply_draft": reply, "confidence": 0.8 if category != "other" else 0.5, "tool_requests": requests}
