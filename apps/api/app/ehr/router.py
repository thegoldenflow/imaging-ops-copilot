"""Hospital day simulator and event log API (WP3).

The control bar of the Control Tower (WP5) drives these endpoints: the clock,
advancing it, running it at a demo rate, fast-forwarding to 08:00 tomorrow and
injecting scripted scenarios. The event log shows what the HL7 adapter turned
into domain events, and each subscriber's progress. Bed managers (operations
manager) and admins only; every control action is audited as `simulator_event`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.core.auth import client_ip, require_roles
from app.core.models import Role, StaffUser
from app.core.store import get_store
from app.ehr import scenarios, simulator
from app.ehr.events import EVENT_TYPES, bus
from app.ehr.hl7 import Hl7EventAdapter

router = APIRouter(prefix="/api/hospital")
CONTROL = (Role.OPERATIONS_MANAGER, Role.ADMIN)


def _audit(request: Request, user: StaffUser, action: str, detail: str, resource_id: str | None = None) -> None:
    get_store().audit.record(user_id=user.id, user_name=user.name, role=str(user.role), action="simulator_event",
                             resource_type="HospitalSimulator", resource_id=resource_id or action,
                             source_ip=client_ip(request), reason=f"{action}: {detail}")


def _busy(e: simulator.SimulatorBusy):
    raise HTTPException(status_code=409, detail=str(e)) from e


@router.get("/simulator")
def get_simulator(user: StaffUser = Depends(require_roles(*CONTROL))):
    return {**simulator.status(get_store()), "scenarios": scenarios.SCENARIOS}


class AdvanceRequest(BaseModel):
    minutes: float | None = Field(default=None, gt=0, le=36 * 60)
    until: datetime | None = None


@router.post("/simulator/advance")
def advance(request: Request, body: AdvanceRequest, user: StaffUser = Depends(require_roles(*CONTROL))):
    if (body.minutes is None) == (body.until is None):
        raise HTTPException(status_code=422, detail="Give either minutes or until")
    store = get_store()
    try:
        result = (simulator.advance_by(store, body.minutes) if body.minutes is not None
                  else simulator.advance(store, body.until))
    except simulator.SimulatorBusy as e:
        _busy(e)
    _audit(request, user, "advance", f"{result.start:%Y-%m-%d %H:%M} -> {result.end:%Y-%m-%d %H:%M}, "
                                     f"{sum(result.events.values())} events")
    return result.summary()


@router.post("/simulator/fast-forward")
def fast_forward(request: Request, hour: int = Body(8, embed=True, ge=0, le=23),
                 user: StaffUser = Depends(require_roles(*CONTROL))):
    store = get_store()
    try:
        result = simulator.fast_forward(store, hour)
    except simulator.SimulatorBusy as e:
        _busy(e)
    _audit(request, user, "fast-forward", f"to {result.end:%Y-%m-%d %H:%M}, {sum(result.events.values())} events")
    return result.summary()


@router.post("/simulator/run")
def run(request: Request, rate: float = Body(simulator.DEFAULT_RATE, embed=True),
        user: StaffUser = Depends(require_roles(*CONTROL))):
    try:
        state = simulator.run(get_store(), rate)
    except simulator.SimulatorBusy as e:
        _busy(e)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    _audit(request, user, "run", f"rate {rate:g}")
    return state


@router.post("/simulator/pause")
def pause(request: Request, user: StaffUser = Depends(require_roles(*CONTROL))):
    try:
        state = simulator.pause(get_store())
    except simulator.SimulatorBusy as e:
        _busy(e)
    _audit(request, user, "pause", f"at {state['now']}")
    return state


class InjectRequest(BaseModel):
    scenario: Literal["icu_surge", "ed_surge", "or_overrun", "consent_revoked"]
    count: int | None = Field(default=None, ge=1, le=20)  # icu_surge only: patients (default: fill ICU plus one)


@router.post("/simulator/inject")
def inject(request: Request, body: InjectRequest, user: StaffUser = Depends(require_roles(*CONTROL))):
    try:
        added = scenarios.inject(get_store(), body.scenario, operator=user.id, count=body.count)
    except simulator.SimulatorBusy as e:
        _busy(e)
    except scenarios.ScenarioError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    _audit(request, user, "inject", f"{body.scenario}, correlation {added['correlation_id']}", body.scenario)
    return added


@router.get("/events")
def events(request: Request, after: int | None = None, before: int | None = None,
           type: list[str] | None = Query(None), encounter: str | None = None,
           limit: int = Query(100, ge=1, le=500), user: StaffUser = Depends(require_roles(*CONTROL))):
    """The domain event log, newest first (references only, no clinical content)."""
    unknown = set(type or []) - set(EVENT_TYPES)
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown event type(s): {sorted(unknown)}")
    store = get_store()
    found = bus.events(store, after=after, before=before, types=type, encounter=encounter, limit=limit)
    store.audit.record(user_id=user.id, user_name=user.name, role=str(user.role), action="read",
                       resource_type="DomainEvent", resource_id=encounter, source_ip=client_ip(request),
                       reason="event log")
    return {"events": [e.model_dump(mode="json") for e in found], "counts": bus.counts(store),
            "types": EVENT_TYPES}


@router.get("/events/consumers")
def consumers(user: StaffUser = Depends(require_roles(*CONTROL))):
    return {"consumers": bus.consumer_status(get_store())}


@router.post("/hl7")
async def hl7_inbound(request: Request, user: StaffUser = Depends(require_roles(Role.ADMIN))):
    """An HL7 v2 message (ER7 text) into the same adapter the simulator's messages go through.
    Answers with the HL7 ACK; an unmappable message is parked as a dead letter."""
    text = (await request.body()).decode("utf-8", errors="replace")
    event, ack = await run_in_threadpool(Hl7EventAdapter().receive_er7, text)
    _audit(request, user, "hl7-inbound", f"{event.source} -> {event.type}" if event else "rejected",
           event.message_id if event else None)
    return {"ack": ack, "event": event.model_dump(mode="json") if event else None}
