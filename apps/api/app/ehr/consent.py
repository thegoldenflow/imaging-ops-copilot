"""Consent management (spec 6.3).

Three consent categories live as FHIR Consent resources (generator: some patients
have none on purpose): `ai_processing`, `followup_call`, `sms`. A category is
`permit`, `deny` or `missing`; only `permit` allows the use. Missing or denied
consent makes a module degrade, never fail:

- no `followup_call`: the follow-up service (7.4) does not call; a nurse gets a
  manual follow-up Task instead (`plan_followup_call`);
- no `ai_processing`: the documentation modules (7.3) offer the template only and
  generate no AI draft (`documentation_mode`);
- no `sms`: the message is not sent; it is recorded as a Communication with status
  not-done and the registration desk gets a Task to reach the patient another way
  (`send_sms`).

`change_consent` records a decision through FhirGateway (audit `consent_change`)
and publishes `consent.revoked` (permit -> deny) or `consent.granted` on the
domain event bus, so subscribers stop or resume what depended on it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.core.registry import CONSENT_CATEGORIES
from app.core.store import get_store
from app.ehr.clock import hospital_now
from app.ehr.codes import PRACTITIONER_ROLE, TASK_CODE, concept
from app.ehr.events import bus, platform_event
from app.fhir.dt import fhir_datetime, ref

ConsentState = Literal["permit", "deny", "missing"]


def _category(consent: dict) -> str | None:
    return next((c.get("code") for cat in consent.get("category") or [] for c in cat.get("coding") or []), None)


def consent_status(fhir, patient_id: str) -> dict[str, ConsentState]:
    """The patient's current decision per category (the latest Consent of each)."""
    latest: dict[str, dict] = {}
    for consent in fhir.get_consents(patient_id):
        category = _category(consent)
        if category in CONSENT_CATEGORIES and consent.get("status") == "active" and \
                (category not in latest or (consent.get("dateTime") or "") > (latest[category].get("dateTime") or "")):
            latest[category] = consent
    return {c: ((latest[c].get("provision") or {}).get("type", "deny") if c in latest else "missing")
            for c in CONSENT_CATEGORIES}


def permits(fhir, patient_id: str, category: str) -> bool:
    return consent_status(fhir, patient_id)[category] == "permit"


def change_consent(fhir, patient_id: str, category: str, permit: bool, *, at: datetime | None = None) -> dict:
    """Record the patient's decision and tell subscribers; returns {consent, previous, current, event}."""
    at = at or hospital_now(get_store())
    consent, previous = fhir.record_consent(patient_id, category, permit, at=at)
    current = "permit" if permit else "deny"
    event = None
    if previous != current:
        event_type = "consent.granted" if permit else "consent.revoked"
        event = bus.publish(platform_event(
            event_type, at=at, actor=f"{fhir.actor.kind}:{fhir.actor.id}" if fhir.actor.kind != "system"
            else fhir.actor.id, refs={"patient": f"Patient/{patient_id}", "consent": f"Consent/{consent['id']}"},
            attrs={"category": category, "previous": previous}))
    return {"consent": consent, "previous": previous, "current": current, "event": event}


# ---------- degradations ----------


def _task(patient_id: str, encounter_id: str | None, code: tuple[str, str], role_code: str, description: str,
          at: datetime) -> dict:
    task = {"resourceType": "Task", "status": "requested", "intent": "order", "priority": "routine",
            "code": concept(TASK_CODE, *code), "description": description, "for": ref("Patient", patient_id),
            "authoredOn": fhir_datetime(at), "performerType": [concept(PRACTITIONER_ROLE, role_code)]}
    if encounter_id:
        task["encounter"] = ref("Encounter", encounter_id)
    return task


@dataclass
class FollowupPlan:
    mode: Literal["call", "manual_task"]
    reason: str
    task_id: str | None = None


def plan_followup_call(fhir, patient_id: str, encounter_id: str | None = None, *,
                       at: datetime | None = None) -> FollowupPlan:
    """7.4: call the patient only with followup_call consent; otherwise a nurse follows up by hand."""
    state = consent_status(fhir, patient_id)["followup_call"]
    if state == "permit":
        return FollowupPlan("call", "follow-up call consent on file")
    at = at or hospital_now(get_store())
    why = "no follow-up call consent on file" if state == "missing" else "the patient declined automated follow-up calls"
    task = fhir.create(_task(patient_id, encounter_id, ("followup-manual", "Manual follow-up"), "nurse",
                             f"Follow up with the patient by hand: {why}.", at))
    return FollowupPlan("manual_task", why, task.id)


@dataclass
class DocumentationMode:
    mode: Literal["draft", "template_only"]
    reason: str


def documentation_mode(fhir, patient_id: str) -> DocumentationMode:
    """7.3: an AI draft only with ai_processing consent; otherwise the empty template for a person to fill."""
    state = consent_status(fhir, patient_id)["ai_processing"]
    if state == "permit":
        return DocumentationMode("draft", "AI processing consent on file")
    why = "no AI processing consent on file" if state == "missing" else "the patient declined AI processing"
    return DocumentationMode("template_only", why)


@dataclass
class SmsOutcome:
    sent: bool
    communication_id: str
    reason: str
    task_id: str | None = None


def send_sms(fhir, patient_id: str, body: str, *, encounter_id: str | None = None,
             at: datetime | None = None) -> SmsOutcome:
    """Queue an SMS only with sms consent; otherwise record it as not sent and ask the desk to call instead."""
    at = at or hospital_now(get_store())
    state = consent_status(fhir, patient_id)["sms"]
    comm = {"resourceType": "Communication", "subject": ref("Patient", patient_id), "recipient": [ref("Patient", patient_id)],
            "medium": [concept("http://terminology.hl7.org/CodeSystem/v3-ParticipationMode", "SMSWRIT", "SMS")],
            "payload": [{"contentString": body}]}
    if encounter_id:
        comm["encounter"] = ref("Encounter", encounter_id)
    if state == "permit":
        stored = fhir.create({**comm, "status": "preparation"})  # queued for the messaging adapter
        return SmsOutcome(True, stored.id, "SMS consent on file")
    why = "no SMS consent on file" if state == "missing" else "the patient declined SMS"
    stored = fhir.create({**comm, "status": "not-done", "statusReason": {"text": why}})
    task = fhir.create(_task(patient_id, encounter_id, ("contact-patient", "Contact the patient"), "clerk",
                             f"A message could not be sent by SMS ({why}); reach the patient by phone or letter.", at))
    return SmsOutcome(False, stored.id, why, task.id)
