# Durable workflows (spec 6.5)

Three cross-department flows run as Temporal workflows: a step that fails is retried without repeating its side
effect, a worker that dies resumes where it stopped, and a wait for a person is a signal racing a timer, never a
poll. Temporal is optional: without `TEMPORAL_ADDRESS` nothing here runs, the workflow view says "offline" and
every module (Control Tower, signing, review queue, the agents) works as before.

| Workflow | Started by | Steps |
| --- | --- | --- |
| `InpatientJourneyWorkflow`, `journey-<encounter>` | `patient.admitted` for an inpatient stay | triageAssist → orderReview → medRecAdmission → bedAssignment → predictDuration → proposeSchedule → wardMonitoring (NEWS2, until the discharge) → draftDischargeSummary → draftPatientInstructions → 48 h → scheduleFollowupCall → codingStub |
| `CapacityExceptionWorkflow`, `capacity-<exception>` | `exception.opened` (Control Tower) | detect → explainAndRecommend → decision → execute → 1 h → verify → recordOutcome |
| `LowConfidenceReviewWorkflow`, `review-<run>` | `agent.low_confidence` (runtime) | createReviewTask → reviewed → writeCorrectedOutput → appendToEvalSet → triggerRegression |

Files: `model.py` (ids, inputs, step catalogues; pure, imported inside the sandbox), `base.py` (signals, steps,
sign-off waits, escalation), `journey.py`, `capacity.py`, `review.py` (workflow code), `activities.py` (the work,
each in its own transaction), `bridge.py` (events in, starts and signals out), `client.py` (the API's link to
Temporal), `progress.py` (the timeline the view reads), `tools.py` (createWorkflowTask), `faults.py` (the demo's
deliberate failures), `router.py` (`/api/workflows`), `worker.py` (`python -m app.workflows.worker`). The journey's
steps whose modules come later are placeholders in `app/agents/library/journey_stubs.py`, run as the agents that
will own them: orderReview and medRecAdmission (WP6), the discharge documents (WP7), the follow-up call (WP8),
NEWS2 flags (WP9's 8.1 stub), coding (8.1 roadmap card).

## Input resources

- Domain events from the bus (`app/ehr/events.py`), mapped by the bridge: `patient.admitted` / `patient.discharged`
  (encounter class IMP), `flag.raised` (code news2), `document.signed`, `task.decided` (only for a Task or
  document a workflow registered in `workflow_waits`), `exception.opened` / `decided` / `cleared`,
  `agent.low_confidence`. Events carry references only.
- FHIR through the Tool Gateway's read tools (as the step's agent) or FhirGateway as the system actor
  `workflows` (purpose module `workflow_engine`): the encounter, its ED visit (basedOn the bed request), CTAS,
  medications as of the admission, the encounter bundle at discharge, documents, consents (checked by the gateway).
- The Control Tower's snapshot (surgical-duration predictions, occupancy for the 1-hour check) and its exception
  table; the low-confidence review records (`agent_reviews`, de-identified model input and output).
- Start input: the business key and the timer durations (`Timers`: sign-off 24 h, escalation 24 h, follow-up 48 h,
  verify 1 h, from `WORKFLOW_*_S`), so a configuration change never alters a running workflow's history.

## Output schema

- FHIR, only through the Tool Gateway: draft DocumentReferences (medrec, discharge_summary, patient_instructions;
  preliminary), Tasks (`workflow-signoff`, `workflow-escalation` priority urgent, `workflow-notify` priority stat for
  the operations manager, `workflow-failed`, `ai-review`, `news2-response`, `followup-manual`, Control Tower
  `flow-action`), the follow-up Appointment (proposed, booked after a clerk's approval), notes on Tasks. Every write
  carries the gateway's idempotency key (agent, encounter, tool, arguments) and the run's Provenance.
- `workflow_runs` (one timeline per workflow: steps with status pending / running / retrying / waiting / failed /
  done / skipped, owner, attempts, detail, references, escalation level, Tasks, start and finish times; notes;
  steps and notes encrypted) and `workflow.step_completed` events for steps done or skipped.
- `flow_exceptions.outcome` {decision, verified {occupancy before and after, condition present, Task statuses},
  resolved}; the decision's actions get their Task ids from the workflow's execute step.
- `evals/<agent>/cases.jsonl` (a case with source human_review), `evals/<agent>/report.json|md` (the regression
  eval), the corrected output on the run's trace (`human_action`).
- Audit: `workflow_retry`, `workflow_skip`, `workflow_start`, `workflow_fault` (event type write, module
  workflow_engine) besides the tool calls' own events.

## Evaluation and thresholds

No model is judged here; the acceptance criteria are the eval (`scripts/workflow_eval.py` → `evals/workflows/`):
the journey with failures injected into the MedRec activity after its draft was written, retried (1 s, 4 s), leaves
exactly one MedRec document; a stopped worker's journey resumes on a new worker without scheduling a completed step
again; a sign-off timeout of 24 s produces the urgent Task and 24 s later the operations manager's; the
low-confidence loop appends a human_review case and runs the regression; every workflow has a replay test from a
captured history (and the version-1 journey replays through `patched()`). Gate: all criteria pass. The tests run on
Temporal's time-skipping test server with a worker in the test process; `scripts/workflow_demo.py` repeats the
failure and a real worker-process kill against the docker dev server.

## Known failure modes

- Temporal's timers run on the wall clock; the hospital clock is simulated. "48 hours after discharge" is 48 wall
  hours unless WORKFLOW_FOLLOWUP_DELAY_S is set lower for a demo.
- The journey's steps run in the spec's order, one after the other: a patient discharged while the order review
  waits for the pharmacist is noticed only when the journey reaches the ward step (the event waits in the inbox).
  A surgery booked after the journey passed the surgical branch is not predicted.
- Placeholders write template drafts, not the WP6–WP8 modules' content; they are marked in the text and on the
  timeline ("placeholder until WP6").
- A worker that dies holds its workflows' next tasks on its sticky queue for about 10 seconds before Temporal hands
  them to another worker. Temporal's time-skipping test server never hands them over, so the tests run workers
  without a workflow cache (every task replays the whole history).
- Without the worker, starts and signals wait in Temporal and the timeline stays as last recorded; without Temporal
  they wait in the outbox. A demo reset terminates running workflows; a finished journey for an encounter id that
  the regenerated data uses again is replaced by a new run.
