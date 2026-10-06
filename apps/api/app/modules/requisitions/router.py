"""Requisition intake API: queue, detail, corrections, triage and protocol
decisions, and handing approved requisitions to scheduling."""

import random
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.core.auth import CLINICAL_STAFF, audit_phi, require_roles
from app.core.models import Requisition, Role, StaffUser
from app.core.store import Store, get_store
from app.llm.deid import age_from_dob
from app.modules.contrast import service as contrast
from app.modules.mri_safety import service as mri
from app.modules.prep import service as prep
from app.modules.priors import service as priors
from app.modules.protocols import service as protocols
from app.modules.protocols.library import BY_ID
from app.modules.requisitions import service
from app.modules.requisitions.extraction import FIELD_LABELS, LOW_CONFIDENCE
from app.modules.requisitions.generator import generate
from app.modules.scheduling.router import appointment_view
from app.modules.triage import service as triage

router = APIRouter(prefix="/api/requisitions", tags=["requisitions"])

VIEWERS = require_roles(*CLINICAL_STAFF)
INTAKE = require_roles(Role.FRONT_DESK, Role.OPERATIONS_MANAGER, Role.ADMIN)
RADIOLOGIST = require_roles(Role.RADIOLOGIST)
DIRECTOR = require_roles(Role.MEDICAL_DIRECTOR, Role.ADMIN)
OPEN_STATUSES = {"received", "processing", "ready", "approved", "waitlisted"}


def _get(store: Store, req_id: str) -> Requisition:
    req = store.requisitions.get(req_id)
    if req is None:
        raise HTTPException(404, "Requisition not found")
    return req


def summary(store: Store, req: Requisition) -> dict:
    patient = store.patients[req.patient_id]
    ext = service.extractions(store).get(req.id)
    tri = triage.records(store).get(req.id)
    pro = protocols.records(store).get(req.id)
    protocol = BY_ID.get(pro.effective_id) if pro else None
    row = {
        **req.model_dump(mode="json", exclude={"text"}),
        "patient_name": patient.full_name,
        "patient_language": patient.preferred_language,
        "referrer_name": store.referrers[req.referrer_id].name,
        "requested_exam": ext.fields["requested_exam"]["value"] if ext else None,
        "low_confidence": ext.low_confidence if ext else [],
        "ai_priority": tri.ai_priority if tri else None,
        "priority": tri.final_priority if tri else None,
        "triage_reviewed": bool(tri and tri.review_action),
        "red_flags": tri.ai_red_flags if tri else [],
        "days_left": triage.days_left(store, tri, req.received_at) if tri else None,
        "protocol_id": protocol.id if protocol else None,
        "protocol_name": protocol.name if protocol else None,
        "protocol_approved": bool(pro and pro.approved_id),
        "modality": str(protocol.modality) if protocol else None,
        "contrast_status": None,
        "mri_status": None,
    }
    if protocol and protocol.contrast:
        row["contrast_status"] = contrast.evaluate(store, req.patient_id, req.id).status
    if screening := mri.for_requisition(store, req.id):
        row["mri_status"] = screening.status
    return row


@router.get("")
def queue(request: Request, include_closed: bool = False, user: StaffUser = Depends(VIEWERS)):
    store = get_store()
    rows = [summary(store, r) for r in store.requisitions.values() if include_closed or r.status in OPEN_STATUSES]
    rows.sort(key=lambda r: (r["days_left"] is None, r["days_left"] if r["days_left"] is not None else 0))
    audit_phi(request, user, action="read", resource_type="requisition_queue", resource_id=None)
    return {"requisitions": rows, "targets": triage.config(store).model_dump(),
            "counts": {s: sum(1 for r in store.requisitions.values() if r.status == s)
                       for s in ("received", "processing", "ready", "approved", "waitlisted", "booked", "failed")}}


@router.get("/samples")
def samples(user: StaffUser = Depends(INTAKE)):
    """Synthetic requisitions to paste into the intake form."""
    store = get_store()
    rng = random.Random()
    patients = [p for p in store.patients.values() if not p.id.startswith("PT-DEMO")]
    out = []
    for _ in range(3):
        patient, referrer = rng.choice(patients), rng.choice(list(store.referrers.values()))
        text, labels = generate(rng, patient, referrer)
        out.append({"patient_id": patient.id, "patient_name": patient.full_name, "referrer_id": referrer.id,
                    "text": text, "expected": {"priority": labels.priority, "protocol_id": labels.protocol_id}})
    return {"samples": out}


class NewRequisition(BaseModel):
    patient_id: str
    referrer_id: str
    text: str
    channel: Literal["fax", "portal", "online_form"] = "fax"


