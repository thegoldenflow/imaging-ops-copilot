"""Placeholder steps of the inpatient journey (spec 6.5), each run as the agent that will own the real one.

PLACEHOLDERS, NOT THE MODULES. The journey workflow (app/workflows/journey.py) needs these steps now; the modules
behind them come in later work packages, which replace the bodies below and keep the signatures:

| Step                     | Agent                      | Replaced by                                        |
| ------------------------ | -------------------------- | -------------------------------------------------- |
| orderReview              | order_review               | WP6, done: app/modules/med_safety/journey.py       |
| medRecAdmission          | medication_reconciliation  | WP6, done: app/modules/med_safety/journey.py       |
| draftDischargeSummary    | discharge_summary          | WP7, done: app/modules/documentation/journey.py    |
| draftPatientInstructions | patient_instructions       | WP7, done: app/modules/documentation/journey.py    |
| scheduleFollowupCall     | followup_calls             | WP8, done: app/modules/voice_services/journey.py   |

WP6's, WP7's and WP8's steps need the module's own state (the order reviews, the reconciliation; the drafts and
their validators; the follow-up calls), so they live in the modules and the activities call them there;
`StepResult` stays the interface. The WP8 placeholder below is kept only for journeys started before WP8 (versions 1
and 2 of the workflow replay it: `journey.requestFollowupCall`). It takes the run (`AgentRun`) and returns a
`StepResult`: what it read or wrote, and what the workflow waits for (a draft to be signed final, a Task to be
decided). No model is called. Everything goes through the run's tools (the
Tool Gateway), whose consent check also applies: without the patient's consent the call is refused and the workflow
opens a manual Task instead. The arguments are built only from data that does not change afterwards (the finished
stay), so an activity that Temporal retries sends the same arguments and gets the first result back (the
idempotency key), never a second draft.

Agent code: it uses only the run (no FhirGateway, HTTP client or database).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.agents.runtime import AgentRun


@dataclass
class StepResult:
    refs: list[str] = field(default_factory=list)  # resources written (or read, for the record)
    wait_for: list[str] = field(default_factory=list)  # what a person has to sign or decide
    detail: str = ""
    signer: str | None = None
    refused: str | None = None  # the gateway refused (e.g. consent missing): the workflow falls back to a person


def _refused(result) -> str:
    return f"{result.reason}: {result.detail}"


# ---------- WP8 ----------


def followup_consent(run: AgentRun) -> StepResult:
    """Interface: may the patient be called? The follow-up agent needs the patient's followup_call consent (the
    Tool Gateway checks it on every call). Placeholder: reading the encounter is the consent check."""
    context = run.tool("getEncounterContext", {"encounter_id": run.encounter_id})
    if not context.ok:
        return StepResult(refused=_refused(context))
    return StepResult(refs=[f"Encounter/{run.encounter_id}"], detail="Follow-up call consent on file")
