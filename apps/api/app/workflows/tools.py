"""Tool handler of the workflows (config/tools/createWorkflowTask.yaml), called by the Tool Gateway after its checks.

A workflow's Tasks for people: a sign-off to give, a sign-off that is overdue (escalation), a note to the
operations manager, a failed step to retry or skip, an AI output to review, a NEWS2 flag to respond to, a
follow-up to do by hand. Each Task names its workflow and step (inputs `workflow`, `step`, `kind`), so a person
can open the workflow view from it. Idempotent per workflow, step, kind and text (the gateway's key), so an
activity Temporal retries opens it once.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.store import get_store
from app.ehr import access
from app.ehr.clock import hospital_now
from app.ehr.codes import PRACTITIONER_ROLE, TASK_CODE, concept
from app.fhir.dt import fhir_datetime, ref

if TYPE_CHECKING:
    from app.agents.gateway import ToolContext

CODES = {"signoff": ("workflow-signoff", "Confirm a workflow step"),
         "escalation": ("workflow-escalation", "Sign-off overdue"),
         "notify": ("workflow-notify", "Workflow needs attention"),
         "failed": ("workflow-failed", "Workflow step failed"),
         "review": ("ai-review", "Review an AI recommendation"),
         "news2": ("news2-response", "Respond to a NEWS2 score"),
         "manual": ("followup-manual", "Manual follow-up")}


def _entry(name: str, value: str) -> dict:
    return {"type": {"text": name}, "valueString": value}


def create_workflow_task(ctx: ToolContext, workflow_id: str, step: str, kind: str, description: str,
                         performer_role: str, priority: str = "routine", encounter_id: str | None = None,
                         unit_id: str | None = None, focus: str | None = None, inputs: dict | None = None) -> dict:
    now = fhir_datetime(hospital_now(get_store()))
    code, display = CODES[kind]
    task: dict = {"resourceType": "Task", "status": "requested", "intent": "proposal" if kind == "review" else "order",
                  "priority": priority, "code": concept(TASK_CODE, code, display), "description": description,
                  "authoredOn": now, "lastModified": now,
                  "performerType": [concept(PRACTITIONER_ROLE, access.ROLE_CODE[performer_role])],
                  "input": [_entry("workflow", workflow_id), _entry("step", step), _entry("kind", kind),
                            *(_entry(k, str(v)) for k, v in sorted((inputs or {}).items()))]}
    if encounter_id:
        encounter = ctx.fhir.read("Encounter", encounter_id)
        if encounter is None:
            raise LookupError(f"Encounter/{encounter_id} not found")
        task["encounter"] = ref("Encounter", encounter_id)
        if patient := access.patient_of(encounter.to_fhir()):
            task["for"] = ref("Patient", patient)
    if focus:
        task["focus"] = {"reference": focus}
    elif unit_id:
        task["focus"] = ref("Location", unit_id)
    stored = ctx.fhir.create(ctx.stamp(task)).to_fhir()
    return {"task_id": stored["id"], "status": stored["status"], "_refs": [f"Task/{stored['id']}"]}
