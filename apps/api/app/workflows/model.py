"""What the three workflows share with the API and the activities (spec 6.5): ids, inputs, steps, statuses.

Pure Python with no app imports: the workflow code imports it inside Temporal's sandbox.

Workflow ids are business keys, so a start is idempotent: `journey-<encounter id>` (one per inpatient
Encounter), `capacity-<exception id>` (one per Control Tower exception), `review-<run id>` (one per agent run
whose confidence fell below its registry threshold).

Timer durations travel in the start input (`Timers`): changing a setting never changes what a running workflow
replays, and tests pass seconds instead of hours.
"""

from __future__ import annotations

from dataclasses import dataclass, field

JOURNEY = "InpatientJourneyWorkflow"
CAPACITY = "CapacityExceptionWorkflow"
REVIEW = "LowConfidenceReviewWorkflow"
TYPES = (JOURNEY, CAPACITY, REVIEW)
PREFIX = {JOURNEY: "journey-", CAPACITY: "capacity-", REVIEW: "review-"}

# The signals every workflow takes. `event`: a domain event the bridge forwards (a document signed, a Task
# decided, a discharge, a NEWS2 flag, an exception decided or cleared); `control`: retry or skip a step (the
# operations manager or admin, audited by the API). Both carry an event_id; the workflow ignores a repeat.
SIGNAL_EVENT = "event"
SIGNAL_CONTROL = "control"

# Activity retry policy (6.5): 3 retries 1 s / 4 s / 16 s apart; activities that call a privileged tool are not
# retried (a failure becomes a Task for a person).
RETRY_INITIAL_S = 1
RETRY_BACKOFF = 4.0
RETRY_MAX_INTERVAL_S = 16
RETRY_MAX_ATTEMPTS = 4  # the first attempt and 3 retries
PRIVILEGED_MAX_ATTEMPTS = 1

# Step statuses on the timeline
PENDING, RUNNING, RETRYING, WAITING, FAILED, DONE, SKIPPED = (
    "pending", "running", "retrying", "waiting", "failed", "done", "skipped")
FINISHED = (DONE, SKIPPED)


def journey_id(encounter_id: str) -> str:
    return PREFIX[JOURNEY] + encounter_id


def capacity_id(exception_id: str) -> str:
    return PREFIX[CAPACITY] + exception_id


def review_id(run_id: str) -> str:
    return PREFIX[REVIEW] + run_id


def type_of(workflow_id: str) -> str | None:
    return next((t for t, p in PREFIX.items() if workflow_id.startswith(p)), None)


@dataclass
class Timers:
    signoff_timeout_s: int = 24 * 3600  # a sign-off not given by then: a high-priority Task for the signer
    escalation_timeout_s: int = 24 * 3600  # still not given after that: the operations manager is told
    followup_delay_s: int = 48 * 3600  # discharge to the follow-up call
    verify_after_s: int = 3600  # an approved capacity action to the re-check of the occupancy


@dataclass
class JourneyInput:
    encounter_id: str
    patient_id: str | None = None
    timers: Timers = field(default_factory=Timers)
    started_by: str = ""  # the domain event id, or "user:<id>" for a start from the workflow view


@dataclass
class CapacityInput:
    exception_id: str
    timers: Timers = field(default_factory=Timers)
    started_by: str = ""


@dataclass
class ReviewInput:
    review_id: str  # the AgentReview record (app/agents/reviews.py)
    run_id: str
    agent_id: str
    timers: Timers = field(default_factory=Timers)
    started_by: str = ""


@dataclass(frozen=True)
class StepDef:
    key: str
    label: str
    owner: str  # the role responsible for the step ("system" when nobody is)
    kind: str  # auto, signoff (waits for a person), privileged (an approved privileged tool call), wait, timer
    stub: str = ""  # the work package whose module replaces the step's placeholder ("" when the step is final)


JOURNEY_STEPS = (
    StepDef("triageAssist", "Triage assist", "nurse", "auto"),
    StepDef("orderReview", "Order review", "pharmacist", "signoff", "WP6"),
    StepDef("medRecAdmission", "Admission medication reconciliation", "pharmacist", "signoff", "WP6"),
    StepDef("bedAssignment", "Bed assignment", "operations_manager", "auto"),
    StepDef("predictDuration", "Surgical duration prediction", "operations_manager", "auto"),
    StepDef("proposeSchedule", "OR booking check", "operations_manager", "signoff"),
    StepDef("wardMonitoring", "Ward monitoring (NEWS2)", "nurse", "wait", "WP9"),
    StepDef("draftDischargeSummary", "Discharge summary", "physician", "signoff", "WP7"),
    StepDef("draftPatientInstructions", "Patient instructions", "nurse", "signoff", "WP7"),
    StepDef("followupDelay", "48 hours after discharge", "system", "timer"),
    StepDef("scheduleFollowupCall", "Follow-up call", "clerk", "privileged", "WP8"),
    StepDef("codingStub", "Coding suggestion", "clerk", "auto", "8.1 roadmap"),
)

CAPACITY_STEPS = (
    StepDef("detect", "Detect", "system", "auto"),
    StepDef("explainAndRecommend", "Explain and recommend (AI)", "system", "auto"),
    StepDef("decision", "Approval", "operations_manager", "signoff"),
    StepDef("execute", "Execute the approved actions", "system", "auto"),
    StepDef("verifyDelay", "1 hour after execution", "system", "timer"),
    StepDef("verify", "Re-check the occupancy", "system", "auto"),
    StepDef("recordOutcome", "Record the outcome", "system", "auto"),
)

REVIEW_STEPS = (
    StepDef("createReviewTask", "Review task", "system", "auto"),
    StepDef("reviewed", "Human review", "nurse", "signoff"),
    StepDef("writeCorrectedOutput", "Write the corrected output", "system", "auto"),
    StepDef("appendToEvalSet", "Add an eval case", "system", "auto"),
    StepDef("triggerRegression", "Run the regression eval", "system", "auto"),
)

STEPS = {JOURNEY: JOURNEY_STEPS, CAPACITY: CAPACITY_STEPS, REVIEW: REVIEW_STEPS}


def timeline(defs: tuple[StepDef, ...]) -> list[dict]:
    """A fresh timeline: every step pending."""
    return [{"key": d.key, "label": d.label, "owner": d.owner, "kind": d.kind, "stub": d.stub, "status": PENDING,
             "started_at": None, "finished_at": None, "attempts": 0, "detail": None, "refs": [], "waiting_for": None,
             "escalation": 0, "tasks": []} for d in defs]


def step_def(workflow_type: str, key: str) -> StepDef | None:
    return next((d for d in STEPS.get(workflow_type, ()) if d.key == key), None)
