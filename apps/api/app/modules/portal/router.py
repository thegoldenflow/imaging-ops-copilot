"""System 17 · Referring Physician Portal API (referrer role only)."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.auth import audit_phi, deny, require_roles
from app.core.models import Requisition, Role, StaffUser
from app.core.store import get_store
from app.modules.portal import service

router = APIRouter(prefix="/api/portal", tags=["portal"])

REFERRER = require_roles(Role.REFERRER)


def _referrer_id(user: StaffUser) -> str:
    if not user.referrer_id:
        raise HTTPException(403, "No referrer profile linked to this account")
    return user.referrer_id


@router.get("/patients")
def my_patients(request: Request, user: StaffUser = Depends(REFERRER)):
    store, rid, now = get_store(), _referrer_id(user), datetime.now()
    rows = []
    for pid in service.patient_ids(store, rid):
        p = store.patients[pid]
        upcoming = sorted(a.start for a in list(store.appointments.values())
                          if a.patient_id == pid and a.referrer_id == rid and a.start > now
                          and a.status in ("booked", "confirmed"))
        open_reqs = sum(1 for r in list(store.requisitions.values())
                        if r.patient_id == pid and r.referrer_id == rid and r.status != "booked")
        rows.append({"id": pid, "name": p.full_name, "dob": p.dob.isoformat(),
                     "next_appointment": upcoming[0].isoformat(timespec="minutes") if upcoming else None,
                     "open_requisitions": open_reqs})
    rows.sort(key=lambda r: r["name"])
    audit_phi(request, user, action="read", resource_type="portal_patient_list", resource_id=rid)
    return {"patients": rows, "referrer": store.referrers[rid].model_dump()}


@router.get("/patients/{patient_id}")
def patient(patient_id: str, request: Request, user: StaffUser = Depends(REFERRER)):
    store, rid = get_store(), _referrer_id(user)
    if patient_id not in service.patient_ids(store, rid):
        # Same answer whether the patient exists or not, so ids cannot be probed.
        deny(request, user, resource_type="patient", resource_id=patient_id)
    audit_phi(request, user, action="read", resource_type="patient", resource_id=patient_id)
    return service.patient_detail(store, rid, patient_id)


@router.get("/requisitions")
def my_requisitions(request: Request, user: StaffUser = Depends(REFERRER)):
    store, rid = get_store(), _referrer_id(user)
    reqs = sorted((r for r in list(store.requisitions.values()) if r.referrer_id == rid),
                  key=lambda r: r.received_at, reverse=True)
    audit_phi(request, user, action="read", resource_type="portal_requisitions", resource_id=rid)
    return {"requisitions": [service.requisition_view(store, r) for r in reqs]}


@router.get("/form-options")
def form_options(user: StaffUser = Depends(REFERRER)):
    return {"exams": service.EXAM_CHOICES, "languages": {"en": "English", "fr": "Français", "zh": "中文", "pa": "ਪੰਜਾਬੀ"}}


@router.post("/requisitions")
def submit(body: service.PortalRequisition, request: Request, user: StaffUser = Depends(REFERRER)):
    store, rid = get_store(), _referrer_id(user)
    if body.patient_id:
        if body.patient_id not in service.patient_ids(store, rid):
            deny(request, user, resource_type="patient", resource_id=body.patient_id)
        patient = store.patients[body.patient_id]
    elif body.new_patient:
        patient = service.find_or_create_patient(store, body.new_patient)
    else:
        raise HTTPException(422, "Choose one of your patients or enter a new patient")
    req = Requisition(id=store.next_id("REQ"), patient_id=patient.id, referrer_id=rid, received_at=datetime.now(),
                      channel="portal", text=service.compose_text(patient, store.referrers[rid], body))
    store.requisitions[req.id] = req
    store.touch()
    audit_phi(request, user, action="create", resource_type="requisition", resource_id=req.id)
    # The intake worker runs extraction, triage and protocol suggestion within seconds.
    return service.requisition_view(store, req)
