"""System 16 · Referral Analytics API."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.core.auth import audit_phi, require_roles
from app.core.models import Role, StaffUser
from app.core.store import get_store
from app.modules.referrals import service

router = APIRouter(prefix="/api/referrals", tags=["referrals"])

VIEWERS = require_roles(Role.OPERATIONS_MANAGER, Role.MEDICAL_DIRECTOR, Role.ADMIN)


@router.get("/overview")
def overview(specialty: str | None = None, modality: str | None = None, site_id: str | None = None,
             referrer_id: str | None = None, user: StaffUser = Depends(VIEWERS)):
    store = get_store()
    data = service.overview(store, datetime.now(), specialty=specialty or None, modality=modality or None,
                            site_id=site_id or None, referrer_id=referrer_id or None)
    plans = service.visits(store)
    for row in data["visit_list"]:
        plan = plans.get(row["referrer_id"])
        row["visit"] = plan.model_dump(mode="json") if plan else None
    data["specialties"] = sorted({r.specialty for r in store.referrers.values()})
    data["sites"] = [{"id": s.id, "name": s.name} for s in store.sites.values()]
    data["thresholds"] = {"decline": service.DECLINE_THRESHOLD, "recent_weeks": service.RECENT_WEEKS,
                          "baseline_weeks": service.WEEKS - service.RECENT_WEEKS,
                          "min_baseline_per_week": service.MIN_BASELINE_PER_WEEK}
    return data


class VisitRequest(BaseModel):
    status: str
    note: str = ""


@router.put("/visits/{referrer_id}")
def plan_visit(referrer_id: str, body: VisitRequest, request: Request, user: StaffUser = Depends(VIEWERS)):
    store = get_store()
    if referrer_id not in store.referrers:
        raise HTTPException(404, "Unknown referrer")
    if body.status not in ("planned", "visited"):
        raise HTTPException(422, "Status must be planned or visited")
    plan = service.VisitPlan(referrer_id=referrer_id, status=body.status, note=body.note.strip(), by=user.name,
                             at=datetime.now())
    service.visits(store)[referrer_id] = plan
    store.touch()
    audit_phi(request, user, action="update", resource_type="referrer_visit", resource_id=referrer_id)
    return plan.model_dump(mode="json")


def _current(store) -> service.WeeklySummary | None:
    week = service.weeks(datetime.now())[-1].isoformat()
    return service.summaries(store).get(week)


@router.get("/summary")
def get_summary(user: StaffUser = Depends(VIEWERS)):
    summary = _current(get_store())
    return {"summary": summary.model_dump(mode="json") if summary else None,
            "week_start": service.weeks(datetime.now())[-1].isoformat()}


@router.post("/summary")
def generate(user: StaffUser = Depends(VIEWERS)):
    summary = service.generate_summary(get_store(), datetime.now(), user.name)
    return {"summary": summary.model_dump(mode="json"), "week_start": summary.week_start}


@router.post("/summary/approve")
def approve(user: StaffUser = Depends(VIEWERS)):
    store = get_store()
    summary = _current(store)
    if summary is None or summary.ai_status != "ok":
        raise HTTPException(409, "No usable draft for this week")
    summary.status, summary.approved_by, summary.approved_at = "approved", user.name, datetime.now()
    store.touch()
    return {"summary": summary.model_dump(mode="json"), "week_start": summary.week_start}
