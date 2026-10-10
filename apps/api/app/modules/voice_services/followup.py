"""Post-discharge follow-up calls (spec 7.4): scheduling, the call, red-flag escalation and the write-back.

Scheduling (`schedule`), 48 hours after the discharge (FOLLOWUP_CALL_DELAY_H; the journey's timer when the stay
has a journey workflow): the call is placed only when all four conditions hold, checked in code through the
module's own audited reads:

- a discharge time on the stay (the encounter finished, with its end);
- the patient's `followup_call` consent (WP4: permit, not missing or declined);
- a phone number;
- the patient instructions `pinstr-<stay>` signed final (WP7).

When one is missing a nurse gets a manual follow-up Task naming what is missing (the journey writes it as its own
Task, the background sweep as this module). Otherwise the questions are built from the signed instructions (their
red flags one by one, each medicine against the timetable, the follow-up dates and places, wound and daily care,
anything else), in the patient's language (English, Simplified or Traditional Chinese, from phrases.py and WP7's
renderer), and the call is recorded as a Communication in preparation.

The call (`start_call`, `answer`): text-simulated, one run of the `followup_calls` agent per patient answer. The
patient confirms their birth date first. Every answer goes through the red-flag rules (redflags.py); a hit, or a
yes to one of the patient's own warning signs, escalates at once without the model: the nurse call-back promise
(or the 911 prompt, ending the call, for an emergency), a high-priority nurse Task, a `followup.escalated` event
(the notification). After the last question the model classifier reads the whole call (followup_agent.py);
`escalate` or a confidence below 0.7 escalates too, and an unavailable model sends the call to a nurse for review.
Write-back: one Communication per call (status, summary, escalated flag), each reported symptom as a
patient-reported Observation (preliminary), the nurse Task. The model never writes what the patient hears.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable

from app.agents.runtime import AgentRun, runtime
from app.core.config import settings
from app.core.context import acting_as_system
from app.core.models import StaffUser
from app.core.store import Store, get_store
from app.ehr.clock import hospital_now
from app.ehr.consent import consent_status
from app.ehr.events import bus, platform_event
from app.fhir.dt import parse
from app.modules.documentation import phrases as DP
from app.modules.documentation import terms as T
from app.modules.documentation.document import read_data
from app.modules.frontdesk.tools import parse_dob
from app.modules.voice_services import followup_agent, redflags
from app.modules.voice_services import phrases as P
from app.modules.voice_services.callcenter import MODULE, acting_agent, patient_name, preferred_language, system_fhir
from app.modules.voice_services.models import FollowupCall, Turn

TABLE = "followup_calls"
AGENT = "followup_calls"
CONFIDENCE_FLOOR = 0.7  # 7.4: a classifier less sure than this escalates as well
PRIORITY_RANK = {"stat": 0, "urgent": 1, "asap": 2, "routine": 3, None: 4}
MISSING_TEXT = {"discharge": "no discharge time is recorded for the stay",
                "consent": "no follow-up call consent on file", "consent_declined": "the patient declined follow-up calls",
                "phone": "no phone number on file", "instructions": "the discharge instructions are not signed final",
                "instructions_missing": "there are no discharge instructions for the stay"}


def table(store: Store | None = None):
    return (store or get_store()).module(TABLE, dict)


def get(call_id: str, store: Store | None = None) -> FollowupCall | None:
    return table(store).get(call_id)


def save(call: FollowupCall, store: Store | None = None) -> None:
    call.updated_at = datetime.now()
    table(store)[call.id] = call
    (store or get_store()).touch()


def call_id_for(encounter_id: str) -> str:
    return f"FU-{encounter_id}"


def instructions_id(encounter_id: str) -> str:
    return f"pinstr-{encounter_id}"


# ---------- the four conditions ----------


@dataclass
class Conditions:
    encounter_id: str
    patient_id: str | None = None
    unit_id: str | None = None
    discharged_at: datetime | None = None
    language: str = "en"
    instructions: dict | None = None  # the signed DocumentReference
    patient: dict | None = None
    missing: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


def _unit(encounter: dict) -> str | None:
    beds = [(loc.get("location") or {}).get("reference", "").split("/")[-1] for loc in encounter.get("location") or []]
    return beds[-1].split("-", 1)[0] if beds and beds[-1] else None


def check(encounter_id: str, discharged_at: datetime | None = None) -> Conditions:
    """The four conditions; `discharged_at`: the discharge the caller already knows of (the journey's discharge
    event) when the EHR has not closed the encounter yet."""
    fhir = system_fhir()
    found = fhir.read("Encounter", encounter_id)
    if found is None:
        raise LookupError(f"Encounter/{encounter_id} not found")
    enc = found.to_fhir()
    out = Conditions(encounter_id, (enc.get("subject") or {}).get("reference", "").split("/")[-1] or None,
                     _unit(enc))
    end = (enc.get("period") or {}).get("end")
    if enc.get("status") == "finished" and end:
        out.discharged_at = parse(end)
    elif discharged_at is not None:
        out.discharged_at = discharged_at
    else:
        out.missing.append("discharge")
        out.reasons.append(MISSING_TEXT["discharge"])
    patient = fhir.read("Patient", out.patient_id).to_fhir() if out.patient_id else None
    out.patient = patient
    out.language = preferred_language(patient)
    state = consent_status(fhir, out.patient_id)["followup_call"] if out.patient_id else "missing"
    if state != "permit":
        out.missing.append("consent")
        out.reasons.append(MISSING_TEXT["consent_declined" if state == "deny" else "consent"])
    if not any(t.get("system") == "phone" and t.get("value") for t in (patient or {}).get("telecom") or []):
        out.missing.append("phone")
        out.reasons.append(MISSING_TEXT["phone"])
    doc = fhir.read("DocumentReference", instructions_id(encounter_id))
    doc = doc.to_fhir() if doc is not None else None
    if doc is None or doc.get("docStatus") != "final" or not read_data(doc):
        out.missing.append("instructions")
        out.reasons.append(MISSING_TEXT["instructions" if doc is not None else "instructions_missing"])
    else:
        out.instructions = doc
    return out


# ---------- the script ----------


def _lower(text: str, lang: str) -> str:
    return text[:1].lower() + text[1:] if lang == "en" else text


def _q(key: str, kind: str, lang: str, phrase: DP.Phrase, **values) -> dict:
    texts = {"en": P.say(phrase, "en", **{k: v["en"] if isinstance(v, dict) else v for k, v in values.items()})}
    texts[lang] = P.say(phrase, lang, **{k: v[lang] if isinstance(v, dict) else v for k, v in values.items()})
    return {"key": key, "kind": kind, "text": texts}


def build_script(content: dict, lang: str) -> list[dict]:
    """The questions, in order, from the signed instructions (patient-instructions@1, WP7)."""
    from app.modules.documentation.models import InstructionsContent
    from app.modules.documentation.render import render

    langs = ("en", lang)
    conditions = [r.get("code") for r in content.get("reason") or [] if r.get("kind") == "diagnosis"]
    script = [{"key": "identity", "kind": "identity", "text": {}},
              _q("feeling", "open", lang, P.FU["feeling"])]
    for flag in content.get("red_flags") or []:
        if flag.get("code") in DP.RED_FLAGS:
            phrase, action = DP.RED_FLAGS[flag["code"]]
            q = _q(f"flag:{flag['code']}", "flag", lang, P.FU["flag"],
                   flag={g: _lower(phrase[g], g) for g in langs})
            q.update(code=flag["code"], emergency=action == "emergency")
            script.append(q)
    medicines = content.get("medicines") or []
    for m in medicines:
        term = T.drug_term(m.get("rxcui") or "", m.get("name") or "", conditions)
        name = {g: term.name[g] for g in langs}
        if m.get("category") == "stopped":
            q = _q(f"med:{m['item']}", "med", lang, P.FU["med_stopped"], name=name)
        else:
            times = {g: P.join([_lower(DP.TIMES[t][g], g) for t in m.get("times") or [] if t in DP.TIMES], g) or
                     ("as prescribed" if g == "en" else "按医嘱" if g == "zh-Hans" else "按醫囑") for g in langs}
            q = _q(f"med:{m['item']}", "med", lang, P.FU["med"], name=name, times=times)
        q["category"] = m.get("category")
        script.append(q)
    if medicines:
        script.append(_q("med_problems", "med_problems", lang, P.FU["med_problems"]))
    rendered = {g: render(InstructionsContent.model_validate(content), g) for g in langs}
    for f in content.get("follow_ups") or []:
        key = f"followup:{f['item']}"
        line = {g: next((ln["text"] for b in rendered[g]["blocks"] if b["key"] == "follow_up" for ln in b["lines"]
                         if ln["key"] == key), "") for g in langs}
        q = _q(key, "followup", lang, P.FU["follow_up"], appointment=line)
        q["tell"] = {g: P.say(P.FU["follow_up_tell"], g, appointment=line[g]) for g in langs}
        script.append(q)
    care = [c.get("code") for c in content.get("daily_care") or [] if c.get("code") in DP.DAILY_CARE]
    if any(DP.DAILY_CARE[c][0] == "wound" for c in care) or any(
            f.get("code") in ("wound_infection", "puncture_site") for f in content.get("red_flags") or []):
        script.append(_q("wound", "wound", lang, P.FU["wound"]))
    other_care = [c for c in care if DP.DAILY_CARE[c][0] != "wound"][:3]
    if other_care:
        items = {g: P.join([_lower(DP.DAILY_CARE[c][1][g].rstrip(".。"), g) for c in other_care], g) for g in langs}
        script.append(_q("care", "care", lang, P.FU["care"], items=items))
    script.append(_q("other", "other", lang, P.FU["other"]))
    return script


# ---------- scheduling ----------


def _run(call: FollowupCall | None = None, *, encounter_id: str):
    return runtime.start(acting_agent(AGENT), user=None, encounter_id=encounter_id,
                         context_id=f"FollowupCall/{call.id}" if call else None)


def module_manual_task(encounter_id: str, reason: str, *, priority: str = "routine") -> str | None:
    """The manual follow-up Task the module writes (the sweep's degradation, a call that could not go ahead)."""
    with acting_as_system(), runtime.start(MODULE, user=None, encounter_id=encounter_id) as run:
        result = run.tool("createCallTask", {
            "code": "followup-manual", "performer_role": "nurse", "priority": priority, "encounter_id": encounter_id,
            "description": f"Follow up with the patient by phone yourself: {reason}."[:1000]})
        run.finish(outcome="completed" if result.ok else result.status)
    return (result.output or {}).get("task_id") if result.ok else None


def schedule(store: Store, encounter_id: str, *, source: str = "sweep", workflow_id: str | None = None,
             manual_task: Callable[[str], str | None] | None = None,
             discharged_at: datetime | None = None) -> FollowupCall:
    """The stay's follow-up call, scheduled once (a second call finds the row): manual or scheduled."""
    if (existing := get(call_id_for(encounter_id), store)) is not None:
        return existing
    cond = check(encounter_id, discharged_at)
    call = FollowupCall(id=call_id_for(encounter_id), encounter_id=encounter_id, patient_id=cond.patient_id or "",
                        unit_id=cond.unit_id, discharged_at=cond.discharged_at, source=source, workflow_id=workflow_id,
                        language=cond.language)
    if cond.missing:
        reason = "; ".join(cond.reasons)
        call.status, call.missing, call.reason = "manual", cond.missing, reason
        call.task_id = (manual_task or (lambda why: module_manual_task(encounter_id, why)))(reason)
        call.review_status, call.priority = "open", "routine"
        save(call, store)
        return call
    call.instructions_ref = f"DocumentReference/{cond.instructions['id']}"
    call.script = build_script(read_data(cond.instructions)["content"], cond.language)
    now = hospital_now(store)
    call.due_at = max(now, cond.discharged_at + timedelta(hours=settings.followup_call_delay_h)) \
        if source == "sweep" else now
    with acting_as_system(), _run(call, encounter_id=encounter_id) as run:
        record = run.tool("writeCallRecord", {
            "patient_id": call.patient_id, "encounter_id": encounter_id, "kind": "followup-call",
            "direction": "outbound", "status": "preparation", "language": call.language,
            "about": [call.instructions_ref],
            "text": f"Post-discharge follow-up call scheduled for {call.due_at:%Y-%m-%d %H:%M} (hospital time), in "
                    f"{call.language}; {len(call.script) - 1} questions from the signed discharge instructions."})
        run.finish(outcome="completed" if record.ok else record.status)
    call.run_ids.append(run.run_id)
    if not record.ok:  # the gateway refused (e.g. consent withdrawn a moment ago): a nurse calls
        reason = f"the call could not be scheduled ({record.reason})"
        call.status, call.reason = "manual", reason
        call.task_id = (manual_task or (lambda why: module_manual_task(encounter_id, why)))(reason)
        call.review_status, call.priority = "open", "routine"
    else:
        call.communication_id = record.output["communication_id"]
    save(call, store)
    return call


def candidates(store: Store) -> list[dict]:
    """Inpatient stays discharged between the delay and the end of the window (hospital time), not yet scheduled
    and not followed by a journey workflow (which schedules its own call)."""
    from app.workflows import progress
    from app.workflows.model import journey_id

    now = hospital_now(store)
    earliest = now - timedelta(hours=settings.followup_call_window_h)
    latest = now - timedelta(hours=settings.followup_call_delay_h)
    found = system_fhir().search("Encounter", cls="IMP", status="finished", ended_from=earliest,
                                 ended_to=latest + timedelta(seconds=1))
    rows = table(store)
    out = []
    for enc in found:
        end = parse((enc.get("period") or {}).get("end"))
        if end is None or not earliest <= end <= latest or call_id_for(enc["id"]) in rows:
            continue
        if progress.get(journey_id(enc["id"])) is not None:
            continue
        out.append(enc)
    return sorted(out, key=lambda e: e["id"])


def sweep(store: Store) -> list[str]:
    """Schedule the follow-up calls that are due (the API's hospital loop, and on demand)."""
    return [schedule(store, enc["id"], source="sweep").id for enc in candidates(store)]


_SWEPT: dict[str, object] = {}


def sweep_step(store: Store) -> None:
    """Background step of the API's hospital loop: sweep when the hospital clock has moved 5 minutes or the demo
    data was regenerated (a sweep reads the stays discharged in the window; not every second)."""
    now = hospital_now(store)
    key = (now.replace(minute=now.minute - now.minute % 5, second=0, microsecond=0),
           (store.modules.get("hospital_plan") or {}).get("generated_for"))
    if _SWEPT.get("key") == key:
        return
    sweep(store)
    _SWEPT["key"] = key


# ---------- the call ----------


class CallError(ValueError):
    pass


def start_call(store: Store, call_id: str, user: StaffUser | None = None) -> tuple[str, FollowupCall]:
    call = get(call_id, store)
    if call is None:
        raise LookupError(f"No follow-up call {call_id}")
    if call.status != "scheduled":
        raise CallError(f"The call is {call.status}; only a scheduled call can be placed")
    if consent_status(system_fhir(), call.patient_id)["followup_call"] != "permit":  # withdrawn since scheduling
        reason = "the patient no longer consents to follow-up calls"
        call.status, call.reason, call.review_status, call.priority = "manual", reason, "open", "routine"
        call.missing = [*call.missing, "consent"]
        call.task_id = module_manual_task(call.encounter_id, reason)
        save(call, store)
        raise CallError(reason)
    call.status, call.started_at, call.step, call.retries = "in_progress", datetime.now(), 0, 0
    with acting_as_system(), _run(call, encounter_id=call.encounter_id) as run:
        record = run.tool("writeCallRecord", {
            "patient_id": call.patient_id, "encounter_id": call.encounter_id, "communication_id": call.communication_id,
            "kind": "followup-call", "direction": "outbound", "status": "in-progress", "language": call.language,
            "text": "Post-discharge follow-up call in progress."})
        run.finish(outcome="completed" if record.ok else record.status)
    call.run_ids.append(run.run_id)
    if not record.ok:  # e.g. the patient withdrew the consent after scheduling: no call, a nurse follows up
        reason = f"the call could not go ahead ({record.reason}: {record.detail})"
        call.status, call.reason, call.review_status, call.priority = "manual", reason, "open", "routine"
        call.task_id = module_manual_task(call.encounter_id, reason)
        save(call, store)
        raise CallError(reason)
    patient = system_fhir().read("Patient", call.patient_id)
    first = patient_name(patient.to_fhir() if patient else None, call.language)
    reply = P.say(P.FU["intro"], call.language, first=first)
    call.transcript.append(Turn(role="agent", text=reply, lang=call.language, question="identity"))
    save(call, store)
    return reply, call


def answer(store: Store, call_id: str, text: str) -> tuple[str, FollowupCall, bool]:
    call = get(call_id, store)
    if call is None:
        raise LookupError(f"No follow-up call {call_id}")
    if call.status != "in_progress":
        raise CallError(f"The call is {call.status}")
    text = " ".join((text or "").split())[:800]
    if not text:
        raise CallError("Empty answer")
    question = call.script[call.step]
    call.transcript.append(Turn(role="caller", text=text, lang=call.language, question=question["key"]))
    with acting_as_system(), _run(call, encounter_id=call.encounter_id) as run:
        engine = _Call(store, call, run)
        reply = engine.handle(question, text)
        run.finish(outcome="completed")
    call.run_ids.append(run.run_id)
    asked = call.script[call.step]["key"] if not engine.ended and call.step < len(call.script) else None
    call.transcript.append(Turn(role="agent", text=reply, lang=call.language, question=asked))
    save(call, store)
    return reply, call, engine.ended


def no_answer(store: Store, call_id: str) -> FollowupCall:
    """The patient did not pick up (a demo control): the call record says so and a nurse follows up by hand."""
    call = get(call_id, store)
    if call is None:
        raise LookupError(f"No follow-up call {call_id}")
    if call.status not in ("scheduled", "in_progress"):
        raise CallError(f"The call is {call.status}")
    with acting_as_system(), _run(call, encounter_id=call.encounter_id) as run:
        run.tool("writeCallRecord", {
            "patient_id": call.patient_id, "encounter_id": call.encounter_id, "communication_id": call.communication_id,
            "kind": "followup-call", "direction": "outbound", "status": "not-done", "status_reason": "no answer",
            "language": call.language, "text": "Post-discharge follow-up call: no answer."})
        run.finish(outcome="completed")
    call.status, call.ended_at, call.reason = "no_answer", datetime.now(), "the patient did not answer the call"
    call.task_id = module_manual_task(call.encounter_id, "the automated follow-up call was not answered")
    call.review_status, call.priority = "open", "routine"
    save(call, store)
    return call


class _Call:
    """One answer of the patient: rules first, escalation at once, the next question; the classifier at the end."""

    def __init__(self, store: Store, call: FollowupCall, run: AgentRun) -> None:
        self.store, self.call, self.run = store, call, run
        self.ended = False

    def say(self, key: str, **values) -> str:
        return P.say(P.FU[key], self.call.language, **values)

    def text_of(self, q: dict) -> str:
        return (q.get("text") or {}).get(self.call.language) or (q.get("text") or {}).get("en", "")

    def first_name(self) -> str:
        found = system_fhir().read("Patient", self.call.patient_id)
        return patient_name(found.to_fhir() if found else None, self.call.language)

    def handle(self, q: dict, text: str) -> str:
        if q["kind"] == "identity":
            return self._identity(text)
        hits = redflags.scan(text)
        said = redflags.polarity(text)
        if q["kind"] == "flag" and said == "yes":
            rule = redflags.INSTRUCTION_FLAG_RULE.get(q["code"])
            phrase = DP.RED_FLAGS[q["code"]][0]["en"]
            hit = redflags.hit(rule, text, how="answer", question=q["key"]) if rule else redflags.Hit(
                q["code"], phrase, q.get("emergency", False), text[:300], "answer", None, q["key"])
            hit.emergency = hit.emergency or q.get("emergency", False)
            hits = [hit, *[h for h in hits if h.code != hit.code]]
        if not hits and said == "unclear" and q["kind"] in ("flag", "med", "med_problems", "followup") and \
                self.call.retries < 1 and len(text.split()) <= 6:
            self.call.retries += 1
            return self.say("unclear")
        self.call.retries = 0
        self.call.answers[q["key"]] = {"answer": said if q["kind"] != "open" else "text", "text": text,
                                       "kind": q["kind"]}
        known = {h["code"] for h in self.call.rule_hits}
        new = [h for h in hits if h.code not in known]
        prefix = []
        if new:
            self.call.rule_hits += [h.as_dict() for h in new]
            self._escalate(new)
            if any(h.emergency for h in new):
                self._finish()
                return self.say("escalate_emergency")
            prefix.append(self.say("escalate_call", minutes=settings.followup_callback_minutes))
        elif q["kind"] == "followup" and said == "no":
            prefix.append((q.get("tell") or {}).get(self.call.language, ""))
        if q["kind"] in ("other", "open") and followup_agent.CONCERNS[-1][1].search(text):
            prefix.append(self.say("no_advice"))
        if new and q["kind"] != "other":  # after an escalation: straight to "anything else", the nurse covers the rest
            self.call.step = len(self.call.script) - 1
        else:
            self.call.step += 1
        sep = "" if self.call.language.startswith("zh") else " "  # Chinese sentences run on without a space
        if self.call.step >= len(self.call.script):
            return sep.join(x for x in [*prefix, self._finish()] if x).strip()
        return sep.join(x for x in [*prefix, self.text_of(self.call.script[self.call.step])] if x).strip()

    def _identity(self, text: str) -> str:
        said = parse_dob(text)
        found = system_fhir().read("Patient", self.call.patient_id)
        birth = (found.to_fhir() if found else {}).get("birthDate")
        if said is not None and said.isoformat() == birth:
            self.call.answers["identity"] = {"answer": "confirmed", "text": "(birth date confirmed)", "kind": "identity"}
            self.call.step = 1
            return self.text_of(self.call.script[1])
        self.call.dob_failures += 1
        if self.call.dob_failures < 2:
            return self.say("dob_retry")
        self.ended = True
        self.call.status, self.call.ended_at = "not_confirmed", datetime.now()
        self.call.reason = "the patient's identity could not be confirmed on the follow-up call"
        self.run.tool("writeCallRecord", {
            "patient_id": self.call.patient_id, "encounter_id": self.call.encounter_id,
            "communication_id": self.call.communication_id, "kind": "followup-call", "direction": "outbound",
            "status": "not-done", "status_reason": "identity not confirmed", "language": self.call.language,
            "text": "Post-discharge follow-up call stopped: the birth date did not match twice."})
        task = self.run.tool("createCallTask", {
            "code": "followup-manual", "performer_role": "nurse", "priority": "routine",
            "encounter_id": self.call.encounter_id, "call_id": self.call.id,
            "description": "Follow up with the patient by phone yourself: the automated follow-up call could not "
                           "confirm who answered."})
        self.call.task_id = (task.output or {}).get("task_id")
        self.call.review_status, self.call.priority = "open", "routine"
        return self.say("dob_failed")

    # ----- escalation -----

    def _escalate(self, hits: list[redflags.Hit] | None = None, *, by: str = "rules", priority: str | None = None,
                  reason: str = "") -> None:
        emergency = any(h.emergency for h in hits or [])
        priority = priority or ("stat" if emergency else "urgent")
        call = self.call
        stronger = PRIORITY_RANK[priority] < PRIORITY_RANK[call.priority if call.escalated else None]
        call.escalated, call.emergency = True, call.emergency or emergency
        call.escalated_by = call.escalated_by or by
        if not stronger:
            return
        call.priority, call.review_status = priority, "open"
        labels = ", ".join(h.label for h in hits or []) or reason
        words = "; ".join(f'"{h.quote}"' for h in hits or [])
        minutes = settings.followup_callback_minutes
        description = (f"POSSIBLE EMERGENCY on the follow-up call: {labels}. The patient was told to call 911. Call "
                       f"them now." if emergency else
                       f"Follow-up call escalation ({by}): {labels}. Call the patient back within {minutes} minutes.")
        if words:
            description += f" The patient said: {words}"
        task = self.run.tool("createCallTask", {
            "code": "followup-escalation", "performer_role": "nurse", "priority": priority,
            "encounter_id": call.encounter_id, "call_id": call.id, "description": description[:1000],
            **({"focus": f"Communication/{call.communication_id}"} if call.communication_id else {})})
        if task.ok:
            call.task_id = task.output["task_id"]
        bus.publish(platform_event(
            "followup.escalated", at=hospital_now(self.store), actor=f"agent:{self.run.spec.agent_id}",
            refs={"patient": f"Patient/{call.patient_id}", "encounter": f"Encounter/{call.encounter_id}",
                  **({"task": f"Task/{call.task_id}"} if call.task_id else {})},
            key=f"followup-escalated|{call.id}|{priority}",
            attrs={"priority": priority, "by": by, "codes": ",".join(h.code for h in hits or [])}))

    # ----- the end of the call -----

    def _transcript(self) -> str:
        lines = []
        for q in self.call.script[1:]:
            given = self.call.answers.get(q["key"])
            if given:
                lines.append(f"Q [{q['key']}]: {q['text'].get('en', '')}\nA: {given['text']}")
        return "\n".join(lines)

    def _classify(self) -> None:
        call = self.call
        rule_hits = ", ".join(sorted({h["code"] for h in call.rule_hits})) or "none"
        flags = ", ".join(q["code"] for q in call.script if q["kind"] == "flag") or "none"
        if self.run.spec.kind == "module":
            outcome, seen = None, ""
            call.classifier = {"status": "not_used"}
        else:
            outcome, seen = followup_agent.classify(self.run, self._transcript(), flags, rule_hits)
            call.classifier = {"status": outcome.status if outcome else "refused", "seen_by_model": seen}
        if outcome is not None and outcome.status == "ok":
            data = followup_agent.ClassifierOutput.model_validate(outcome.data)
            call.classifier.update(data.model_dump(), evaluated=outcome.evaluated)
            if data.escalate:
                self._escalate(by="model", priority="urgent", reason=data.reason)
            elif data.confidence < CONFIDENCE_FLOOR:
                self._escalate(by="low_confidence", priority="routine",
                               reason=f"the classifier was unsure ({data.confidence:.2f} < {CONFIDENCE_FLOOR})")
            return
        if not call.escalated:  # no model: a nurse reads the call (recall first)
            self._escalate(by="model_unavailable", priority="routine", reason="the AI classifier was unavailable; "
                                                                               "a nurse reviews the call")

    def _summary(self) -> str:
        call = self.call
        data = call.classifier or {}
        if data.get("summary"):
            text = data["summary"]
        else:
            concerns = [f"{q['key']}: {a['text']}" for q in call.script if (a := call.answers.get(q["key"]))
                        and q["kind"] != "identity" and (a["answer"] == "yes" and q["kind"] == "flag" or
                                                         a["answer"] == "no" and q["kind"] == "med")]
            text = ("Answers needing attention: " + "; ".join(concerns)) if concerns else \
                f"{len(call.answers)} answers recorded; see the call for details."
        hits = ", ".join(h["label"] for h in call.rule_hits)
        if call.escalated:
            text += f" Escalated to a nurse ({call.priority}; {call.escalated_by}" + (f": {hits}" if hits else "") + \
                (f"; {data.get('reason')}" if data.get("reason") and call.escalated_by != "rules" else "") + ")."
        return text

    def _finish(self) -> str:
        call = self.call
        self.ended = True
        self._classify()
        call.summary = self._summary()
        observation_ids = []
        reported = {h["code"] for h in call.rule_hits}
        for h in call.rule_hits:
            obs = self.run.tool("recordPatientReportedObservation", {
                "encounter_id": call.encounter_id, "code": h["code"], "display": h["label"][:120],
                "text": h["quote"] or h["label"], "red_flag": True,
                **({"temperature_c": h["temperature_c"]} if h.get("temperature_c") else {}),
                **({"communication_id": call.communication_id} if call.communication_id else {})})
            if obs.ok:
                observation_ids.append(obs.output["observation_id"])
        for s in (call.classifier or {}).get("symptoms") or []:
            code = s.get("code") if s.get("code") in followup_agent.SYMPTOM_CODES else "other"
            if code in reported or code == "question_for_nurse":
                continue
            reported.add(code)
            obs = self.run.tool("recordPatientReportedObservation", {
                "encounter_id": call.encounter_id, "code": code, "display": code.replace("_", " ").capitalize(),
                "text": (s.get("text") or code)[:1000], "red_flag": False,
                **({"communication_id": call.communication_id} if call.communication_id else {})})
            if obs.ok:
                observation_ids.append(obs.output["observation_id"])
        call.observation_ids = observation_ids
        about = [f"Observation/{o}" for o in observation_ids] + ([f"Task/{call.task_id}"] if call.task_id else [])
        self.run.tool("writeCallRecord", {
            "patient_id": call.patient_id, "encounter_id": call.encounter_id,
            "communication_id": call.communication_id, "kind": "followup-call", "direction": "outbound",
            "status": "completed", "language": call.language, "text": call.summary[:8000],
            "escalated": call.escalated, "priority": call.priority or "routine",
            **({"about": about} if about else {})})
        call.status, call.ended_at = "completed", datetime.now()
        call.review_status = "open" if call.escalated else "none"
        first = self.first_name()
        if call.escalated and call.priority in ("stat", "urgent"):
            return self.say("close_escalated", first=first, minutes=settings.followup_callback_minutes)
        if call.escalated:
            return self.say("close_review", first=first)
        return self.say("close_ok", first=first)
