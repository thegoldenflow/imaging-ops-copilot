# Control Tower (spec 7.1)

One screen for the bed manager and the charge nurses: do we have enough beds today, where is the bottleneck, what
do we do next. Risk tier `ops`; decisions by the operations manager (bed manager) or a nurse of the unit (charge
nurse). Web: `apps/web/src/features/control-tower/`. API: `/api/control-tower/...` (`router.py`).

## Input resources

Read through `FhirGateway` only. The boards (`snapshot.py`) are read as the Control Tower itself (registry
entry `control_tower`: hospital-wide data scope over Location, Encounter, Patient, ServiceRequest, Appointment,
Procedure, Observation, Task, Flag), ten searches audited as one record per build:

| Board | FHIR |
| --- | --- |
| ED | Encounter (EMER, active), Observation (CTAS, first vital-sign panel), ServiceRequest (orders, bed requests), Patient, Encounter (IMP, last 12 months) |
| Beds | Location (unit, room, bed, operationalStatus), Encounter (IMP, active), Flag (ALC, fall risk), ServiceRequest (open and recent orders) |
| OR | Appointment (OR rooms, today and the next 24 h), Procedure (actual start and end), Task (pre-op checks, focus the Appointment), Patient |

The patient card is read as the signed-in user (unit scope, reduced views, break-glass). The day simulator's
domain events are not subscribed to one by one: the snapshot is rebuilt when the store's change counter, the
hospital clock or the last domain event changes, which the simulator moves together with its FHIR writes; the
API's hospital loop refreshes after every event drain (every second).

## Output schema

- Boards (`GET /board`): KPIs, ED rows, unit rows, OR cases and rooms, the exception stream, the clock and the
  latest events; shaped per role (`service.board_view`): MRNs only within the viewer's scope, prediction
  explanations without clinical values for the bed manager.
- Exceptions (`exceptions.FlowException`, table `flow_exceptions`): rule, severity, facts, evidence, the
  engine's action menu, the narration, the decision. The narrator's output is the spec's schema (see
  `evals/control_tower/README.md`), stored as an `ai-review` Task (`explainException`).
- Decisions: approve → one `flow-action` Task per approved action (`createFlowTask`, performerType = the
  action's owner role, basedOn the review Task, written by the agent after the recorded approval), reject with
  a reason, defer with a reminder on the hospital clock. Audit: `approve` (accept / reject) on the review Task,
  `tool_call` per Task, `write` for a deferral.

## Evaluation and thresholds

- Rule engine: 20 scripted scenarios, all must match (`evals/control_tower/rules/`).
- Narrator: 30 exceptions from the seeded hospital, 100% schema-valid and 100% resolvable evidence; human rating
  >= 4/5 pending (`evals/control_tower/`).
- Flow models: the spec's baselines and gates on a time split (`apps/api/models/flow/`).

## Known failure modes

- Exceptions follow the rules' thresholds; a condition just under a threshold (94% occupancy, 2 boarders) is not
  raised, and a condition that flips around a threshold opens a new exception each time it returns.
- Facts are stored when an exception opens or changes severity; the narrative quotes those numbers ("facts as of
  HH:MM") while the board shows the live ones.
- A 12-bed ICU swings by about three beds from day to day: the generated ICU holds about 8 patients on a weekday
  morning and fills on its own on some planned days only, so whether the ICU exception shows up without a scenario
  depends on the seeded day. The `icu_surge` scenario forces it (the control bar's default of 6 patients also leaves
  a few boarding in the ED for over 2 hours).
- Pre-op checks stay open until the case starts (the simulator closes them at the start), so a gap exception
  lasts until then unless a nurse completes the Task.
- Approved Tasks are not tracked to completion here; checking the effect an hour later is the
  `CapacityExceptionWorkflow` of 6.5 (WP4c).