@router.post("")
def receive(body: NewRequisition, request: Request, user: StaffUser = Depends(INTAKE)):
    store = get_store()
    if body.patient_id not in store.patients or body.referrer_id not in store.referrers:
        raise HTTPException(422, "Unknown patient or referrer")
    if not body.text.strip():
        raise HTTPException(422, "Requisition text is empty")
    req = Requisition(id=store.next_id("REQ"), patient_id=body.patient_id, referrer_id=body.referrer_id,
                      received_at=datetime.now(), channel=body.channel, text=body.text.strip()[:6000])
    store.requisitions[req.id] = req
    store.touch()
    audit_phi(request, user, action="create", resource_type="requisition", resource_id=req.id)
    # The intake worker picks it up within a few seconds (see app.main).
    return summary(store, req)


@router.get("/triage/config")
def get_targets(user: StaffUser = Depends(VIEWERS)):
    return triage.config(get_store()).model_dump()


@router.put("/triage/config")
def put_targets(body: triage.TriageConfig, request: Request, user: StaffUser = Depends(DIRECTOR)):
    store = get_store()
    store.modules["triage_config"] = body
    audit_phi(request, user, action="update", resource_type="triage_config", resource_id=None)
    store.touch()
    return body.model_dump()


@router.get("/triage/agreement")
def triage_agreement(user: StaffUser = Depends(VIEWERS)):
    return triage.agreement(get_store())


@router.get("/{req_id}")
def detail(req_id: str, request: Request, user: StaffUser = Depends(VIEWERS)):
    store = get_store()
    req = _get(store, req_id)
    patient = store.patients[req.patient_id]
    ext = service.extractions(store).get(req.id)
    tri = triage.records(store).get(req.id)
    pro = protocols.records(store).get(req.id)
    protocol = BY_ID.get(pro.effective_id) if pro else None
    appt = store.appointments.get(req.appointment_id or "")
    screening = mri.for_requisition(store, req.id)
    out = {
        "summary": summary(store, req),
        "text": req.text,
        "patient": {"id": patient.id, "name": patient.full_name, "age": age_from_dob(patient.dob), "sex": patient.sex,
                    "language": patient.preferred_language},
        "extraction": ext.model_dump(mode="json") if ext else None,
        "field_labels": FIELD_LABELS,
        "low_confidence_threshold": LOW_CONFIDENCE,
        "triage": tri.model_dump(mode="json") if tri else None,
        "targets": triage.config(store).model_dump(),
        "protocol": None,
        "contrast": None,
        "mri": screening.model_dump(mode="json") if screening else None,
        "appointment": appointment_view(store, appt) if appt else None,
        "waitlist": store.waitlist[req.waitlist_id].model_dump(mode="json") if req.waitlist_id in store.waitlist else None,
        "priors": [t.model_dump(mode="json") for t in priors.tasks(store).values() if appt and t.appointment_id == appt.id],
        "prep": None,
    }
    if pro:
        out["protocol"] = {
            **pro.model_dump(mode="json"),
            "primary": BY_ID[pro.primary_id].model_dump(),
            "alternatives": [BY_ID[a].model_dump() for a in pro.alternative_ids],
            "approved": BY_ID[pro.approved_id].model_dump() if pro.approved_id else None,
            "library": [p.model_dump() for p in BY_ID.values() if p.modality == BY_ID[pro.primary_id].modality],
        }
    if protocol and protocol.contrast:
        out["contrast"] = contrast.evaluate(store, req.patient_id, req.id).model_dump(mode="json")
    if protocol:
        text, used, note = prep.message_text(store, protocol.exam_code, patient.preferred_language)
        out["prep"] = {"key": prep.key_for(protocol.exam_code), "language": used, "text": text, "note": note}
    audit_phi(request, user, action="read", resource_type="requisition", resource_id=req.id)
    return out


class Correction(BaseModel):
    value: str | list[str]


@router.patch("/{req_id}/fields/{field}")
def correct_field(req_id: str, field: str, body: Correction, request: Request, user: StaffUser = Depends(VIEWERS)):
    store = get_store()
    _get(store, req_id)
    ext = service.extractions(store).get(req_id)
    if ext is None or field not in ext.fields:
        raise HTTPException(404, "No such extracted field")
    old = ext.fields[field]
    corrected = lambda v: {"value": v.strip(), "source_quote": "", "confidence": 1.0, "corrected_by": user.name}  # noqa: E731
    if isinstance(old, list):
        values = body.value if isinstance(body.value, list) else [v for v in body.value.split(";")]
        ext.fields[field] = [corrected(v) for v in values if v.strip()]
    else:
        ext.fields[field] = corrected(body.value if isinstance(body.value, str) else "; ".join(body.value))
    ext.corrections.append({"field": field, "old": old, "new": ext.fields[field], "by": user.name,
                            "at": datetime.now().isoformat(timespec="seconds")})
    ext.low_confidence = [f for f in ext.low_confidence if f != field]
    store.touch()
    audit_phi(request, user, action="update", resource_type="requisition_extraction", resource_id=req_id)
    return detail(req_id, request, user)


class Override(BaseModel):
    priority: Literal["P1", "P2", "P3", "P4"]
    reason: str


