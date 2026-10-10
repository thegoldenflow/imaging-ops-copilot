"""Workflow API (spec 6.5 UI): the workflow view, retry and skip, the demo's deliberate failure, low-confidence reviews.

- GET  /api/workflows/status                       Temporal on / off / unreachable, workers polling, outbox
- GET  /api/workflows                              timelines (filter by type, status)
- GET  /api/workflows/{id}                         one timeline (+ Temporal's own description when online)
- GET  /api/workflows/encounters/{encounter_id}    the encounter's journey (the Control Tower's patient card)
- POST /api/workflows/{id}/steps/{step}/retry      operations manager and admin only, audited
- POST /api/workflows/{id}/steps/{step}/skip       operations manager and admin only, audited
- POST /api/workflows/journeys                     start a journey for an inpatient admitted before the bridge ran
- GET / POST / DELETE /api/workflows/faults        the demo's deliberate failures (demo mode only)
- GET  /api/workflows/reviews/{id}, POST ...       a low-confidence output and its correction

The timeline is read from the database (progress.py), so the view works while the worker is down; without
TEMPORAL_ADDRESS it says "offline" and retry, skip and starts are refused.
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.agents import approvals, evalsets, registry, reviews
from app.core.auth import client_ip, require_roles
from app.core.models import Role, StaffUser
from app.core.store import get_store
from app.ehr import access
from app.ehr.gateway import Actor, FhirGateway
from app.workflows import bridge, client, faults, progress
from app.workflows import model as M

router = APIRouter(prefix="/api/workflows")
VIEWERS = (Role.OPERATIONS_MANAGER, Role.ADMIN, Role.PHYSICIAN, Role.NURSE, Role.PHARMACIST, Role.CLERK)
CONTROLLERS = (Role.OPERATIONS_MANAGER, Role.ADMIN)
MODULE = "workflow_engine"


def _status() -> dict:
    link = client.link()
    out = link.status() if link is not None else client.offline_status()
    return {**out, "outbox": bridge.backlog(get_store())}


def _visible(user: StaffUser, run: progress.WorkflowRun) -> bool:
    """Nurses and physicians see the workflows of their units; hospital-wide roles see all."""
    policy = access.POLICIES.get(str(user.role))
    if policy is None or policy.scope != "unit":
        return True
    return run.unit_id is None or run.unit_id in user.unit_ids


def _run(user: StaffUser, workflow_id: str) -> progress.WorkflowRun:
    run = progress.get(workflow_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"No workflow {workflow_id}")
    if not _visible(user, run):
        raise HTTPException(status_code=403, detail="This workflow belongs to a patient outside your units")
    return run


@router.get("/status")
def status(user: StaffUser = Depends(require_roles(*VIEWERS))):
    return {**_status(), "can_control": user.role in CONTROLLERS,
            "timers": bridge.timers(), "faults": faults.pending() if registry.app_mode() == "demo" else {}}


@router.get("")
def workflows(type: str | None = None, status: str | None = None, limit: int = 200,
              user: StaffUser = Depends(require_roles(*VIEWERS))):
    runs = [r for r in progress.all_runs() if _visible(user, r)
            and (type is None or r.workflow_type == type) and (status is None or r.status == status)]
    runs.sort(key=lambda r: (r.status != "running", -(r.updated_at.timestamp() if r.updated_at else 0)))
    counts: dict[str, dict[str, int]] = {}
    for r in runs:
        c = counts.setdefault(r.workflow_type, {"running": 0, "completed": 0, "failed": 0, "attention": 0})
        c[r.status] = c.get(r.status, 0) + 1
        c["attention"] += int(progress.summary(r)["attention"])
    return {"status": _status(), "workflows": [progress.summary(r) for r in runs[:limit]], "counts": counts}


@router.get("/encounters/{encounter_id}")
def encounter_journey(encounter_id: str, user: StaffUser = Depends(require_roles(*VIEWERS))):
    run = progress.for_encounter(encounter_id)
    if run is None or not _visible(user, run):
        raise HTTPException(status_code=404, detail=f"No journey workflow for {encounter_id}")
    return progress.summary(run)


class ControlBody(BaseModel):
    note: str = Field(default="", max_length=300)


RETRYABLE = {M.FAILED}
SKIPPABLE = {M.FAILED, M.WAITING}


@router.post("/{workflow_id}/steps/{step}/{action}", status_code=202)
def control(workflow_id: str, step: str, action: Literal["retry", "skip"], body: ControlBody, request: Request,
            user: StaffUser = Depends(require_roles(*CONTROLLERS))):
    """Retry a failed step, or skip a failed or waiting one. The signal goes out through the outbox."""
    store = get_store()
    run = progress.get(workflow_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"No workflow {workflow_id}")
    if client.link() is None:
        raise HTTPException(status_code=409, detail="Temporal is offline (TEMPORAL_ADDRESS is not set)")
    s = next((x for x in run.steps if x["key"] == step), None)
    if s is None:
        raise HTTPException(status_code=404, detail=f"{workflow_id} has no step {step}")
    allowed = RETRYABLE if action == "retry" else SKIPPABLE
    if run.status != "running" or s["status"] not in allowed:
        raise HTTPException(status_code=409, detail=f"Step {step} is {s['status']}; {action} needs a step that is "
                                                    f"{' or '.join(sorted(allowed))}")
    event_id = str(uuid.uuid4())
    store.audit.record(user_id=user.id, user_name=user.name, role=str(user.role), action=f"workflow_{action}",
                       event_type="write", resource_type="Workflow", resource_id=workflow_id, outcome="allowed",
                       source_ip=client_ip(request), reason=f"{action} step {step}" + (f": {body.note}" if body.note
                                                                                       else ""),
                       encounter_id=run.encounter_id, module=MODULE)
    bridge.control(workflow_id, step, action, f"{user.name} ({user.role})", str(user.role), event_id)
    return {"queued": True, "workflow_id": workflow_id, "step": step, "action": action, "event_id": event_id}


class JourneyBody(BaseModel):
    encounter_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    signoff_timeout_s: int | None = Field(default=None, ge=5, le=7 * 86400)
    escalation_timeout_s: int | None = Field(default=None, ge=5, le=7 * 86400)
    followup_delay_s: int | None = Field(default=None, ge=5, le=14 * 86400)


@router.post("/journeys", status_code=202)
def start_journey(body: JourneyBody, request: Request, user: StaffUser = Depends(require_roles(*CONTROLLERS))):
    """A journey for an inpatient already in a bed (the bridge starts one at every new admission). Idempotent:
    the workflow id is the encounter's business key."""
    if client.link() is None:
        raise HTTPException(status_code=409, detail="Temporal is offline (TEMPORAL_ADDRESS is not set)")
    found = FhirGateway(Actor.system("workflows"), MODULE).read("Encounter", body.encounter_id)
    enc = found.to_fhir() if found is not None else None
    if enc is None or (enc.get("class") or {}).get("code") != "IMP" or enc.get("status") != "in-progress":
        raise HTTPException(status_code=409, detail=f"{body.encounter_id} is not an inpatient stay in progress")
    timer_values = {k: v for k, v in body.model_dump().items() if k.endswith("_s") and v is not None}
    event_id = str(uuid.uuid4())
    get_store().audit.record(user_id=user.id, user_name=user.name, role=str(user.role), action="workflow_start",
                             event_type="write", resource_type="Workflow", resource_id=M.journey_id(body.encounter_id),
                             outcome="allowed", source_ip=client_ip(request),
                             reason="journey started from the workflow view" + (f" (timers {timer_values})"
                                                                                if timer_values else ""),
                             encounter_id=body.encounter_id, module=MODULE)
    workflow_id = bridge.start_journey(body.encounter_id, access.patient_of(enc), f"user:{user.id}", event_id,
                                       timer_values)
    return {"queued": True, "workflow_id": workflow_id}


