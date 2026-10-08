"""System 20 · PHIPA Access Monitoring API (privacy officer: admin; medical director)."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from app.core.auth import audit_phi, require_roles
from app.core.models import Role, StaffUser
from app.core.store import get_store
from app.modules.phipa import service

router = APIRouter(prefix="/api/phipa", tags=["phipa"])

PRIVACY = require_roles(Role.ADMIN, Role.MEDICAL_DIRECTOR)


def _with_state(store, alert: dict) -> dict:
    inv = service.investigations(store).get(alert["id"])
    return {**alert, "investigation": inv.model_dump(mode="json") if inv else
            service.Investigation(alert_id=alert["id"]).model_dump(mode="json")}


@router.get("/alerts")
def alerts(request: Request, status: str | None = None, rule: str | None = None, user: StaffUser = Depends(PRIVACY)):
    store = get_store()
    rows = [_with_state(store, a) for a in service.detect(store)]
    counts = {k: sum(r["investigation"]["status"] == k for r in rows) for k in ("new", "investigating", "closed")}
    if status:
        rows = [r for r in rows if r["investigation"]["status"] == status]
    if rule:
        rows = [r for r in rows if r["rule"] == rule]
    audit_phi(request, user, action="read", resource_type="phipa_alerts", resource_id=None)
    return {"alerts": [{**r, "evidence": r["evidence"][:5], "evidence_count": len(r["evidence"])} for r in rows],
            "counts": counts, "rules": {k: v[0] for k, v in service.RULES.items()}, "outcomes": service.OUTCOMES,
            "staff": sorted(u.name for u in store.staff.values() if u.role in (Role.ADMIN, Role.MEDICAL_DIRECTOR))}


@router.get("/alerts/{alert_id}")
def alert(alert_id: str, request: Request, user: StaffUser = Depends(PRIVACY)):
    store = get_store()
    found = next((a for a in service.detect(store) if a["id"] == alert_id), None)
    if found is None:
        raise HTTPException(404, "Alert not found")
    audit_phi(request, user, action="read", resource_type="phipa_alert", resource_id=alert_id)
    return _with_state(store, found)


class Action(BaseModel):
    action: str  # assign, note, close, reopen
    note: str = ""
    assignee: str | None = None
    outcome: str | None = None


@router.post("/alerts/{alert_id}/actions")
def act(alert_id: str, body: Action, request: Request, user: StaffUser = Depends(PRIVACY)):
    store = get_store()
    found = next((a for a in service.detect(store) if a["id"] == alert_id), None)
    if found is None:
        raise HTTPException(404, "Alert not found")
    try:
        inv = service.act(store, alert_id, body.action, user.name, datetime.now(), note=body.note,
                          assignee=body.assignee, outcome=body.outcome)
    except ValueError as e:
        raise HTTPException(422, str(e))
    store.touch()
    # The investigation trail is itself part of the tamper-evident log.
    audit_phi(request, user, action=f"investigation_{body.action}", resource_type="phipa_alert", resource_id=alert_id)
    return _with_state(store, found) | {"investigation": inv.model_dump(mode="json")}


@router.get("/report")
def report(request: Request, days: int = 30, user: StaffUser = Depends(PRIVACY)):
    store = get_store()
    audit_phi(request, user, action="read", resource_type="phipa_report", resource_id=None)
    return service.report(store, datetime.now(), max(1, min(days, 365)))


@router.get("/export.csv")
def export(request: Request, user: StaffUser = Depends(PRIVACY)):
    store = get_store()
    audit_phi(request, user, action="export", resource_type="phipa_alerts", resource_id=None)
    return Response(service.to_csv(store, service.detect(store)), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="phipa-alerts-{datetime.now():%Y%m%d}.csv"'})
