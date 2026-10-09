"""Control Tower API (spec 7.1): boards, drill-down, the exception stream and the action drawer.

Viewers: the bed manager (operations manager), nurses and physicians; what each
sees is shaped in service.py. Decisions: the operations manager, or a nurse for
an exception on one of their units. The simulator's control bar uses the WP3
endpoints (/api/hospital/simulator/...), which the operations manager drives.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.agents import approvals
from app.core.auth import client_ip, require_roles
from app.core.models import Role, StaffUser
from app.core.store import get_store
from app.ehr import scenarios, simjobs, simulator
from app.ehr.events import bus
from app.modules.control_tower import exceptions as X
from app.modules.control_tower import service
from app.modules.control_tower.flowmodels import models

router = APIRouter(prefix="/api/control-tower")
VIEWERS = (Role.OPERATIONS_MANAGER, Role.NURSE, Role.PHYSICIAN)
CONTROLLERS = {"operations_manager", "admin"}  # may drive the simulator (WP3 API)


def _decision_errors(fn):
    try:
        return fn()
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except (approvals.DecisionDenied, PermissionError) as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except (service.DecisionProblem, approvals.DecisionError) as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.get("/board")
def board(user: StaffUser = Depends(require_roles(*VIEWERS))):
    """The three boards, the KPIs, the exception stream, the hospital clock and the latest events."""
    store = get_store()
    snap = service.refresh(store)
    clock = simulator.status(store, upcoming=0)
    events = bus.events(store, limit=6)
    return {**service.board_view(store, user, snap),
            "clock": {k: clock[k] for k in ("now", "rate", "running", "day_start", "horizon", "stopped")},
            "job": simjobs.current(), "can_control": str(user.role) in CONTROLLERS,
            "scenarios": scenarios.SCENARIOS,
            "events": [{"seq": e.seq, "type": e.type, "at": e.occurred_at, "encounter": e.encounter,
                        "attrs": e.attrs} for e in events]}


@router.get("/units/{unit_id}")
def unit(unit_id: str, user: StaffUser = Depends(require_roles(*VIEWERS))):
    """The unit's bed grid (drill-down)."""
    snap = service.refresh(get_store())
    try:
        return service.unit_view(user, snap, unit_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.get("/patients/{encounter_id}")
def patient(encounter_id: str, request: Request, user: StaffUser = Depends(require_roles(*VIEWERS))):
    """The patient card, read as the user (6.3): the bed manager sees MRN, admission and expected discharge only;
    a nurse or physician outside the patient's unit gets 403 break_glass_required."""
    snap = service.refresh(get_store())
    try:
        return service.patient_card(user, request, encounter_id, snap)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.get("/exceptions")
def exception_list(user: StaffUser = Depends(require_roles(*VIEWERS))):
    store = get_store()
    service.refresh(store)
    return {"exceptions": [service.exception_view(e) for e in X.stream(store, limit=200)]}


@router.get("/exceptions/{exception_id}")
def exception_detail(exception_id: str, user: StaffUser = Depends(require_roles(*VIEWERS))):
    """One exception with its facts, menu and narrative (narrated now when the background has not yet)."""
    store = get_store()
    exc = X.get(store, exception_id)
    if exc is None:
        raise HTTPException(status_code=404, detail=f"No exception {exception_id}")
    if exc.narration_status == "pending" and exc.status in X.LIVE and exc.cleared_at is None:
        exc = service.narrate(store, exception_id)
    return {**service.exception_view(exc, detail=True), "can_decide": service.can_decide(user, exc)}


class ApproveBody(BaseModel):
    action_ids: list[str] = Field(min_length=1, max_length=8)
    note: str = Field(default="", max_length=500)


class RejectBody(BaseModel):
    reason: str = Field(max_length=500)


class DeferBody(BaseModel):
    minutes: int
    note: str = Field(default="", max_length=500)


@router.post("/exceptions/{exception_id}/approve")
def approve(exception_id: str, body: ApproveBody, request: Request,
            user: StaffUser = Depends(require_roles(Role.OPERATIONS_MANAGER, Role.NURSE))):
    exc = _decision_errors(lambda: service.approve(get_store(), user, exception_id, body.action_ids, body.note,
                                                   source_ip=client_ip(request)))
    return service.exception_view(exc, detail=True)


@router.post("/exceptions/{exception_id}/reject")
def reject(exception_id: str, body: RejectBody, request: Request,
           user: StaffUser = Depends(require_roles(Role.OPERATIONS_MANAGER, Role.NURSE))):
    exc = _decision_errors(lambda: service.reject(get_store(), user, exception_id, body.reason,
                                                  source_ip=client_ip(request)))
    return service.exception_view(exc, detail=True)


@router.post("/exceptions/{exception_id}/defer")
def defer(exception_id: str, body: DeferBody, request: Request,
          user: StaffUser = Depends(require_roles(Role.OPERATIONS_MANAGER, Role.NURSE))):
    exc = _decision_errors(lambda: service.defer(get_store(), user, exception_id, body.minutes, body.note,
                                                 source_ip=client_ip(request)))
    return service.exception_view(exc, detail=True)


@router.get("/models")
def model_report(user: StaffUser = Depends(require_roles(*VIEWERS))):
    """The flow models' metrics (from synthetic data: they show the pipeline works, not real-world performance)."""
    return {"models": models().metrics(),
            "note": "Trained and validated on synthetic data; the metrics show that the pipeline works, not clinical "
                    "or operational performance."}
