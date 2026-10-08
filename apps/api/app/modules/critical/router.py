"""System 12 · Critical results tracker API."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.core.auth import audit_phi, deny, require_roles
from app.core.models import Role, StaffUser
from app.core.store import Store, get_store
from app.modules.critical import service

router = APIRouter(prefix="/api/critical", tags=["critical"])

VIEWERS = require_roles(Role.RADIOLOGIST, Role.MEDICAL_DIRECTOR, Role.ADMIN, Role.FRONT_DESK, Role.OPERATIONS_MANAGER)
RECORDERS = require_roles(Role.RADIOLOGIST, Role.MEDICAL_DIRECTOR, Role.ADMIN, Role.FRONT_DESK)
CLOSERS = require_roles(Role.RADIOLOGIST, Role.MEDICAL_DIRECTOR, Role.ADMIN)
DIRECTOR = require_roles(Role.MEDICAL_DIRECTOR, Role.ADMIN)
REFERRER = require_roles(Role.REFERRER)


def case_view(store: Store, case: service.CriticalCase, now: datetime) -> dict:
    patient = store.patients[case.patient_id]
    ref = store.referrers[case.referrer_id]
    study = store.studies.get(case.study_id or "")
    return {
        **case.model_dump(mode="json"),
        "patient_name": patient.full_name, "referrer_name": ref.name, "referrer_phone": ref.phone,
        "exam_name": store.exams[study.exam_code].name if study else None,
        "level_label": service.policy(store).levels[case.level].label,
        "overdue": service.is_overdue(case, now),
        "seconds_to_ack_due": round((case.ack_due_at - now).total_seconds()),
    }


def _get(store: Store, case_id: str) -> service.CriticalCase:
    case = service.cases(store).get(case_id)
    if case is None:
        raise HTTPException(404, "Case not found")
    return case


@router.get("")
def list_cases(request: Request, status: str | None = None, user: StaffUser = Depends(VIEWERS)):
    store, now = get_store(), datetime.now()
    # Open work first (newest first), then the closed archive.
    items = sorted(service.cases(store).values(), key=lambda c: (c.status == "closed", -c.created_at.timestamp()))
    counts: dict[str, int] = {}
    for c in items:
        counts[c.status] = counts.get(c.status, 0) + 1
    if status:
        items = [c for c in items if c.status == status]
    audit_phi(request, user, action="read", resource_type="critical_results", resource_id=None)
    return {"cases": [case_view(store, c, now) for c in items], "counts": counts,
            "overdue": sum(service.is_overdue(c, now) for c in service.cases(store).values()),
            "methods": service.ACK_METHODS, "version": store.version}


@router.get("/policy")
def get_policy(user: StaffUser = Depends(VIEWERS)):
    return service.policy(get_store()).model_dump(mode="json")


class PolicyUpdate(BaseModel):
    levels: dict[str, service.LevelPolicy]


@router.put("/policy")
def put_policy(body: PolicyUpdate, request: Request, user: StaffUser = Depends(DIRECTOR)):
    store = get_store()
    pol = service.policy(store)
    if set(body.levels) != set(pol.levels):
        raise HTTPException(422, f"Give a policy for each level: {', '.join(pol.levels)}")
    if any(lv.escalate_after_s <= lv.renotify_after_s for lv in body.levels.values()):
        raise HTTPException(422, "Escalation must come after the re-notification")
    pol.levels, pol.updated_by, pol.updated_at = body.levels, user.name, datetime.now()
    store.touch()
    audit_phi(request, user, action="update", resource_type="critical_policy", resource_id=None)
    return pol.model_dump(mode="json")


@router.get("/mine")
def referrer_cases(request: Request, user: StaffUser = Depends(REFERRER)):
    store, now = get_store(), datetime.now()
    items = [c for c in service.cases(store).values() if c.referrer_id == user.referrer_id]
    items.sort(key=lambda c: c.created_at, reverse=True)
    audit_phi(request, user, action="read", resource_type="critical_results", resource_id=user.referrer_id)
    return {"cases": [case_view(store, c, now) for c in items]}


@router.post("/{case_id}/acknowledge-portal")
def acknowledge_portal(case_id: str, request: Request, user: StaffUser = Depends(REFERRER)):
    store = get_store()
    case = _get(store, case_id)
    if case.referrer_id != user.referrer_id:
        deny(request, user, resource_type="critical_result", resource_id=case_id)
    try:
        service.acknowledge(store, case, by_name=user.name, by_role="Ordering physician", method="portal",
                            recorded_by=user.name)
    except service.CaseError as e:
        raise HTTPException(409, str(e))
    audit_phi(request, user, action="acknowledge", resource_type="critical_result", resource_id=case_id)
    return case_view(store, case, datetime.now())


@router.get("/{case_id}")
def get_case(case_id: str, request: Request, user: StaffUser = Depends(VIEWERS)):
    store = get_store()
    case = _get(store, case_id)
    audit_phi(request, user, action="read", resource_type="critical_result", resource_id=case_id)
    return case_view(store, case, datetime.now())


class AckRequest(BaseModel):
    by_name: str
    by_role: str = "Ordering physician"
    method: str = "phone"
    at: datetime | None = None


@router.post("/{case_id}/acknowledge")
def acknowledge(case_id: str, body: AckRequest, request: Request, user: StaffUser = Depends(RECORDERS)):
    store = get_store()
    case = _get(store, case_id)
    if body.at and body.at > datetime.now():
        raise HTTPException(422, "Acknowledgement time cannot be in the future")
    try:
        service.acknowledge(store, case, by_name=body.by_name, by_role=body.by_role, method=body.method,
                            recorded_by=user.name, at=body.at)
    except service.CaseError as e:
        raise HTTPException(422 if "Record" in str(e) or "Unknown" in str(e) else 409, str(e))
    audit_phi(request, user, action="acknowledge", resource_type="critical_result", resource_id=case_id)
    return case_view(store, case, datetime.now())


class CloseRequest(BaseModel):
    note: str = ""


@router.post("/{case_id}/close")
def close(case_id: str, body: CloseRequest, request: Request, user: StaffUser = Depends(CLOSERS)):
    store = get_store()
    case = _get(store, case_id)
    try:
        service.close(store, case, by=user.name, note=body.note)
    except service.CaseError as e:
        raise HTTPException(409, str(e))
    audit_phi(request, user, action="close", resource_type="critical_result", resource_id=case_id)
    return case_view(store, case, datetime.now())
