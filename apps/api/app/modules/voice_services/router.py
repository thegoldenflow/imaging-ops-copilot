"""Patient voice services API (spec 7.4): the call-centre console, the follow-up calls and the nurse queue.

The calls are text-simulated: a member of staff plays the caller (or the patient on a follow-up call) in the web
console, by typing or with the browser's speech recognition; the agents act as themselves. No audio reaches the
server.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.auth import require_roles
from app.core.models import Role, StaffUser
from app.core.store import get_store
from app.ehr.gateway import FhirAccessDenied
from app.modules.voice_services import callcenter, followup, queue, service

router = APIRouter(prefix="/api/voice", tags=["voice_services"])

CONSOLE = require_roles(Role.CLERK, Role.OPERATIONS_MANAGER)  # who runs the call-centre console
NURSING = require_roles(Role.NURSE, Role.PHYSICIAN)
NURSE = require_roles(Role.NURSE)
DEMO = require_roles(Role.CLERK, Role.NURSE, Role.PHYSICIAN, Role.OPERATIONS_MANAGER)


class Utterance(BaseModel):
    text: str = Field(min_length=1, max_length=800)


class Action(BaseModel):
    kind: Literal["callback", "close", "refer"]
    note: str = Field(min_length=3, max_length=1000)


def _errors(fn):
    try:
        return fn()
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except (PermissionError, FhirAccessDenied) as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


# ---------- the call centre ----------


@router.post("/calls")
def start_call(user: StaffUser = Depends(CONSOLE)):
    store = get_store()
    return service.call_view(callcenter.start(store, user), user)


@router.post("/calls/{call_id}/turn")
def call_turn(call_id: str, body: Utterance, user: StaffUser = Depends(CONSOLE)):
    def run():
        reply, call, ended = callcenter.turn(get_store(), call_id, body.text)
        return {"reply": reply, "ended": ended, "language": call.language, "call": service.call_view(call, user)}

    return _errors(run)


@router.post("/calls/{call_id}/end")
def end_call(call_id: str, user: StaffUser = Depends(CONSOLE)):
    return _errors(lambda: service.call_view(callcenter.end(get_store(), call_id), user))


@router.get("/calls")
def calls(user: StaffUser = Depends(CONSOLE)):
    return {"calls": service.recent_calls(get_store(), user)}


@router.get("/calls/{call_id}")
def call(call_id: str, user: StaffUser = Depends(CONSOLE)):
    found = callcenter.get(call_id, get_store())
    if found is None:
        raise HTTPException(status_code=404, detail="No such call")
    return service.call_view(found, user)


@router.get("/requests")
def requests(user: StaffUser = Depends(require_roles(Role.CLERK))):
    """Reschedules and cancellations the call centre asked for, waiting for a clerk (decide them with
    POST /api/agents/tasks/{task_id}/decision)."""
    return {"requests": service.requests(get_store(), user)}


@router.get("/faq")
def faq(user: StaffUser = Depends(DEMO)):
    return {"sections": service.faq()}


# ---------- follow-up calls and the nurse queue ----------


@router.get("/followups")
def followups(which: Literal["open", "calls", "all"] = "open", user: StaffUser = Depends(NURSING)):
    return _errors(lambda: {"items": queue.items(get_store(), user, which=which)})


@router.get("/followups/summary")
def followups_summary(user: StaffUser = Depends(NURSING)):
    return queue.summary(get_store(), user)


@router.get("/followups/{call_id}")
def followup_detail(call_id: str, user: StaffUser = Depends(NURSING)):
    def run():
        call = queue._item(get_store(), user, call_id)
        return queue.view(call, user, detail=True)

    return _errors(run)


@router.post("/followups/{call_id}/start")
def followup_start(call_id: str, user: StaffUser = Depends(NURSE)):
    def run():
        queue._item(get_store(), user, call_id)
        reply, call = followup.start_call(get_store(), call_id, user)
        return {"reply": reply, "item": queue.view(call, user, detail=True)}

    return _errors(run)


@router.post("/followups/{call_id}/answer")
def followup_answer(call_id: str, body: Utterance, user: StaffUser = Depends(NURSE)):
    def run():
        queue._item(get_store(), user, call_id)
        reply, call, ended = followup.answer(get_store(), call_id, body.text)
        return {"reply": reply, "ended": ended, "item": queue.view(call, user, detail=True)}

    return _errors(run)


@router.post("/followups/{call_id}/no-answer")
def followup_no_answer(call_id: str, user: StaffUser = Depends(NURSE)):
    def run():
        queue._item(get_store(), user, call_id)
        return queue.view(followup.no_answer(get_store(), call_id), user, detail=True)

    return _errors(run)


@router.post("/followups/{call_id}/actions")
def followup_action(call_id: str, body: Action, user: StaffUser = Depends(NURSE)):
    return _errors(lambda: queue.act(get_store(), user, call_id, body.kind, body.note))


@router.post("/followups/sweep")
def followup_sweep(user: StaffUser = Depends(NURSING)):
    """Schedule the follow-up calls that are due now (the background loop does this every few seconds)."""
    return {"scheduled": followup.sweep(get_store())}


@router.get("/demo")
def demo(user: StaffUser = Depends(DEMO)):
    """The planted demo cases and the lines the presenter says to play the caller or the patient."""
    return service.demo_cases(get_store(), user)