@router.post("/{req_id}/triage/confirm")
def confirm_triage(req_id: str, request: Request, user: StaffUser = Depends(RADIOLOGIST)):
    store = get_store()
    _get(store, req_id)
    rec = triage.records(store).get(req_id)
    if rec is None or rec.ai_priority is None:
        raise HTTPException(409, "No AI priority to confirm; set one with an override")
    rec.review_action, rec.final_priority = "confirmed", rec.ai_priority
    rec.reviewed_by, rec.reviewed_at, rec.override_reason = user.name, datetime.now(), None
    store.touch()
    audit_phi(request, user, action="update", resource_type="triage", resource_id=req_id)
    return detail(req_id, request, user)


@router.post("/{req_id}/triage/override")
def override_triage(req_id: str, body: Override, request: Request, user: StaffUser = Depends(RADIOLOGIST)):
    store = get_store()
    req = _get(store, req_id)
    if len(body.reason.strip()) < 5:
        raise HTTPException(422, "A reason is required to change the priority")
    rec = triage.records(store).get(req_id)
    if rec is None:
        raise HTTPException(409, "Requisition has not been triaged yet")
    rec.review_action, rec.final_priority, rec.override_reason = "overridden", body.priority, body.reason.strip()
    rec.reviewed_by, rec.reviewed_at = user.name, datetime.now()
    if req.waitlist_id in store.waitlist:
        store.waitlist[req.waitlist_id].urgency = body.priority
    store.touch()
    audit_phi(request, user, action="override", resource_type="triage", resource_id=req_id)
    return detail(req_id, request, user)


class ProtocolDecision(BaseModel):
    protocol_id: str
    reason: str | None = None


@router.post("/{req_id}/protocol/approve")
def approve_protocol(req_id: str, body: ProtocolDecision, request: Request, user: StaffUser = Depends(RADIOLOGIST)):
    store = get_store()
    req = _get(store, req_id)
    rec = protocols.records(store).get(req_id)
    if rec is None:
        raise HTTPException(409, "No protocol suggestion yet")
    if body.protocol_id not in BY_ID:
        raise HTTPException(422, "Unknown protocol")
    rec.approved_id, rec.approved_by, rec.approved_at = body.protocol_id, user.name, datetime.now()
    rec.change_reason = body.reason if body.protocol_id != rec.primary_id else None
    if req.status == "ready":
        req.status = "approved"
    tri = triage.records(store).get(req_id)
    if tri and not tri.review_action and tri.ai_priority:
        tri.review_action, tri.reviewed_by, tri.reviewed_at = "confirmed", user.name, datetime.now()
    protocol = BY_ID[body.protocol_id]
    if req.waitlist_id in store.waitlist:  # keep the booking slot length in step with the protocol
        entry = store.waitlist[req.waitlist_id]
        entry.protocol_id, entry.duration_minutes, entry.exam_code = protocol.id, protocol.minutes, protocol.exam_code
    mri.create_for(store, req)
    store.touch()
    audit_phi(request, user, action="approve", resource_type="protocol", resource_id=req_id)
    return detail(req_id, request, user)


def _require_approved(store: Store, req: Requisition) -> None:
    if req.status not in ("approved",):
        raise HTTPException(409, f"Requisition is {req.status}; a radiologist must approve the protocol first")


@router.post("/{req_id}/waitlist")
def to_waitlist(req_id: str, request: Request, user: StaffUser = Depends(INTAKE)):
    store = get_store()
    req = _get(store, req_id)
    _require_approved(store, req)
    service.add_to_waitlist(store, req)
    audit_phi(request, user, action="create", resource_type="waitlist_entry", resource_id=req_id)
    return detail(req_id, request, user)


@router.post("/{req_id}/book")
def book(req_id: str, request: Request, user: StaffUser = Depends(INTAKE)):
    store = get_store()
    req = _get(store, req_id)
    _require_approved(store, req)
    if service.book_next(store, req) is None:
        raise HTTPException(409, "No free slot in the next three weeks")
    audit_phi(request, user, action="create", resource_type="appointment", resource_id=req_id)
    return detail(req_id, request, user)


@router.post("/{req_id}/mri-screening")
def send_mri_screening(req_id: str, request: Request, user: StaffUser = Depends(VIEWERS)):
    store = get_store()
    req = _get(store, req_id)
    screening = mri.create_for(store, req)
    if screening is None:
        raise HTTPException(409, "Not an MRI requisition, or a questionnaire was already sent")
    audit_phi(request, user, action="create", resource_type="mri_screening", resource_id=req_id)
    return detail(req_id, request, user)


@router.post("/{req_id}/reprocess")
def reprocess(req_id: str, request: Request, user: StaffUser = Depends(require_roles(Role.OPERATIONS_MANAGER, Role.ADMIN))):
    store = get_store()
    req = _get(store, req_id)
    if req.status in ("booked", "waitlisted"):
        raise HTTPException(409, "Already scheduled")
    req.status = "received"
    store.touch()
    audit_phi(request, user, action="update", resource_type="requisition", resource_id=req_id)
    return summary(store, req)
