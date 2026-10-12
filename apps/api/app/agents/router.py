"""Agent runtime API (spec 6.4): the registries, run traces and lineage, approvals, and the reference agent.

- GET  /api/agents                       the agent registry (any signed-in user: the web app flags
                                         output of agents that have not passed their evaluation)
- GET  /api/agents/tools                 the tool registry
- GET  /api/agents/runs[/{run_id}]       traces (admin)
- GET  /api/agents/lineage?ref=          from an output back to its run, inputs, model and prompt version (admin)
- GET  /api/agents/approvals             open approval requests and AI recommendations for the user's role
- POST /api/agents/tasks/{id}/decision   approve / reject; an approved privileged call then runs
- POST /api/hospital/encounters/{id}/patient-message   the patient message triage agent, for a nurse or physician
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.agents import approvals, registry
from app.agents import trace as traces
from app.agents.library import patient_message_triage
from app.agents.gateway import runtime_fhir
from app.agents.registry import AgentNotDeployable
from app.agents.runtime import RunRefused, lineage, runtime
from app.core.auth import client_ip, current_user, require_roles
from app.core.models import Role, StaffUser
from app.core.store import get_store
from app.ehr.clock import hospital_now
from app.ehr.codes import PRACTITIONER_ROLE, TASK_CODE, concept
from app.ehr.gateway import Actor, FhirGateway
from app.fhir.dt import fhir_datetime, ref

router = APIRouter(prefix="/api")
ADMIN = require_roles(Role.ADMIN)
HOSPITAL = (Role.PHYSICIAN, Role.NURSE, Role.PHARMACIST, Role.CLERK, Role.OPERATIONS_MANAGER)


def _agent_view(spec: registry.AgentSpec) -> dict:
    mode = registry.app_mode()
    return {
        "agent_id": spec.agent_id, "version": spec.version, "kind": spec.kind, "owner": spec.owner,
        "purpose": spec.purpose, "risk_tier": spec.risk_tier, "allowed_tools": spec.allowed_tools,
        "data_scope": spec.data_scope.model_dump(),
        "model_policy": spec.model_policy.model_dump() if spec.model_policy else None,
        "prompt_version": spec.prompt_version, "required_signoff_role": spec.required_signoff_role,
        "cosign": spec.cosign, "consent_required": spec.consent_required,
        "eval_status": spec.eval_status.model_dump(), "deployment_status": spec.deployment_status,
        "entrypoint": spec.entrypoint, "evaluated": spec.evaluated,
        "not_evaluated_flag": spec.is_agent and not spec.evaluated and mode == "demo",
        "refused_reason": registry.gate(spec, mode)}


@router.get("/agents")
def agent_registry(user: StaffUser = Depends(current_user)):
    return {"mode": registry.app_mode(),
            "agents": [_agent_view(s) for _, s in sorted(registry.agents().items())]}


@router.get("/agents/tools")
def tool_registry(user: StaffUser = Depends(current_user)):
    return {"tools": [t.model_dump() for _, t in sorted(registry.tools().items())]}


@router.get("/agents/runs")
def runs(agent_id: str | None = None, limit: int = 50, user: StaffUser = Depends(ADMIN)):
    table = traces.table()
    found = table.find_by("agent_id", agent_id) if agent_id else table.values()
    found = sorted(found, key=lambda t: t.started_at, reverse=True)[:max(1, min(limit, 200))]
    return {"runs": [{"run_id": t.run_id, "agent_id": t.agent_id, "agent_version": t.agent_version, "kind": t.kind,
                      "outcome": t.outcome, "model": t.model, "prompt_version": t.prompt_version,
                      "evaluated": t.evaluated, "tool_calls": len(t.tool_calls),
                      "refused": sum(c.status == "refused" for c in t.tool_calls), "started_at": t.started_at,
                      "cost_usd": t.cost_usd, "actor_role": t.actor_role} for t in found]}


@router.get("/agents/runs/{run_id}")
def run_detail(run_id: str, user: StaffUser = Depends(ADMIN)):
    trace = traces.get(run_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="No such run")
    prov = None
    if trace.provenance_id:
        prov = runtime_fhir().backend.read("Provenance", trace.provenance_id)
        get_store().audit.record(user_id=user.id, user_name=user.name, role=str(user.role), action="read",
                                 resource_type="Provenance", resource_id=trace.provenance_id, module="agent_runtime")
    return {"trace": trace.model_dump(mode="json"), "provenance": prov}


@router.get("/agents/lineage")
def output_lineage(ref: str, user: StaffUser = Depends(ADMIN)):
    try:
        out = lineage(ref)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    get_store().audit.record(user_id=user.id, user_name=user.name, role=str(user.role), action="read",
                             resource_type="Provenance", resource_id=ref, module="agent_runtime",
                             reason=f"lineage of {ref}")
    return out


@router.get("/agents/approvals")
def approval_queue(user: StaffUser = Depends(require_roles(*HOSPITAL))):
    return {"tasks": approvals.queue(user)}


class Decision(BaseModel):
    decision: Literal["approve", "reject"]
    note: str = Field(default="", max_length=1000)


def _result_view(result) -> dict | None:
    if result is None:
        return None
    return {"tool_id": result.tool_id, "status": result.status, "reason": result.reason, "detail": result.detail,
            "refs": result.refs, "output": result.output}


@router.post("/agents/tasks/{task_id}/decision")
def decide(task_id: str, body: Decision, request: Request, user: StaffUser = Depends(require_roles(*HOSPITAL))):
    try:
        decision = approvals.decide(user, task_id, body.decision, body.note, source_ip=client_ip(request))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except approvals.DecisionDenied as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except approvals.DecisionError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    try:
        executed = runtime.execute_approved(decision)
    except AgentNotDeployable as e:
        executed, decision["execution_refused"] = None, str(e)
    return {**decision, "execution": _result_view(executed)}


class PatientMessage(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    channel: Literal["portal", "voicemail", "phone", "sms", "letter"] = "portal"


@router.post("/hospital/encounters/{encounter_id}/patient-message")
def triage_patient_message(encounter_id: str, body: PatientMessage, request: Request,
                           user: StaffUser = Depends(require_roles(Role.NURSE, Role.PHYSICIAN))):
    """Run the patient message triage agent for this user. Without the patient's AI consent, or when the
    agent may not run (prod mode, not evaluated), the message is still recorded for a nurse: no AI."""
    try:
        with runtime.start(patient_message_triage.AGENT_ID, user=user, encounter_id=encounter_id) as run:
            result = patient_message_triage.run(run, body.message, channel=body.channel)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except RunRefused as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except AgentNotDeployable as e:
        return {"status": "not_processed", "reason": str(e), **_manual(user, request, encounter_id, body)}
    if result.status == "not_processed":
        return {**result.as_dict(), **_manual(user, request, encounter_id, body)}
    return result.as_dict()


def _manual(user: StaffUser, request: Request, encounter_id: str, body: PatientMessage) -> dict:
    """No AI: record the message and give the nurse a Task to read it (bedside nursing module, as the user)."""
    fhir = FhirGateway(Actor.of(user, request), "bedside_nursing")
    encounter = fhir.read("Encounter", encounter_id)
    patient = ref("Patient", encounter.subject.reference.split("/", 1)[1])
    now = fhir_datetime(hospital_now(get_store()))
    comm = fhir.create({"resourceType": "Communication", "status": "completed", "subject": patient,
                        "sender": patient, "encounter": ref("Encounter", encounter_id), "received": now,
                        "payload": [{"contentString": body.message}]})
    task = fhir.create({"resourceType": "Task", "status": "requested", "intent": "order", "priority": "routine",
                        "code": concept(TASK_CODE, "patient-message", "Patient message"),
                        "description": "Patient message received; read it (no AI triage).", "for": patient,
                        "encounter": ref("Encounter", encounter_id), "authoredOn": now,
                        "performerType": [concept(PRACTITIONER_ROLE, "nurse")],
                        "focus": {"reference": f"Communication/{comm.id}"}})
    return {"communication_id": comm.id, "task_id": task.id, "manual": True}
