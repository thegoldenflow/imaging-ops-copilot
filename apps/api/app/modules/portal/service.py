"""System 17 · Referring Physician Portal.

A referring physician signs in separately and sees only their own patients:
those with an appointment, requisition, waitlist entry or study that names them
as the referrer. Anything else is refused (403) and audited. Requisitions
written in the portal (structured form plus free text) enter the phase 2
pipeline exactly like faxed ones."""

import random
from datetime import date, datetime

from pydantic import BaseModel, Field

from app.core.models import ACTIVE_STATUSES, Patient, Requisition
from app.core.store import Store
from app.modules.protocols.library import BY_ID as PROTOCOLS
from app.modules.reports import service as reports
from app.modules.requisitions import service as requisitions
from app.modules.triage import service as triage

# Exam choices offered on the form (free text is allowed too).
EXAM_CHOICES = [
    "CT chest with contrast", "CT chest without contrast", "Low-dose CT chest", "CT abdomen and pelvis with contrast",
    "CT head without contrast", "CT renal stone (KUB)", "MRI brain", "MRI brain with and without contrast",
    "MRI lumbar spine", "MRI knee", "Ultrasound abdomen", "Ultrasound pelvis", "Ultrasound thyroid",
    "Venous Doppler lower limb", "Chest X-ray", "Knee X-ray", "Lumbar spine X-ray",
]

STATUS_TEXT = {
    "received": "Received, being processed",
    "processing": "Received, being processed",
    "ready": "With the radiologist for review",
    "approved": "Approved, waiting to be scheduled",
    "waitlisted": "Approved, on the waitlist",
    "booked": "Booked",
    "failed": "Received; our staff will process it manually",
}


def patient_ids(store: Store, referrer_id: str) -> set[str]:
    ids = {a.patient_id for a in list(store.appointments.values()) if a.referrer_id == referrer_id}
    ids |= {r.patient_id for r in list(store.requisitions.values()) if r.referrer_id == referrer_id}
    ids |= {w.patient_id for w in list(store.waitlist.values()) if w.referrer_id == referrer_id}
    ids |= {s.patient_id for s in list(store.studies.values()) if s.referrer_id == referrer_id}
    return ids


def requisition_view(store: Store, req: Requisition) -> dict:
    ext = requisitions.extractions(store).get(req.id)
    tri = triage.records(store).get(req.id)
    appt = store.appointments.get(req.appointment_id) if req.appointment_id else None
    status = STATUS_TEXT.get(req.status, req.status)
    if appt:
        status = f"Booked for {appt.start:%a %b %d, %H:%M} at {store.sites[appt.site_id].name}"
    return {
        "id": req.id, "patient_id": req.patient_id, "patient_name": store.patients[req.patient_id].full_name,
        "received_at": req.received_at.isoformat(timespec="minutes"), "channel": req.channel, "status": req.status,
        "status_text": status, "requested_exam": ext.fields["requested_exam"]["value"] if ext else None,
        # Only a priority a radiologist has confirmed is shown to the referrer, never the AI suggestion.
        "confirmed_priority": tri.final_priority if tri and tri.review_action else None,
    }