STEP_KEYS = sorted({d.key for defs in M.STEPS.values() for d in defs if d.kind != "timer"})


class FaultBody(BaseModel):
    step: str = Field(pattern="^(" + "|".join(STEP_KEYS) + ")$")
    times: int = Field(default=2, ge=1, le=faults.MAX_TIMES)


def _demo_only() -> None:
    if registry.app_mode() != "demo":
        raise HTTPException(status_code=409, detail="Deliberate failures are a demo feature (APP_MODE=demo)")


@router.get("/faults")
def fault_list(user: StaffUser = Depends(require_roles(*CONTROLLERS))):
    return {"faults": faults.pending()}


@router.post("/faults")
def fault_inject(body: FaultBody, request: Request, user: StaffUser = Depends(require_roles(*CONTROLLERS))):
    _demo_only()
    out = faults.inject(body.step, body.times)
    get_store().audit.record(user_id=user.id, user_name=user.name, role=str(user.role), action="workflow_fault",
                             event_type="write", resource_type="Workflow", resource_id=body.step, outcome="allowed",
                             source_ip=client_ip(request),
                             reason=f"demo: the next {body.times} attempt(s) of {body.step} fail", module=MODULE)
    return {"faults": out}


@router.delete("/faults")
def fault_clear(request: Request, user: StaffUser = Depends(require_roles(*CONTROLLERS))):
    faults.clear()
    get_store().audit.record(user_id=user.id, user_name=user.name, role=str(user.role), action="workflow_fault",
                             event_type="write", resource_type="Workflow", resource_id="*", outcome="allowed",
                             source_ip=client_ip(request), reason="demo failures cleared", module=MODULE)
    return {"faults": {}}


