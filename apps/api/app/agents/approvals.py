"""Human approvals and review decisions on AI work (spec 6.4).

Three kinds of Task (code system urn:demo-hospital:task), all written by the
runtime as a system actor (registry entry `agent_runtime`):

- `approval-request`: a privileged tool call waiting for a person. The Task
  names the tool, the agent, the run, the hash of the exact arguments (and the
  arguments, encrypted at rest with the rest of the FHIR store) and the roles that
  may approve (performerType). An approver's decision completes it (or rejects
  it) and writes an `approve` audit event; only then does the Tool Gateway run
  the call, and only with these arguments.
- `ai-review`: a recommendation in a person's review queue (submitForReview);
  accepting or rejecting it is recorded the same way.
- `needs-human`: a tool that failed after its retries, for a person to do by hand.

Every decision is also written into the run's trace as `human_action`.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from app.agents import trace as traces
from app.core.audit import mrn_hash
from app.core.models import StaffUser
from app.core.store import get_store
from app.ehr import access, breakglass
from app.ehr.clock import hospital_now
from app.ehr.codes import MRN_SYSTEM, PRACTITIONER_ROLE, TASK_CODE, concept
from app.ehr.gateway import Actor, FhirGateway
from app.fhir.dt import fhir_datetime, ref

if TYPE_CHECKING:
    from app.agents.gateway import Target
    from app.agents.registry import ToolSpec
    from app.agents.runtime import AgentRun

APPROVAL = "approval-request"
REVIEW = "ai-review"
NEEDS_HUMAN = "needs-human"
OPEN = ("requested", "received", "accepted", "ready", "in-progress")
ROLE_OF_CODE = {code: role for role, code in access.ROLE_CODE.items()}


class ApprovalInvalid(Exception):
    """The approval record does not cover this call."""


class DecisionError(ValueError):
    """The task cannot be decided (not an approval or review task, or already decided)."""


class DecisionDenied(PermissionError):
    """This user may not decide this task."""


def _fhir() -> FhirGateway:
    from app.agents.gateway import runtime_fhir

    return runtime_fhir()


def code_of(task: dict) -> str | None:
    return next((c.get("code") for c in (task.get("code") or {}).get("coding") or []), None)


def _values(entries: list | None) -> dict[str, str]:
    return {(e.get("type") or {}).get("text"): e.get("valueString") for e in entries or []}


def inputs(task: dict) -> dict[str, str]:
    return _values(task.get("input"))


def outputs(task: dict) -> dict[str, str]:
    return _values(task.get("output"))


def _agent_of(task: dict) -> str | None:
    label = next((e.get("valueString") for e in task.get("extension") or [] if e.get("url") == traces.AI_AGENT), None)
    return label.split("@")[0] if label else None


def _entry(name: str, value: str) -> dict:
    return {"type": {"text": name}, "valueString": value}


def _task(run: AgentRun, code: tuple[str, str], description: str, roles: list[str], target: Target,
          priority: str = "routine", entries: list[dict] | None = None) -> dict:
    now = fhir_datetime(hospital_now(get_store()))
    task: dict = {"resourceType": "Task", "status": "requested", "intent": "order", "priority": priority,
                  "code": concept(TASK_CODE, *code), "description": description[:1000], "authoredOn": now,
                  "lastModified": now,
                  "performerType": [concept(PRACTITIONER_ROLE, access.ROLE_CODE[r]) for r in roles
                                    if r in access.ROLE_CODE]}
    if target.patient_id:
        task["for"] = ref("Patient", target.patient_id)
    if target.encounter_id:
        task["encounter"] = ref("Encounter", target.encounter_id)
    if target.ref and not target.ref.startswith(("Location/", "Patient/")):
        task["focus"] = {"reference": target.ref}
    if entries:
        task["input"] = entries
    return traces.stamp(task, run.run_id, run.spec.label)


def _summary(tool: ToolSpec, args: dict) -> str:
    shown = ", ".join(f"{k}={v}" for k, v in args.items() if isinstance(v, str) and len(v) <= 40)
    return f"{tool.description} ({shown})" if shown else tool.description


def request(run: AgentRun, tool: ToolSpec, args: dict, args_hash: str, target: Target, roles: list[str]) -> str:
    """Open an approval Task for a privileged call; returns its id."""
    entries = [_entry("tool", tool.tool_id), _entry("agent", run.spec.agent_id), _entry("run", run.run_id),
               _entry("args_hash", args_hash), _entry("args", traces.canonical(args)),
               _entry("on_behalf_of", run.user.id if run.user else "system")]
    task = _task(run, (APPROVAL, "Approve an AI action"),
                 f"Approve {tool.tool_id} requested by {run.spec.label}: {_summary(tool, args)}", roles, target,
                 entries=entries)
    return _fhir().create(task).id


def needs_human(run: AgentRun, tool: ToolSpec, target: Target, detail: str) -> str:
    roles = [r for r in run.spec.required_signoff_role if r in access.ROLE_CODE] or ["nurse"]
    task = _task(run, (NEEDS_HUMAN, "Needs a person"),
                 f"{run.spec.label} could not complete {tool.tool_id}; please do it by hand. {detail}", roles[:1],
                 target, priority="urgent", entries=[_entry("tool", tool.tool_id), _entry("run", run.run_id)])
    return _fhir().create(task).id


def is_open(task_id: str | None) -> bool:
    task = _fhir().backend.read("Task", task_id) if task_id else None
    return bool(task) and task.get("status") in OPEN


def verify(task_id: str, run: AgentRun, tool: ToolSpec, args_hash: str, roles: list[str], target: Target) -> StaffUser:
    """The approver, when the task is a completed approval of exactly this call; ApprovalInvalid otherwise."""
    task = _fhir().backend.read("Task", task_id)
    if task is None or code_of(task) != APPROVAL:
        raise ApprovalInvalid(f"{task_id} is not an approval record")
    given = inputs(task)
    if given.get("tool") != tool.tool_id or given.get("agent") != run.spec.agent_id:
        raise ApprovalInvalid(f"approval {task_id} was given for {given.get('tool')} by {given.get('agent')}")
    if given.get("args_hash") != args_hash:
        raise ApprovalInvalid(f"approval {task_id} was given for other arguments")
    if task.get("status") != "completed" or outputs(task).get("decision") != "approve":
        raise ApprovalInvalid(f"approval {task_id} is {task.get('status')}, not approved")
    approver_id = outputs(task).get("decided_by")
    approver = get_store().staff.get(approver_id) if approver_id else None
    if approver is None or str(approver.role) not in roles:
        raise ApprovalInvalid(f"approval {task_id} was not given by {' or '.join(roles)}")
    events = [e for e in get_store().audit.query(resource_type="Task", resource_id=task_id, user_id=approver.id,
                                                 outcome="allowed")
              if (e.event_type, e.action) in (("approve", "approve"), ("sign", "sign"))]
    if not events:
        raise ApprovalInvalid(f"no approve or sign audit event by {approver.id} for {task_id}")
    return approver


def decide(user: StaffUser, task_id: str, decision: Literal["approve", "reject"], note: str = "", *,
           source_ip: str | None = None) -> dict:
    """A person approves or rejects an approval request, or accepts or rejects a recommendation."""
    fhir = _fhir()
    task = fhir.backend.read("Task", task_id)
    if task is None:
        raise LookupError(f"Task/{task_id} not found")
    code = code_of(task)
    if code not in (APPROVAL, REVIEW):
        raise DecisionError(f"Task/{task_id} is not an approval request or an AI recommendation")
    if task.get("status") not in OPEN:
        raise DecisionError(f"Task/{task_id} is already {task.get('status')}")
    roles = [ROLE_OF_CODE.get(c) for c in access.performer_codes(task)]
    if str(user.role) not in roles:
        raise DecisionDenied(f"This task is for {' or '.join(r for r in roles if r) or 'nobody'}, "
                             f"not the {user.role} role")
    patient = access.patient_of(task)
    if patient and not FhirGateway(Actor.of(user), "agent_runtime").is_in_scope(patient) \
            and breakglass.active_grant(get_store(), user.id, patient) is None:
        raise DecisionDenied("This patient is outside your units")
    action = ("approve" if code == APPROVAL else "accept") if decision == "approve" else "reject"
    now = hospital_now(get_store())
    task["status"] = "completed" if decision == "approve" else "rejected"
    task["lastModified"] = fhir_datetime(now)
    task["owner"] = ref("Practitioner", user.practitioner_id, user.name) if user.practitioner_id else \
        {"display": user.name}
    task["output"] = [_entry("decision", "approve" if decision == "approve" else "reject"),
                      _entry("decided_by", user.id), *([_entry("note", note[:1000])] if note else [])]
    fhir.update(task)
    given = inputs(task)
    agent_id = given.get("agent") or _agent_of(task)
    patient_resource = fhir.backend.read("Patient", patient) if patient else None
    mrn = next((i.get("value") for i in (patient_resource or {}).get("identifier") or []
                if i.get("system") == MRN_SYSTEM), None)
    get_store().audit.record(
        user_id=user.id, user_name=user.name, role=str(user.role), action=action, event_type="approve",
        resource_type="Task", resource_id=task_id, outcome="allowed", source_ip=source_ip,
        reason="; ".join(x for x in (f"{code} {given.get('tool') or ''}".strip(), note[:200]) if x),
        patient_mrn_hash=mrn_hash(mrn) if mrn else None, encounter_id=access.encounter_of(task), module=agent_id)
    run_id = given.get("run") or traces.run_of(task)
    trace = traces.get(run_id) if run_id else None
    if trace is not None:
        trace.human_action = {"role": str(user.role), "user_id": user.id, "decision": action, "task_id": task_id,
                              "at": datetime.now().isoformat(timespec="seconds")}
        traces.save(trace)
    return {"task_id": task_id, "kind": code, "status": task["status"], "decision": action, "agent_id": agent_id,
            "tool_id": given.get("tool"), "run_id": run_id,
            "args": json.loads(given["args"]) if given.get("args") else None,
            "on_behalf_of": given.get("on_behalf_of")}


def queue(user: StaffUser, *, limit: int = 50) -> list[dict]:
    """Open approval requests and AI recommendations for the user's role (and, for unit-scoped roles, units)."""
    code = access.ROLE_CODE.get(str(user.role))
    if code is None:
        return []
    out = []
    fhir = _fhir()
    for task in fhir.backend.search("Task", status=list(OPEN), code=[APPROVAL, REVIEW], order="-date", limit=500):
        kind = code_of(task)
        if code not in access.performer_codes(task):
            continue
        patient = access.patient_of(task)
        if patient and access.POLICIES.get(str(user.role)) and access.POLICIES[str(user.role)].scope == "unit" \
                and not FhirGateway(Actor.of(user), "agent_runtime").is_in_scope(patient):
            continue
        given = inputs(task)
        out.append({"task_id": task["id"], "kind": kind, "status": task.get("status"),
                    "description": task.get("description"), "authored_on": task.get("authoredOn"),
                    "encounter_id": access.encounter_of(task), "focus": (task.get("focus") or {}).get("reference"),
                    "tool_id": given.get("tool"), "agent_id": given.get("agent") or _agent_of(task),
                    "run_id": given.get("run") or traces.run_of(task),
                    "recommendation": given.get("recommendation")})
        if len(out) >= limit:
            break
    return out
