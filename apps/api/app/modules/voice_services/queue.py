"""The nurse follow-up queue (spec 7.4).

Every follow-up call that needs a nurse: escalations (a red flag: urgent, an emergency: stat; the classifier:
urgent; unsure or no model: routine) and manual follow-ups (a missing condition, no answer, identity not
confirmed). Sorted by priority (stat first), then by how long it has waited. Each item shows the patient's MRN, the
discharge date, the call summary, the red flags and the patient's own words (the model saw them de-identified;
they are shown restored). A nurse sees the queue of their units (the unit the patient was discharged from) and
reads the patient through FhirGateway as themself, so the 72-hour unit scope of WP4 applies: an item whose stay
left that window stays in the list with its priority but without the patient's details (`restricted`); opening it
needs emergency access (break-glass) in the chart.

Actions, each written as a Communication through the Tool Gateway (module `voice_services`, on the nurse's
authority): a call-back note (the item stays open), close (the nurse Task completed: the escalation is handled),
refer to a physician (a physician Task; the nurse Task completed as referred).
"""

from __future__ import annotations

from datetime import datetime

from app.agents.runtime import runtime
from app.core.models import StaffUser
from app.core.store import Store
from app.ehr.clock import hospital_now
from app.ehr.codes import MRN_SYSTEM
from app.ehr.events import bus, platform_event
from app.ehr.gateway import Actor, FhirAccessDenied, FhirGateway
from app.modules.voice_services import followup
from app.modules.voice_services.callcenter import MODULE
from app.modules.voice_services.models import FollowupCall
from app.modules.voice_services.redflags import RULES

PRIORITY = {"stat": 0, "urgent": 1, "asap": 2, "routine": 3}
ROLES = ("nurse", "physician")
RESTRICTED_KEYS = ("id", "encounter_id", "patient_id", "unit_id", "status", "review_status", "priority", "escalated",
                   "emergency", "escalated_by", "missing", "language", "discharged_at", "due_at", "source",
                   "workflow_id", "communication_id", "started_at", "ended_at", "updated_at", "questions")


class QueueError(ValueError):
    pass


def visible(user: StaffUser, call: FollowupCall) -> bool:
    """Unit-scoped staff see their units' follow-ups (the unit the patient left)."""
    return str(user.role) in ROLES and (call.unit_id in user.unit_ids if user.unit_ids else False)


def _patient(user: StaffUser, call: FollowupCall) -> dict:
    found = FhirGateway(Actor.of(user), MODULE).read("Patient", call.patient_id)
    return found.to_fhir() if found is not None else {}


def _name(patient: dict) -> str:
    names = patient.get("name") or []
    official = next((n for n in names if n.get("use") == "official"), names[0] if names else {})
    usual = next((n.get("text") for n in names if n.get("use") == "usual"), None)
    return " ".join(x for x in (official.get("text"), f"({usual})" if usual else None) if x)


def _task(call: FollowupCall) -> dict | None:
    if not call.task_id:
        return None
    found = FhirGateway(Actor.system("voice-services"), MODULE).read("Task", call.task_id)
    task = found.to_fhir() if found is not None else None
    return {"id": call.task_id, "status": task.get("status"), "priority": task.get("priority"),
            "description": task.get("description")} if task else None


def view(call: FollowupCall, user: StaffUser | None = None, *, detail: bool = False) -> dict:
    data = call.classifier or {}
    flags = [{"code": h["code"], "label": h["label"], "label_zh": RULES.get(h["code"], ("", h["label"]))[1],
              "emergency": h["emergency"], "how": h["how"], "words": h["quote"], "question": h.get("question"),
              "temperature_c": h.get("temperature_c"), "source": "rules"} for h in call.rule_hits]
    words = [h["quote"] for h in call.rule_hits if h.get("quote")]
    words += [q for q in data.get("quotes") or [] if q not in words]
    out = {"id": call.id, "encounter_id": call.encounter_id, "patient_id": call.patient_id, "unit_id": call.unit_id,
           "status": call.status, "review_status": call.review_status, "priority": call.priority,
           "escalated": call.escalated, "emergency": call.emergency, "escalated_by": call.escalated_by,
           "missing": call.missing, "reason": call.reason, "language": call.language,
           "discharged_at": call.discharged_at.isoformat() if call.discharged_at else None,
           "due_at": call.due_at.isoformat() if call.due_at else None, "source": call.source,
           "workflow_id": call.workflow_id, "summary": call.summary, "red_flags": flags,
           "classifier": {k: data.get(k) for k in ("status", "escalate", "reason", "confidence", "evaluated")}
           if data else None, "patient_words": words[:6], "task": _task(call),
           "communication_id": call.communication_id, "observation_ids": call.observation_ids,
           "nurse_actions": call.nurse_actions, "started_at": call.started_at.isoformat() if call.started_at else None,
           "ended_at": call.ended_at.isoformat() if call.ended_at else None,
           "updated_at": call.updated_at.isoformat(), "questions": len(call.script)}
    if user is not None:
        try:
            patient = _patient(user, call)
        except FhirAccessDenied:
            if detail:
                raise
            return _restricted(out)
        out["mrn"] = next((i.get("value") for i in patient.get("identifier") or [] if i.get("system") == MRN_SYSTEM),
                          None)
        out["patient_name"] = _name(patient)
    if detail:
        out["transcript"] = [t.model_dump(mode="json") for t in call.transcript]
        out["script"] = [{"key": q["key"], "kind": q["kind"], "text": q.get("text")} for q in call.script]
        out["answers"] = call.answers
        out["seen_by_model"] = data.get("seen_by_model")
    return out


