"""Tool handlers: the domain bindings behind config/tools/*.yaml (spec 6.4).

The Tool Gateway calls `handler(ctx, **args)` after every check has passed. A
handler uses the domain services only through `ctx`: `ctx.fhir` is a FhirGateway
opened for the actor the gateway chose (the user the agent acts for, the agent
itself, or for privileged tools the approver) with the agent as purpose module,
so the EHR's own allow-list, role policy, unit scope and audit apply underneath.
Resources an agent writes are stamped with the run (`ctx.stamp`). A handler
returns the tool's output; `_refs` lists the FHIR resources it wrote or read.

Errors: FhirAccessDenied (refused), FhirConflict, LookupError, ValueError
(failed, not retried); ToolUnavailable and network errors are retried by the gateway.
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from app.core.store import get_store
from app.ehr import access
from app.ehr.clock import hospital_now
from app.ehr.codes import DOC_TYPES, EXT, FLAG_CODE, LOCAL, MRN_SYSTEM, PRACTITIONER_ROLE, TASK_CODE, concept
from app.fhir.dt import fhir_datetime, parse, ref

if TYPE_CHECKING:
    from app.agents.gateway import ToolContext

BUNDLE_TYPES = ("Condition", "Procedure", "MedicationRequest", "Observation", "DiagnosticReport", "ServiceRequest",
                "Task", "CarePlan", "DocumentReference", "Flag")
MEDIUM = {"phone": ("PHONE", "Telephone"), "sms": ("SMSWRIT", "SMS"), "portal": ("EMAILWRIT", "Patient portal"),
          "letter": ("MAILWRIT", "Letter"), "in-person": ("VIDEOCONF", "In person")}
PARTICIPATION_MODE = "http://terminology.hl7.org/CodeSystem/v3-ParticipationMode"


def _now() -> datetime:
    return hospital_now(get_store())


def _plain(models) -> list[dict]:
    return [m.to_fhir() for m in models]


def _ref(resource: dict) -> str:
    return f"{resource['resourceType']}/{resource['id']}"


def _read_out(resources: list[dict]) -> dict:
    return {"resources": resources, "_refs": [_ref(r) for r in resources]}


def _encounter(ctx: ToolContext, encounter_id: str) -> dict:
    found = ctx.fhir.read("Encounter", encounter_id)
    if found is None:
        raise LookupError(f"Encounter/{encounter_id} not found")
    return found.to_fhir()


# ---------- read ----------


def get_encounter_context(ctx: ToolContext, encounter_id: str) -> dict:
    encounter = _encounter(ctx, encounter_id)
    out = [encounter]
    patient_id = access.patient_of(encounter)
    if patient_id and ctx.readable("Patient"):
        patient = ctx.fhir.read("Patient", patient_id)
        if patient is not None:
            out.append(patient.to_fhir())
    if ctx.readable("Flag"):
        out += [f for f in _plain(ctx.fhir.get_encounter_resources("Flag", encounter_id))
                if f.get("status") == "active"]
    return _read_out(out)


def get_encounter_bundle(ctx: ToolContext, encounter_id: str) -> dict:
    out = get_encounter_context(ctx, encounter_id)["resources"]
    out = [r for r in out if r["resourceType"] != "Flag"]
    for rtype in BUNDLE_TYPES:
        if ctx.readable(rtype):
            out += _plain(ctx.fhir.get_encounter_resources(rtype, encounter_id))
    return _read_out(out)


def get_bed_board(ctx: ToolContext, unit_id: str) -> dict:
    board = ctx.fhir.get_bed_board(unit_id)
    return {"board": board.model_dump(mode="json"), "_refs": [f"Location/{unit_id}"]}


def get_orders(ctx: ToolContext, encounter_id: str) -> dict:
    return _read_out(_plain(ctx.fhir.get_orders(encounter_id)))


def get_medications(ctx: ToolContext, encounter_id: str) -> dict:
    return _read_out(_plain(ctx.fhir.get_medications(encounter_id)))


def get_home_meds(ctx: ToolContext, encounter_id: str) -> dict:
    patient_id = access.patient_of(_encounter(ctx, encounter_id))
    patient = ctx.fhir.read("Patient", patient_id) if patient_id else None
    mrn = next((i.value for i in (patient.identifier or []) if i.system == MRN_SYSTEM), None) if patient else None
    return _read_out(_plain(ctx.fhir.get_home_meds(mrn)) if mrn else [])


def get_observations(ctx: ToolContext, encounter_id: str, codes: list[str] | None = None,
                     since: str | None = None) -> dict:
    return _read_out(_plain(ctx.fhir.get_observations(encounter_id, codes, parse(since) if since else None)))


def get_documents(ctx: ToolContext, encounter_id: str) -> dict:
    return _read_out(_plain(ctx.fhir.get_documents(encounter_id)))


# ---------- recommend ----------


def _patient_of(ctx: ToolContext, encounter_id: str) -> str:
    patient = ctx.target.patient_id or access.patient_of(_encounter(ctx, encounter_id))
    if patient is None:
        raise LookupError(f"Encounter/{encounter_id} has no patient")
    return patient


def draft_document(ctx: ToolContext, encounter_id: str, doc_type: str, title: str, text: str,
                   source_refs: list[str] | None = None) -> dict:
    system, code, display = DOC_TYPES[doc_type]
    doc = {"resourceType": "DocumentReference", "status": "current", "docStatus": "preliminary",
           "type": concept(system, code, display), "subject": ref("Patient", _patient_of(ctx, encounter_id)),
           "date": fhir_datetime(_now()), "description": title,
           "author": [{"display": f"AI draft by {ctx.agent.label}"}],
           "content": [{"attachment": {"contentType": "text/plain", "language": "en", "title": title,
                                       "data": base64.b64encode(text.encode()).decode()}}],
           "context": {"encounter": [ref("Encounter", encounter_id)],
                       "related": [{"reference": r} for r in source_refs or []]}}
    stored = ctx.fhir.create(ctx.stamp(doc)).to_fhir()
    return {"document_id": stored["id"], "doc_status": stored["docStatus"], "_refs": [_ref(stored)]}


def submit_for_review(ctx: ToolContext, encounter_id: str, reviewer_role: str, summary: str, recommendation: str,
                      evidence_refs: list[str] | None = None, priority: str = "routine") -> dict:
    from app.agents.approvals import REVIEW

    now = fhir_datetime(_now())
    task = {"resourceType": "Task", "status": "requested", "intent": "proposal", "priority": priority,
            "code": concept(TASK_CODE, REVIEW, "Review an AI recommendation"), "description": summary,
            "for": ref("Patient", _patient_of(ctx, encounter_id)), "encounter": ref("Encounter", encounter_id),
            "authoredOn": now, "lastModified": now,
            "performerType": [concept(PRACTITIONER_ROLE, access.ROLE_CODE[reviewer_role])],
            "input": [{"type": {"text": "recommendation"}, "valueString": recommendation},
                      *([{"type": {"text": "evidence"}, "valueString": ", ".join(evidence_refs)}]
                        if evidence_refs else [])]}
    stored = ctx.fhir.create(ctx.stamp(task)).to_fhir()
    return {"task_id": stored["id"], "status": stored["status"], "_refs": [_ref(stored)]}


# ---------- action ----------


def create_task(ctx: ToolContext, encounter_id: str, code: str, description: str, performer_role: str,
                priority: str = "routine") -> dict:
    now = fhir_datetime(_now())
    task = {"resourceType": "Task", "status": "requested", "intent": "order", "priority": priority,
            "code": concept(TASK_CODE, code), "description": description,
            "for": ref("Patient", _patient_of(ctx, encounter_id)), "encounter": ref("Encounter", encounter_id),
            "authoredOn": now, "lastModified": now,
            "performerType": [concept(PRACTITIONER_ROLE, access.ROLE_CODE[performer_role])]}
    stored = ctx.fhir.create(ctx.stamp(task)).to_fhir()
    return {"task_id": stored["id"], "status": stored["status"], "_refs": [_ref(stored)]}


def update_task(ctx: ToolContext, task_id: str, status: str, note: str | None = None) -> dict:
    found = ctx.fhir.read("Task", task_id)
    if found is None:
        raise LookupError(f"Task/{task_id} not found")
    task = found.to_fhir()
    task["status"] = status
    task["lastModified"] = fhir_datetime(_now())
    if note:
        task["note"] = [*(task.get("note") or []), {"text": note}]
    stored = ctx.fhir.update(ctx.stamp(task)).to_fhir()
    return {"task_id": stored["id"], "status": stored["status"], "_refs": [_ref(stored)]}


def propose_appointment(ctx: ToolContext, encounter_id: str, service: str, start: str, minutes: int,
                        location_id: str | None = None, description: str | None = None) -> dict:
    begin = parse(start)
    if begin is None:
        raise ValueError(f"start {start!r} is not a date and time")
    participants = [{"actor": ref("Patient", _patient_of(ctx, encounter_id)), "status": "needs-action"}]
    if location_id:
        participants.append({"actor": ref("Location", location_id), "status": "needs-action"})
    appt = {"resourceType": "Appointment", "status": "proposed", "serviceType": [concept(LOCAL, service)],
            "start": fhir_datetime(begin), "end": fhir_datetime(begin + timedelta(minutes=minutes)),
            "minutesDuration": minutes, "participant": participants,
            "extension": [{"url": EXT + "encounter", "valueReference": ref("Encounter", encounter_id)}]}
    if description:
        appt["description"] = description
    stored = ctx.fhir.create(ctx.stamp(appt)).to_fhir()
    return {"appointment_id": stored["id"], "status": stored["status"], "_refs": [_ref(stored)]}


def create_flag(ctx: ToolContext, encounter_id: str, code: str, display: str) -> dict:
    flag = {"resourceType": "Flag", "status": "active", "code": concept(FLAG_CODE, code, display),
            "subject": ref("Patient", _patient_of(ctx, encounter_id)), "encounter": ref("Encounter", encounter_id),
            "period": {"start": fhir_datetime(_now())}}
    stored = ctx.fhir.create(ctx.stamp(flag)).to_fhir()
    return {"flag_id": stored["id"], "status": stored["status"], "_refs": [_ref(stored)]}


def update_flag(ctx: ToolContext, flag_id: str, status: str) -> dict:
    found = ctx.fhir.read("Flag", flag_id)
    if found is None:
        raise LookupError(f"Flag/{flag_id} not found")
    flag = found.to_fhir()
    flag["status"] = status
    if status != "active":
        flag["period"] = {**(flag.get("period") or {}), "end": fhir_datetime(_now())}
    stored = ctx.fhir.update(ctx.stamp(flag)).to_fhir()
    return {"flag_id": stored["id"], "status": stored["status"], "_refs": [_ref(stored)]}


def write_communication(ctx: ToolContext, encounter_id: str, direction: str, medium: str, text: str,
                        status: str) -> dict:
    patient = ref("Patient", _patient_of(ctx, encounter_id))
    code, display = MEDIUM[medium]
    comm = {"resourceType": "Communication", "status": status, "subject": patient,
            "encounter": ref("Encounter", encounter_id), "medium": [concept(PARTICIPATION_MODE, code, display)],
            "payload": [{"contentString": text}],
            "category": [concept(LOCAL, "patient-message" if direction == "inbound" else "message-to-patient")]}
    if direction == "inbound":
        comm["sender"] = patient
        comm["received"] = fhir_datetime(_now())
    else:
        comm["recipient"] = [patient]
        if status == "completed":
            comm["sent"] = fhir_datetime(_now())
    stored = ctx.fhir.create(ctx.stamp(comm)).to_fhir()
    return {"communication_id": stored["id"], "status": stored["status"], "_refs": [_ref(stored)]}


def record_consent(ctx: ToolContext, patient_id: str, category: str, permit: bool) -> dict:
    from app.ehr.consent import change_consent

    out = change_consent(ctx.fhir, patient_id, category, permit)
    event = out["event"]
    return {"consent_id": out["consent"]["id"], "previous": out["previous"], "current": out["current"],
            "event_id": event.event_id if event else None, "_refs": [f"Consent/{out['consent']['id']}"]}


# ---------- privileged (ctx.fhir acts as the approver) ----------


def sign_document_final(ctx: ToolContext, document_id: str) -> dict:
    result = ctx.fhir.sign_document(document_id)
    return {"document_id": result.document_id, "doc_status": result.doc_status, "signed_roles": result.signed_roles,
            "missing_roles": result.missing_roles, "_refs": [f"DocumentReference/{document_id}"]}


def book_appointment(ctx: ToolContext, appointment_id: str) -> dict:
    from app.ehr.events import bus, platform_event

    booked = ctx.fhir.book_appointment(appointment_id).to_fhir()
    refs = {"appointment": f"Appointment/{appointment_id}"}
    if patient := access.patient_of(booked):
        refs["patient"] = f"Patient/{patient}"
    if encounter := access.encounter_of(booked) or ctx.target.encounter_id:
        refs["encounter"] = f"Encounter/{encounter}"
    event = bus.publish(platform_event("appointment.scheduled", at=_now(), actor=f"user:{ctx.actor.id}", refs=refs,
                                       attrs={"status": "booked", "source": ctx.agent.agent_id}))
    return {"appointment_id": appointment_id, "status": booked["status"], "event_id": event.event_id,
            "_refs": [f"Appointment/{appointment_id}"]}


def append_encounter_location(ctx: ToolContext, encounter_id: str, bed_id: str) -> dict:
    ctx.fhir.append_encounter_location(encounter_id, bed_id)
    return {"encounter_id": encounter_id, "bed_id": bed_id, "_refs": [f"Encounter/{encounter_id}"]}


def send_patient_sms(ctx: ToolContext, encounter_id: str, body: str) -> dict:
    from app.ehr.consent import send_sms

    outcome = send_sms(ctx.fhir, _patient_of(ctx, encounter_id), body, encounter_id=encounter_id)
    refs = [f"Communication/{outcome.communication_id}"] + ([f"Task/{outcome.task_id}"] if outcome.task_id else [])
    return {"communication_id": outcome.communication_id, "sent": outcome.sent, "reason": outcome.reason,
            "task_id": outcome.task_id, "_refs": refs}


# ---------- runtime ----------


def record_provenance(ctx: ToolContext, provenance: dict) -> dict:
    stored = ctx.fhir.record_provenance(provenance, replace=True).to_fhir()
    return {"provenance_id": stored["id"], "_refs": [_ref(stored)]}
