"""WP7 in the inpatient journey (spec 6.5 and 7.3): the draftDischargeSummary and draftPatientInstructions steps.

The journey's activities (app/workflows/activities.py) call these in their unit of work. They replace WP4c's
placeholders and return the same `StepResult` (what was written, what the workflow waits for):

- draftDischargeSummary (after the discharge): the discharge_summary agent, acting as itself, drafts the summary
  (summary_agent.draft) and the journey waits for the physician to sign it. With key input missing (no diagnosis,
  no signed discharge medication table) nothing is drafted: the agent opens a sign-off Task for the physician that
  says what is missing, and the journey waits for that Task or for the summary signed in Documentation.
- draftPatientInstructions (after the summary is signed): the patient_instructions agent drafts them from the
  signed summary only; the journey waits for a nurse to sign. Without a signed summary: a Task for the nurse.
- Without the patient's ai_processing consent (or with the agent switched off) the gateway refuses the agent; the
  activity opens the work as a Task (6.3 degradation) and also waits for the document written from the template.

Retries. Temporal may run an activity again after its work committed. The drafts have one id per document and
encounter (`dsum-<encounter>`, `pinstr-<encounter>`) and a retry finds the draft and does not ask the model again;
the Task for missing input is created with arguments built only from the missing list, so the gateway's
idempotency key returns the first Task.
"""

from __future__ import annotations

from app.agents import approvals, registry
from app.agents.library.journey_stubs import StepResult
from app.agents.runtime import runtime
from app.core.context import acting_as_system
from app.core.store import Store
from app.modules.documentation import document as D
from app.modules.documentation import instructions_agent, summary_agent


class DraftUnavailable(RuntimeError):
    """The model did not answer (retried by the workflow)."""


def doc_ref(kind: str, encounter_id: str) -> str:
    return f"DocumentReference/{D.document_id(kind, encounter_id)}"


def _missing_task(run, role: str, kind: str, missing: list[str]) -> StepResult:
    label = {"discharge_summary": "Discharge summary", "patient_instructions": "Patient instructions"}[kind]
    description = (f"{label}: insufficient information, missing {'; '.join(missing)}. Nothing was drafted. "
                   f"Write it in Documentation once that is there (signing it there completes this step), or "
                   f"confirm this task when it is done another way.")
    task = run.tool("createTask", {"encounter_id": run.encounter_id, "code": approvals.WORKFLOW_SIGNOFF,
                                   "performer_role": role, "priority": "urgent", "description": description[:1000]})
    if not task.ok:
        return StepResult(refused=f"{task.reason}: {task.detail}")
    ref = f"Task/{task.output['task_id']}"
    target = doc_ref(kind, run.encounter_id)
    return StepResult(refs=[ref], wait_for=[ref, target], signer=role,
                      detail=f"Insufficient information: missing {'; '.join(missing)}")


def _signed(run, encounter_id: str, ref: str) -> bool:
    """The document already signed final (a final document is never drafted again: the draft tool refuses it)."""
    found = run.tool("getDocuments", {"encounter_id": encounter_id})
    return found.ok and any(f"DocumentReference/{d.get('id')}" == ref and d.get("docStatus") == "final"
                            for d in (found.output or {}).get("resources", []))


def _step(agent_id: str, kind: str, role: str, encounter_id: str, draft) -> StepResult:
    try:
        with acting_as_system(), runtime.start(agent_id, user=None, encounter_id=encounter_id) as run:
            ref = doc_ref(kind, encounter_id)
            if _signed(run, encounter_id, ref):  # signed in Documentation before the journey got here (WP9)
                run.finish(outcome="completed")
                return StepResult(refs=[ref], signer=role, detail=f"{ref.split('/')[1]} already signed final")
            result = draft(run)
            if result.status == "insufficient":
                out = _missing_task(run, role, kind, result.missing)
            elif result.status == "refused":
                out = StepResult(refused=result.reason)
            elif result.status == "unavailable":
                run.finish(outcome="needs_human")
                raise DraftUnavailable(result.reason or "the model is unavailable")
            else:
                found = run.tool("getDocuments", {"encounter_id": encounter_id})
                doc = next((d for d in (found.output or {}).get("resources", [])
                            if d.get("id") == result.document_id), None) if found.ok else None
                ref = doc_ref(kind, encounter_id)
                if doc is not None and doc.get("docStatus") == "final":
                    out = StepResult(refs=[ref], signer=role, detail=f"{ref.split('/')[1]} already signed final")
                else:
                    blocked = bool(doc and D.sign_block(doc))
                    out = StepResult(refs=[ref], wait_for=[ref], signer=role,
                                     detail=f"Draft {ref.split('/')[1]}" + (" (untraceable items: the validator "
                                                                            "blocks signing until they are fixed)"
                                                                            if blocked else "") + f"; the {role} signs")
            run.finish(outcome="refused" if out.refused else "completed")
        return out
    except registry.AgentNotDeployable as e:  # prod mode before the agent passed its eval
        return StepResult(refused=f"not_deployable: {e}")


def discharge_summary_step(store: Store, encounter_id: str) -> StepResult:
    return _step(summary_agent.AGENT_ID, "discharge_summary", "physician", encounter_id, summary_agent.draft)


def patient_instructions_step(store: Store, encounter_id: str) -> StepResult:
    return _step(instructions_agent.AGENT_ID, "patient_instructions", "nurse", encounter_id, instructions_agent.draft)