def _restricted(out: dict) -> dict:
    """A list item the user may not read the patient of (outside the 72-hour scope): no clinical content."""
    return {**{k: out[k] for k in RESTRICTED_KEYS}, "restricted": True, "mrn": None, "patient_name": None,
            "reason": "Outside your access window (72 hours after discharge): open the chart with emergency access",
            "summary": None, "red_flags": [], "classifier": None, "patient_words": [], "task": None,
            "observation_ids": [], "nurse_actions": []}


def items(store: Store, user: StaffUser, *, which: str = "open") -> list[dict]:
    """which: open (needs a nurse), calls (the calls to place and placed), all."""
    rows = [c for c in followup.table(store).values() if visible(user, c)]
    if which == "open":
        rows = [c for c in rows if c.review_status == "open"]
    elif which == "calls":
        rows = [c for c in rows if c.status in ("scheduled", "in_progress", "completed", "no_answer", "not_confirmed")]
    rows.sort(key=lambda c: (c.review_status != "open", PRIORITY.get(c.priority or "", 9), c.updated_at))
    return [view(c, user) for c in rows]


def summary(store: Store, user: StaffUser) -> dict:
    """For the notification: open escalations by priority (polled by the web app)."""
    rows = [c for c in followup.table(store).values() if visible(user, c) and c.review_status == "open"]
    return {"open": len(rows), "escalated": sum(c.escalated for c in rows),
            "stat": sum(c.priority == "stat" for c in rows), "urgent": sum(c.priority == "urgent" for c in rows),
            "latest": max((c.updated_at.isoformat() for c in rows if c.escalated), default=None)}


def _item(store: Store, user: StaffUser, call_id: str) -> FollowupCall:
    call = followup.get(call_id, store)
    if call is None:
        raise LookupError(f"No follow-up {call_id}")
    if not visible(user, call):
        raise PermissionError("This follow-up belongs to another unit")
    return call


def act(store: Store, user: StaffUser, call_id: str, kind: str, note: str) -> dict:
    """kind: callback (a note; stays open), close, refer (to a physician). Each writes a Communication."""
    if str(user.role) != "nurse":
        raise PermissionError("Nurses handle follow-up escalations")
    call = _item(store, user, call_id)
    if kind in ("close", "refer") and call.review_status != "open":
        raise QueueError(f"This follow-up is {call.review_status}")
    note = " ".join((note or "").split())
    if len(note) < 3:
        raise QueueError("Write a short note")
    comm_kind = {"callback": "nurse-callback", "close": "nurse-close", "refer": "nurse-referral"}[kind]
    text = {"callback": f"Nurse call-back after the follow-up call: {note}",
            "close": f"Follow-up escalation closed by the nurse: {note}",
            "refer": f"Follow-up referred to a physician by the nurse: {note}"}[kind]
    with runtime.start(MODULE, user=user, encounter_id=call.encounter_id,
                       context_id=f"FollowupCall/{call.id}") as run:
        record = run.tool("writeCallRecord", {
            "patient_id": call.patient_id, "encounter_id": call.encounter_id, "kind": comm_kind,
            "direction": "outbound" if kind == "callback" else "inbound", "status": "completed", "text": text[:8000],
            "about": [r for r in (f"Communication/{call.communication_id}" if call.communication_id else None,
                                  f"Task/{call.task_id}" if call.task_id else None) if r]})
        if not record.ok:
            run.finish(outcome=record.status)
            raise PermissionError(f"{record.reason}: {record.detail}")
        action = {"kind": kind, "note": note, "by": user.id, "name": user.name, "role": str(user.role),
                  "at": datetime.now().isoformat(timespec="seconds"),
                  "communication_id": record.output["communication_id"]}
        if kind == "refer":
            referral = run.tool("createCallTask", {
                "code": "followup-referral", "performer_role": "physician",
                "priority": call.priority if call.priority in ("stat", "urgent") else "routine",
                "encounter_id": call.encounter_id, "call_id": call.id,
                "focus": f"Communication/{call.communication_id}" if call.communication_id else
                f"Communication/{record.output['communication_id']}",
                "description": f"Referred by the nurse after the follow-up call: {note}. Call summary: "
                               f"{call.summary or call.reason or ''}"[:1000]})
            action["task_id"] = (referral.output or {}).get("task_id")
        if kind in ("close", "refer") and call.task_id:
            done = run.tool("updateTask", {"task_id": call.task_id, "status": "completed",
                                           "note": f"{'Closed' if kind == 'close' else 'Referred to a physician'} "
                                                   f"by {user.name}: {note}"[:1000]})
            action["task_status"] = (done.output or {}).get("status")
        run.finish(outcome="completed")
    call.nurse_actions.append(action)
    if kind in ("close", "refer"):
        call.review_status = "closed" if kind == "close" else "referred"
        bus.publish(platform_event("followup.handled", at=hospital_now(store), actor=f"user:{user.id}",
                                   refs={"patient": f"Patient/{call.patient_id}",
                                         "encounter": f"Encounter/{call.encounter_id}",
                                         "communication": f"Communication/{record.output['communication_id']}"},
                                   attrs={"decision": kind}))
    followup.save(call, store)
    return view(call, user)