def patient_detail(store: Store, referrer_id: str, pid: str) -> dict:
    p = store.patients[pid]
    appts = sorted((a for a in list(store.appointments.values()) if a.patient_id == pid and a.referrer_id == referrer_id),
                   key=lambda a: a.start, reverse=True)
    reqs = sorted((r for r in list(store.requisitions.values()) if r.patient_id == pid and r.referrer_id == referrer_id),
                  key=lambda r: r.received_at, reverse=True)
    signed = [r for r in list(reports.reports(store).values())
              if r.patient_id == pid and r.referrer_id == referrer_id and r.status == "signed"]
    return {
        "id": p.id, "name": p.full_name, "dob": p.dob.isoformat(), "sex": p.sex, "phone": p.phone,
        "preferred_language": p.preferred_language, "health_card_last4": p.health_card[-4:],
        "appointments": [{
            "id": a.id, "start": a.start.isoformat(timespec="minutes"), "exam_name": store.exams[a.exam_code].name,
            "site_name": store.sites[a.site_id].name, "status": a.status,
            "protocol_name": PROTOCOLS[a.protocol_id].name if a.protocol_id in PROTOCOLS else None,
        } for a in appts],
        "requisitions": [requisition_view(store, r) for r in reqs],
        "reports": [{"id": r.id, "signed_at": r.signed_at.isoformat(timespec="minutes") if r.signed_at else None,
                     "exam_name": store.exams[store.studies[r.study_id].exam_code].name,
                     "impression": next((s.final_text for s in r.sections if s.key == "impression"), "")}
                    for r in signed],
    }


class NewPatient(BaseModel):
    given_name: str = Field(min_length=1)
    family_name: str = Field(min_length=1)
    dob: date
    sex: str = "F"
    phone: str = ""
    health_card: str = Field(pattern=r"^\d{10}$")
    health_card_version: str = ""
    preferred_language: str = "en"


class PortalRequisition(BaseModel):
    patient_id: str | None = None
    new_patient: NewPatient | None = None
    exam_requested: str = Field(min_length=3)
    clinical_information: str = Field(min_length=3)
    relevant_history: str = ""
    allergies: str = ""
    previous_imaging: str = ""
    urgent: bool = False
    notes: str = ""


def find_or_create_patient(store: Store, data: NewPatient) -> Patient:
    existing = next((p for p in store.patients.values() if p.health_card == data.health_card), None)
    if existing:
        return existing
    pid = store.next_id("PT-P")
    patient = Patient(
        id=pid, given_name=data.given_name.strip(), family_name=data.family_name.strip(), dob=data.dob, sex=data.sex,
        phone=data.phone or "+1-416-555-0100", email=f"{pid.lower()}@example.com", address="Not provided",
        health_card=data.health_card, health_card_version=data.health_card_version.upper()[:2],
        preferred_language=data.preferred_language if data.preferred_language in ("en", "fr", "zh", "pa") else "en",
    )
    store.patients[pid] = patient
    return patient


def compose_text(patient: Patient, referrer, body: PortalRequisition) -> str:
    """The same requisition layout the fax channel uses, so extraction treats both alike."""
    lines = [
        "REQUISITION FOR DIAGNOSTIC IMAGING (submitted through the referrer portal)",
        f"Patient: {patient.full_name}    DOB: {patient.dob:%Y-%m-%d}    Health card: {patient.health_card} {patient.health_card_version}",
        f"Phone: {patient.phone}",
        f"Exam requested: {body.exam_requested.strip()}",
        f"Clinical information: {body.clinical_information.strip()}",
        f"Relevant history: {body.relevant_history.strip() or 'None'}",
        f"Allergies: {body.allergies.strip() or 'NKDA'}",
        f"Previous imaging: {body.previous_imaging.strip() or 'None known'}",
    ]
    if body.urgent:
        lines.append("Priority: URGENT - please expedite")
    if body.notes.strip():
        lines.append(f"Additional notes: {body.notes.strip()}")
    lines.append(f"Referring physician: {referrer.name}, {referrer.clinic}. Fax {referrer.fax}")
    return "\n".join(lines)


def seed(s: Store, rng: random.Random, now: datetime) -> None:
    """Dr. Park (the demo referrer) gets a handful of patients with upcoming exams."""
    upcoming = [a for a in s.appointments.values()
                if a.status in ACTIVE_STATUSES and a.start > now and not a.patient_id.startswith("PT-DEMO")
                and a.id != "AP-DEMO2" and a.requisition_id is None]
    upcoming.sort(key=lambda a: a.id)
    for appt in rng.sample(upcoming, 6):
        appt.referrer_id = "R-DEMO"