# ---------- low-confidence reviews ----------


def _review_for(user: StaffUser, review_id: str) -> reviews.AgentReview:
    review = reviews.get(review_id)
    if review is None:
        raise HTTPException(status_code=404, detail=f"No review {review_id}")
    spec = registry.agent(review.agent_id)
    if spec is None or str(user.role) not in spec.required_signoff_role:
        raise HTTPException(status_code=403, detail=f"{review.agent_id} output is reviewed by "
                                                    f"{' or '.join(spec.required_signoff_role if spec else [])}")
    if review.patient_id and not FhirGateway(Actor.of(user), MODULE).is_in_scope(review.patient_id):
        raise HTTPException(status_code=403, detail="This patient is outside your units")
    return review


@router.get("/reviews/{review_id}")
def review(review_id: str, user: StaffUser = Depends(require_roles(Role.NURSE, Role.PHYSICIAN, Role.PHARMACIST))):
    r = _review_for(user, review_id)
    message = r.input.get("message") or ""
    return {"id": r.id, "agent_id": r.agent_id, "run_id": r.run_id, "status": r.status, "task_id": r.task_id,
            "workflow_id": r.workflow_id, "encounter_id": r.encounter_id, "confidence": r.confidence,
            "threshold": r.threshold, "output": r.output, "correction": r.correction, "case_id": r.case_id,
            "regression": r.regression, "correctable": evalsets.correctable(r.agent_id),
            "input_excerpt": message[:2000], "note": "Shown as the model saw it: identifiers are placeholders."}


class CorrectionBody(BaseModel):
    correction: dict[str, str] = Field(default_factory=dict)
    note: str = Field(default="", max_length=500)


@router.post("/reviews/{review_id}")
def correct_review(review_id: str, body: CorrectionBody, request: Request,
                   user: StaffUser = Depends(require_roles(Role.NURSE, Role.PHYSICIAN, Role.PHARMACIST))):
    _review_for(user, review_id)
    try:
        r = reviews.correct(user, review_id, body.correction, body.note, source_ip=client_ip(request))
    except reviews.ReviewProblem as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except approvals.DecisionDenied as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except approvals.DecisionError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return {"id": r.id, "status": r.status, "correction": r.correction, "task_id": r.task_id}


# Last: the catch-all path must not shadow /status, /faults, /journeys ...
@router.get("/{workflow_id}")
def workflow(workflow_id: str, user: StaffUser = Depends(require_roles(*VIEWERS))):
    run = _run(user, workflow_id)
    out = {**progress.view(run), "can_control": user.role in CONTROLLERS, "temporal": None}
    link = client.link()
    if link is not None:
        try:
            out["temporal"] = link.describe(workflow_id)
        except Exception as e:  # unreachable: the stored timeline is still shown
            out["temporal"] = {"error": str(e)[:200]}
    return out
