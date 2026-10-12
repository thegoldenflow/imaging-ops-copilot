"""The `followup_calls` agent's model step (spec 7.4): after a follow-up call, does a nurse call back?

Agent code (the registry entrypoint): it uses only the run. The call itself is code (followup.py: the questions
from the signed instructions, the red-flag rules on every answer, immediate escalation on a rule hit); once the
call ends this step reads the whole call through the agent's read tool (getEncounterContext, so the patient's
identifiers are learned) and asks the model for `{escalate, reason, confidence}` plus a summary for the nurse, the
symptoms reported and the patient's words to read first. The transcript goes in as untrusted text, de-identified
(rule layer and second pass); the model's output is re-identified by the LLM gateway. Recall first: the prompt says
to escalate when unsure, and the caller escalates below 0.7 confidence too.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from app.agents.prompts import load as load_prompt
from app.agents.runtime import AgentRun
from app.llm.gateway import LlmOutcome
from app.llm.providers import mock_fixture
from app.modules.voice_services import redflags

AGENT_ID = "followup_calls"
SYMPTOM_CODES = (*redflags.RULES, "worse_pain", "dizziness", "nausea_vomiting", "swelling", "wound_concern",
                 "breathing_concern", "bleeding_concern", "medication_problem", "question_for_nurse", "other")


class Symptom(BaseModel):
    code: str
    text: str = Field(max_length=300)


class ClassifierOutput(BaseModel):
    escalate: bool
    reason: str = Field(max_length=300)
    confidence: float = Field(ge=0, le=1)
    summary: str = Field(max_length=1200)
    symptoms: list[Symptom] = Field(default_factory=list, max_length=10)
    quotes: list[str] = Field(default_factory=list, max_length=3)


def classify(run: AgentRun, transcript: str, red_flags: str, rule_hits: str) -> tuple[LlmOutcome | None, str]:
    """(the model's outcome, or None when the context could not be read; the transcript as the model saw it)."""
    ctx = run.gather(("getEncounterContext", {"encounter_id": run.encounter_id}))
    if not ctx.ok:
        return None, ""
    outcome = run.model(load_prompt(run.spec.prompt_version),
                        {"red_flags": red_flags, "rule_hits": rule_hits, "codes": ", ".join(SYMPTOM_CODES)},
                        ClassifierOutput, context=ctx, untrusted={"transcript": transcript})
    seen = (run.last_model or {}).get("variables", {}).get("transcript", "") if outcome.status == "ok" else ""
    return outcome, seen


# ---------- mock model ----------
# The demo runs without an API key. This stand-in reads the de-identified call and escalates the way a recall-tuned
# classifier should: any yes to a warning sign, a worrying word, a medicine problem, a question for the nurse; an
# unclear answer gives a low confidence (which escalates too). Written alongside the eval scripts, so the mock
# baseline is optimistic.

_BLOCK = re.compile(r'<untrusted source="transcript">\n(.*?)\n</untrusted>', re.S)
_QA = re.compile(r"^Q \[(?P<key>.+?)\]: (?P<q>.*?)\nA: (?P<a>.*?)$", re.M)  # keys may hold brackets (med:...[0])
CONCERNS = [
    ("worse_pain", re.compile(r"pain (is )?(worse|getting worse|bad|terrible|a lot)|really (hurts|painful)|"
                              r"hurts (a lot|more)|疼得(厉害|很)|越来越疼|痛得(厉害|很)|很痛|很疼", re.I)),
    ("dizziness", re.compile(r"dizz|light-?headed|头晕|眩晕", re.I)),
    ("nausea_vomiting", re.compile(r"nause|vomit|throw(ing)? up|sick to my stomach|恶心|呕吐|吐了", re.I)),
    ("swelling", re.compile(r"swell|swollen|puffy|肿", re.I)),
    ("wound_concern", re.compile(r"(wound|incision|stitches|site|dressing).{0,30}(red|leak|ooz|fluid|wet|open|"
                                 r"warm|hot|smell|weep)|伤口.{0,6}(红|渗|流|裂|热|臭|湿)", re.I)),
    ("breathing_concern", re.compile(r"breath|cough(ing)? (more|a lot)|wheez|咳嗽|喘", re.I)),
    ("bleeding_concern", re.compile(r"blood|bleed|bruis|black (stool|poop)|出血|流血|瘀青|黑便", re.I)),
    ("question_for_nurse", re.compile(r"\?|should i|can i|is it (ok|okay|normal|safe)|do i need|要不要|可以吗|"
                                      r"能不能|是不是|正常吗|怎么办", re.I)),
]
WORRY = re.compile(r"worried|not (feeling )?(well|good|great|right)|feel(ing)? (worse|awful|terrible|bad)|担心|"
                   r"不舒服|不太好|很难受|难受", re.I)


def _answers(transcript: str) -> list[tuple[str, str, str]]:
    return [(m.group("key"), m.group("q"), m.group("a").strip()) for m in _QA.finditer(transcript)]


@mock_fixture(AGENT_ID)
def _mock(text: str, images: list, attempt: int) -> dict:
    m = _BLOCK.search(text)
    transcript = m.group(1) if m else ""
    rule = re.search(r"Red flags the rule layer found in the call: (.*)\n", text)
    rule_hits = (rule.group(1).strip() if rule else "none") or "none"
    symptoms, quotes, reasons, unclear = [], [], [], 0
    for key, _, answer in _answers(transcript):
        kind = {"feeling": "open"}.get(key, key.split(":", 1)[0])
        said = redflags.polarity(answer)
        concern = None
        if kind == "flag" and said == "yes":
            concern = ("other", f"answered yes to a warning sign ({key.split(':', 1)[1]})")
        elif kind == "med" and said == "no":
            concern = ("medication_problem", "not taking a medicine as on the timetable")
        elif kind == "med_problems" and said == "yes":
            concern = ("medication_problem", "a problem with the medicines")
        elif kind in ("open", "wound", "care", "other", "med_problems", "flag", "followup") and said != "no":
            for code, rx in CONCERNS:
                if rx.search(answer):
                    concern = (code, code.replace("_", " "))
                    break
            if concern is None and WORRY.search(answer):
                concern = ("other", "the patient does not feel well")
        if concern:
            symptoms.append({"code": concern[0], "text": answer[:300]})
            quotes.append(answer[:120])
            reasons.append(concern[1])
        if said == "unclear" and kind in ("flag", "med", "med_problems") and len(answer.split()) <= 3:
            unclear += 1
    escalate = bool(reasons) or rule_hits != "none"
    if rule_hits != "none" and not reasons:
        reasons.append(f"red flags from the rule layer ({rule_hits})")
    confidence = 0.6 if unclear and not escalate else 0.85 if escalate else 0.9
    summary = ("The patient reports: " + "; ".join(dict.fromkeys(reasons)) + ".") if reasons else \
        "The patient reports no warning signs, is taking the medicines as listed and knows the follow-up plan."
    return {"escalate": escalate, "reason": "; ".join(dict.fromkeys(reasons))[:300] or
            ("Some answers were unclear." if unclear else "No warning signs or concerns reported."),
            "confidence": confidence, "summary": summary[:1200], "symptoms": symptoms[:10], "quotes": quotes[:3]}
