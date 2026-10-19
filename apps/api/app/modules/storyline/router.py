"""Storyline API (spec 8.2, WP9): the guided demo's status, one-click reset, the clock's advance and the demo's
automation (do a step, jump to a step).

Any signed-in member of staff may follow the story (the page shows every step and who plays it); the reset, the
advance and the automation are demo controls like the header's Reset demo, audited (`simulator_event`).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.agents import ondemand
from app.core.auth import client_ip, current_user, deny
from app.core.models import StaffUser
from app.core.store import get_store
from app.ehr import simulator
from app.modules.storyline import autoplay, service

router = APIRouter(prefix="/api/storyline")


@router.get("")
def storyline(user: StaffUser = Depends(current_user)):
    return service.status(get_store(), user)


class ResetIn(BaseModel):
    language: str = "zh"  # the patient: zh (Wang Xiuying, Mandarin-speaking) or en (Margaret Ellis, English-speaking)


@router.post("/reset")
def reset(body: ResetIn | None = None, user: StaffUser = Depends(current_user)):
    """The demo reset followed by the storyline's preset (about a minute), with the patient who speaks the chosen
    language (no body: Mandarin, the default story); 422 for a language no storyline patient speaks."""
    try:
        state = service.reset(user, (body or ResetIn()).language)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return {"ok": True, "prepared_at": state["prepared_at"], "capacity": state.get("capacity"),
            "language": state["language"]}


@router.post("/steps/{key}/ai")
def step_ai(key: str, request: Request, user: StaffUser = Depends(current_user)):
    """A step's Ask AI on the storyline page: its items' AI (the exception's narrative; the naproxen review and the
    admission reconciliation) is queued now, as this person's storyline step, for the items they may ask for (the
    items' own checks: the reviewer roles and 6.3 scope); 403 and audited when they may ask for none, and each item
    refused when others were asked for is audited too (as a list page's batch)."""
    store = get_store()
    try:
        out = service.ask_step_ai(store, user, key)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ondemand.NotAllowed:
        deny(request, user, resource_type="storyline_step", resource_id=key)
    for kind_name, item_id in out["denied"]:
        store.audit.record(user_id=user.id, user_name=user.name, role=str(user.role), action="post",
                           resource_type=f"ai_{kind_name}", resource_id=item_id, outcome="denied",
                           source_ip=client_ip(request), reason=f"storyline step {out['step']}: not this person's to "
                           "ask for")
    return out


@router.post("/advance")
def advance(user: StaffUser = Depends(current_user)):
    """Fast-forward the hospital clock to the current step's beat (a background job; GET shows its progress)."""
    try:
        return service.advance(get_store(), user)
    except simulator.SimulatorBusy as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


def _automation(start, key: str, request: Request, user: StaffUser) -> dict:
    try:
        return start(get_store(), user, key, source_ip=client_ip(request))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except (autoplay.Busy, ValueError) as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.post("/steps/{key}/autoplay")
def autoplay_step(key: str, request: Request, user: StaffUser = Depends(current_user)):
    """Demo mode's "Do this step for me": the current step done by the demo's automation, through the API as the
    people who play it (a background job; GET shows its progress as `autoplay`). Audited as this person's demo
    action; every record it signs says it was the automation."""
    return _automation(autoplay.do_step, key, request, user)


@router.post("/steps/{key}/jump")
def jump_to_step(key: str, request: Request, user: StaffUser = Depends(current_user)):
    """Jump to step `key`: the steps before it, from the current one, done by the demo's automation (as above);
    `key` is then the current step."""
    return _automation(autoplay.jump, key, request, user)
