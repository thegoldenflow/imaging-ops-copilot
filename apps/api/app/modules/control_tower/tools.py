"""Tool handlers of the Control Tower (config/tools/explainException.yaml, createFlowTask.yaml).

The Tool Gateway calls them after its checks (6.4); `ctx.fhir` is FhirGateway for
the agent itself with `control_tower` as purpose module, so the EHR's allow-list,
the registry's writes and the audit apply underneath. Both write Tasks only:

- explainException (recommend): the narrative and recommended actions as an
  `ai-review` Task (intent proposal) for the bed manager and the charge nurses;
  their decision (app/agents/approvals.decide) completes it.
- createFlowTask (action): after that approval, one Task per approved action for
  its owner role (code `flow-action`), based on the review Task; idempotent per
  exception and action (the gateway's key), so a retried approval writes it once.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from app.agents.approvals import REVIEW
from app.core.store import get_store
from app.ehr import access
from app.ehr.clock import hospital_now
from app.ehr.codes import PRACTITIONER_ROLE, TASK_CODE, concept
from app.fhir.dt import fhir_datetime, ref

if TYPE_CHECKING:
    from app.agents.gateway import ToolContext

FLOW_ACTION = "flow-action"
APPROVER_ROLES = ("operations_manager", "nurse")  # the bed manager and the charge nurses (7.1)
PRIORITY = {"high": "urgent", "med": "asap", "low": "routine"}


def _entry(name: str, value: str) -> dict:
    return {"type": {"text": name}, "valueString": value}


def explain_exception(ctx: ToolContext, exception_id: str, unit_id: str, severity: str, title: str, narrative: str,
                      recommended_actions: list[dict], evidence_refs: list[str], source: str) -> dict:
    now = fhir_datetime(hospital_now(get_store()))
    recommendation = {"narrative": narrative, "recommended_actions": recommended_actions}
    task = {"resourceType": "Task", "status": "requested", "intent": "proposal", "priority": PRIORITY[severity],
            "code": concept(TASK_CODE, REVIEW, "Review an AI recommendation"),
            "description": f"{title} ({exception_id})"[:1000], "focus": ref("Location", unit_id),
            "authoredOn": now, "lastModified": now,
            "performerType": [concept(PRACTITIONER_ROLE, access.ROLE_CODE[r]) for r in APPROVER_ROLES],
            "input": [_entry("exception", exception_id), _entry("severity", severity), _entry("source", source),
                      _entry("recommendation", json.dumps(recommendation, ensure_ascii=False)),
                      *([_entry("evidence", ", ".join(evidence_refs))] if evidence_refs else [])]}
    gateway = ctx.fhir  # FhirGateway for the agent (the Tool Gateway opened it)
    stored = gateway.create(ctx.stamp(task)).to_fhir()
    return {"task_id": stored["id"], "status": stored["status"], "_refs": [f"Task/{stored['id']}"]}


def flow_task(exception_id: str, action_id: str, unit_id: str, description: str, performer_role: str, priority: str,
              review_task_id: str | None, approved_by: str, *, encounter: dict | None = None) -> dict:
    """The Task of an approved action (also written without the agent when the agent is refused, e.g. in prod mode
    before its evaluation passed: the rule engine and the drawer keep working without AI)."""
    now = fhir_datetime(hospital_now(get_store()))
    task: dict = {"resourceType": "Task", "status": "requested", "intent": "order", "priority": priority,
                  "code": concept(TASK_CODE, FLOW_ACTION, "Control Tower action"), "description": description,
                  "focus": ref("Location", unit_id), "authoredOn": now, "lastModified": now,
                  "performerType": [concept(PRACTITIONER_ROLE, access.ROLE_CODE[performer_role])],
                  "input": [_entry("exception", exception_id), _entry("action", action_id),
                            _entry("approved_by", approved_by)]}
    if review_task_id:
        task["basedOn"] = [ref("Task", review_task_id)]
    if encounter is not None:
        task["encounter"] = ref("Encounter", encounter["id"])
        task["for"] = encounter["subject"]
        task["focus"] = ref("Encounter", encounter["id"])
    return task


def create_flow_task(ctx: ToolContext, exception_id: str, action_id: str, unit_id: str, description: str,
                     performer_role: str, priority: str, review_task_id: str, approved_by: str,
                     encounter_id: str | None = None) -> dict:
    gateway = ctx.fhir  # FhirGateway for the agent (the Tool Gateway opened it)
    encounter = None
    if encounter_id:
        found = gateway.read("Encounter", encounter_id)
        if found is None:
            raise LookupError(f"Encounter/{encounter_id} not found")
        encounter = found.to_fhir()
    task = flow_task(exception_id, action_id, unit_id, description, performer_role, priority, review_task_id,
                     approved_by, encounter=encounter)
    stored = gateway.create(ctx.stamp(task)).to_fhir()
    return {"task_id": stored["id"], "status": stored["status"], "_refs": [f"Task/{stored['id']}"]}
