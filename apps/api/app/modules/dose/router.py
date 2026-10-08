"""System 14 · CT dose monitoring API."""

import statistics
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.core.auth import audit_phi, require_roles
from app.core.models import Role, StaffUser
from app.core.store import Store, get_store
from app.modules.dose import service
from app.modules.protocols.library import BY_ID as PROTOCOLS

router = APIRouter(prefix="/api/dose", tags=["dose"])

VIEWERS = require_roles(Role.TECHNOLOGIST, Role.RADIOLOGIST, Role.OPERATIONS_MANAGER, Role.MEDICAL_DIRECTOR, Role.ADMIN)
REVIEWERS = require_roles(Role.TECHNOLOGIST, Role.RADIOLOGIST, Role.MEDICAL_DIRECTOR)
DIRECTOR = require_roles(Role.MEDICAL_DIRECTOR, Role.ADMIN)


def record_view(store: Store, rec: service.DoseRecord, *, events: bool = False) -> dict:
    ref = service.references(store)[rec.protocol_id]
    data = rec.model_dump(mode="json", exclude=None if events else {"events"})
    return {
        **data, "patient_name": store.patients[rec.patient_id].full_name, "exam_name": store.exams[rec.exam_code].name,
        "protocol_name": PROTOCOLS[rec.protocol_id].name, "scanner_name": f"{store.sites[rec.site_id].name} · {rec.scanner_id}",
        "reference": ref.model_dump(), "exceedance": service.exceedance(store, rec),
    }


@router.get("/overview")
def overview(request: Request, user: StaffUser = Depends(VIEWERS)):
    store, now = get_store(), datetime.now()
    recs = list(service.records(store).values())
    refs = service.references(store)
    exceeding = [r for r in recs if service.exceedance(store, r)]
    exceeding.sort(key=lambda r: (r.review is not None, -r.performed_at.timestamp()))
    protocols = []
    for pid, ref in refs.items():
        mine = [r for r in recs if r.protocol_id == pid]
        protocols.append({
            "protocol_id": pid, "name": PROTOCOLS[pid].name, "reference": ref.model_dump(), "records": len(mine),
            "median_ctdivol": round(statistics.median(r.ctdivol_mgy for r in mine), 2) if mine else None,
            "median_dlp": round(statistics.median(r.dlp_total_mgycm for r in mine), 1) if mine else None,
            "exceeding": sum(1 for r in mine if service.exceedance(store, r)),
        })
    by_scanner = []
    for scanner_id in sorted({r.scanner_id for r in recs}):
        mine = [r for r in recs if r.scanner_id == scanner_id]
        recent = [r for r in mine if r.performed_at >= now - timedelta(days=14)]
        by_scanner.append({"scanner_id": scanner_id, "site": store.sites[store.scanners[scanner_id].site_id].name,
                           "records": len(mine), "exceeding": sum(1 for r in mine if service.exceedance(store, r)),
                           "recent_exceed_rate": round(sum(1 for r in recent if service.exceedance(store, r)) / len(recent), 3) if recent else None})
    audit_phi(request, user, action="read", resource_type="ct_dose", resource_id=None)
    return {
        "coverage": service.coverage(store, now - timedelta(days=service.HISTORY_DAYS)),
        "records": len(recs),
        "exceeding": len(exceeding), "open_exceptions": sum(r.review is None for r in exceeding),
        "exceptions": [record_view(store, r) for r in exceeding[:80]],
        "protocols": protocols, "scanners": by_scanner,
        "trend_by_scanner": service.weekly_trend(store, lambda r: r.scanner_id, now),
        "trend_by_protocol": service.weekly_trend(store, lambda r: PROTOCOLS[r.protocol_id].name, now),
        "review_outcomes": service.REVIEW_OUTCOMES,
    }


@router.get("/records")
def list_records(request: Request, scanner_id: str | None = None, protocol_id: str | None = None, limit: int = 100,
                 user: StaffUser = Depends(VIEWERS)):
    store = get_store()
    recs = [r for r in service.records(store).values()
            if (not scanner_id or r.scanner_id == scanner_id) and (not protocol_id or r.protocol_id == protocol_id)]
    recs.sort(key=lambda r: r.performed_at, reverse=True)
    audit_phi(request, user, action="read", resource_type="ct_dose", resource_id=None)
    return {"records": [record_view(store, r) for r in recs[:min(limit, 500)]], "total": len(recs)}


@router.get("/records/{record_id}")
def get_record(record_id: str, request: Request, user: StaffUser = Depends(VIEWERS)):
    store = get_store()
    rec = service.records(store).get(record_id)
    if rec is None:
        raise HTTPException(404, "Dose record not found")
    audit_phi(request, user, action="read", resource_type="ct_dose_record", resource_id=record_id)
    return record_view(store, rec, events=True)


class ReviewRequest(BaseModel):
    outcome: str
    note: str = ""


@router.post("/records/{record_id}/review")
def review(record_id: str, body: ReviewRequest, request: Request, user: StaffUser = Depends(REVIEWERS)):
    store = get_store()
    rec = service.records(store).get(record_id)
    if rec is None:
        raise HTTPException(404, "Dose record not found")
    if not service.exceedance(store, rec):
        raise HTTPException(409, "This exam is within its reference level")
    if body.outcome not in service.REVIEW_OUTCOMES:
        raise HTTPException(422, f"Outcome must be one of {', '.join(service.REVIEW_OUTCOMES)}")
    rec.review = {"outcome": body.outcome, "note": body.note.strip(), "by": user.name,
                  "at": datetime.now().isoformat(timespec="minutes")}
    store.touch()
    audit_phi(request, user, action="update", resource_type="ct_dose_record", resource_id=record_id)
    return record_view(store, rec)


@router.put("/references/{protocol_id}")
def set_reference(protocol_id: str, body: service.ReferenceLevel, request: Request, user: StaffUser = Depends(DIRECTOR)):
    store = get_store()
    refs = service.references(store)
    if protocol_id not in refs:
        raise HTTPException(404, "No CT protocol with that id")
    refs[protocol_id] = body
    store.touch()
    audit_phi(request, user, action="update", resource_type="dose_reference", resource_id=protocol_id)
    return {"protocol_id": protocol_id, **body.model_dump()}
