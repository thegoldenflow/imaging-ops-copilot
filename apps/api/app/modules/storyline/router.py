"""Storyline API (spec 8.2, WP9): the guided demo's status, one-click reset and the clock's advance.

Any signed-in member of staff may follow the story (the page shows every step and who plays it); the reset and the
advance are demo controls like the header's Reset demo, audited (`simulator_event`).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app.agents import ondemand
from app.core.auth import current_user, deny
from app.core.models import StaffUser
from app.core.store import get_store
from app.ehr import simulator
from app.modules.storyline import service

router = APIRouter(prefix="/api/storyline")


@router.get("")
def storyline(user: StaffUser = Depends(current_user)):
    return service.status(get_store(), user)


@router.post("/reset")
def reset(user: StaffUser = Depends(current_user)):
    """The demo reset followed by the storyline's preset (about a minute)."""
    state = service.reset(user)
    return {"ok": True, "prepared_at": state["prepared_at"], "capacity": state.get("capacity")}


@router.post("/steps/{key}/ai")
def step_ai(key: str, request: Request, user: StaffUser = Depends(current_user)):
    """A step's Ask AI on the storyline page: its items' AI (the exception's narrative; the naproxen review and the
    admission reconciliation) is queued now, as this person's storyline step, for the items they may ask for (the
    items' own checks: the reviewer roles and 6.3 scope); 403 and audited when they may ask for none."""
    try:
        return service.ask_step_ai(get_store(), user, key)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ondemand.NotAllowed:
        deny(request, user, resource_type="storyline_step", resource_id=key)


@router.post("/advance")
def advance(user: StaffUser = Depends(current_user)):
    """Fast-forward the hospital clock to the current step's beat (a background job; GET shows its progress)."""
    try:
        return service.advance(get_store(), user)
    except simulator.SimulatorBusy as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
