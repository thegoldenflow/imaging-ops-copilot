# Interview narrative (spec 8.3, 6.7)

The talk tracks for three audiences, each sentence paired with the screen that proves it. They are spoken, not shown:
the app's **Safety tiers** page carries only the tiers, guardrails and boundaries, built from the registries
([safety-tiers.md](safety-tiers.md) is the same content as a document). Order from the spec (8.4): the business
workflows first for the operations and medical directors, the platform depth second for the technical lead.

## Positioning (6.7)

- "One healthcare AI operations platform running many governed clinical and operational workflows."
- "The same platform primitives power dozens of workflows." Show it: **Safety tiers**, the counts at the top (the
  registered agents and modules and the tools by side-effect level), then **Roadmap**: fourteen modules the same
  registry, gateway, review queue and evals would carry next.
- Definition of done, every module: code, unit tests, an eval set with a threshold, demo data and a demo script; it is
  registered with a risk tier and a signer before it may write.

## Medical director (safety and accountability)

| Say | Show |
| --- | --- |
| "Every function in the system is registered with a risk tier and a required signer. Nothing becomes part of the record without a human signature, and the gateway enforces that — not the UI." | **Safety tiers** (the three tiers with the registered entries and their signers); in the storyline, step 9: the physician's signature turns the draft final; **Audit log** → Approvals |
| "The documentation agent can't invent a medication: every drug name in a draft has to trace back to a FHIR resource, or the validator blocks the signature." | Storyline step 9 (**Documentation**): lisinopril came from a note, not an order; the red item blocks **Sign** until it is removed; the source panel highlights each line's evidence |
| "Anything that touches diagnosis or treatment is flagged as clinical decision support. I treat that as regulated territory — the demo proves the plumbing, not clinical performance." | **Safety tiers**, the `clinical_ds` column (flag, rank, draft; never block or execute; SaMD note); **Medication safety**: rules block, the AI only comments, the pharmacist decides |

Good follow-ups: break-glass (Home → "Break-glass for an ICU patient": reason, 4-hour grant, red banner, admin review),
consent degradations (no AI consent → the template, no follow-up-call consent → a nurse calls by hand), the
de-identification eval (`evals/deid/`, recall ≥ 0.98 on 200 notes with Chinese names and mixed date formats).

## Operations director (beds, staff and money)

| Say | Show |
| --- | --- |
| "The control tower answers three questions: do we have enough beds today, where is the bottleneck, and what do we do next — with a recommended action the charge nurse approves in one click." | **Control Tower** as the operations manager: the three boards, an exception with the AI narrative written from the engine's facts, **Approve** in the action drawer, the Tasks it creates (storyline step 5) |
| "Surgical duration and discharge readiness are forecasting problems. I've built the same forecasting pipeline for warehouse inventory; beds are inventory with a heartbeat." | The OR board: booked 90 against predicted 115 minutes with the three reasons, past the block end (storyline step 7); the discharge-readiness ring in a unit's drill-down; `apps/api/models/flow/report.md` (the four models against the spec's baselines) |
| "The follow-up call runs in the patient's own language and escalates anything that looks like a red flag to a nurse. It's conservative by design: it would rather over-escalate than miss one." | **Follow-up calls** (storyline step 11): the call in Mandarin, "the wound keeps leaking" escalates by rule before any model, the nurse's queue puts it on top; `evals/followup/` (red-flag recall 1.0, over-escalation about a quarter of the clear calls) |

Good follow-ups: the simulator control bar (ICU surge, ED surge, OR overrun at 1–15 hospital hours per minute), the
call centre's one-click confirmation for a clerk, cost per agent on **AI Ops**.

## Technical lead (second round)

| Say | Show |
| --- | --- |
| "Every agent runs through a registered tool gateway with four side-effect levels; privileged tools need a recorded human approval before they execute, and every run leaves a trace and a FHIR Provenance." | **Safety tiers** → the second axis (tools per level, the tier × level matrix: `ops` agents reach privileged tools too); **AI agents** (admin): a run's trace; `apps/api/app/agents/README.md`; `evals/injection/` (38 attack cases, none executed) |
| "The twelve-step patient journey is one durable Temporal workflow. Kill the worker mid-step, restart it, and it resumes without duplicating a single side effect — same pattern I used for freight arbitration." | **Workflows** → the patient's journey (`journey-stay-j0001`) with its timeline; `demo/workflows.md` (a deliberate failure, a worker killed and restarted); `evals/workflows/report.md` (one MedRec document after two injected failures) |
| "One command runs every agent's eval set; a prompt change can't merge without an eval run attached." | `uv run python -m app.aiops.evalrun`; **AI Ops** → Eval Center (a failing case and its trace in three clicks) and Compare (`patient_message_triage@2` regresses one case); the prompt gate in `tests/test_prompt_gate.py`; [evals/README.md](../evals/README.md) |

Good follow-ups: the FHIR gateway's write allow-list (drafts only, `preliminary` until the signing service), the
idempotency key `sha256(agent, encounter, tool, args)`, the release gate in prod mode, model ids only in the
environment, the de-identification token map that never leaves the server.

## Honest boundaries (say them before being asked)

- All data is synthetic: the hospital comes from this project's own Synthea-style generator and the imaging centre
  from its seeded generator. There is no real patient anywhere.
- Model metrics show that the pipeline works, not clinical performance. A real deployment would retrain on the
  hospital's own history, validate retrospectively, then run prospectively in shadow mode.
- Every committed eval number is a mock-model baseline; runs on a real model (Claude or Gemini) and the human rating
  sheets (Control Tower narratives, documentation) are still to be done ([evals/README.md](../evals/README.md)).
- The pharmacy rule tables are simplified demo versions; production needs a commercial drug knowledge base.
- There is no real HL7 interface engine: ADT, order and result messages come from the day simulator. A real system
  replaces only the event source.
- Also true and worth saying if asked: speech runs in the browser (no telephony), SMS and email are mocks, SMART on
  FHIR authentication is defined but not implemented, and the three 8.1 stubs in the storyline run on preset data.

## Likely questions

| Question | Short answer | Where |
| --- | --- | --- |
| What stops a hallucinated drug reaching a patient? | The traceability validator blocks the signature; the patient instructions copy facts from the signed summary and the model only picks phrase codes | `apps/api/app/modules/documentation/README.md` |
| What if someone pastes "ignore your rules" into a message? | It sits in an untrusted block; the gateway authorises from the registry and the caller, never from model output; 38 injection cases, zero calls executed | `evals/injection/README.md` |
| How would you take this live? | Real model runs and human ratings, a hospital's data for retraining and retrospective validation, shadow mode, a commercial drug knowledge base, an HL7/FHIR interface engine, SMART on FHIR, telephony | [PROGRESS.md](PROGRESS.md) limits per work package |
| Why Temporal instead of a queue? | Sign-offs wait for hours or days, steps must not repeat side effects, and a restart must resume; Temporal's history plus the gateway's idempotency key give that, and the workflow view still works when Temporal is down | `apps/api/app/workflows/README.md`, [RATIONALE-db-temporal.md](RATIONALE-db-temporal.md) |
| Why FHIR? | The AI layer sits beside an EHR and must not replace it: reads are FHIR, writes are drafts, so swapping the synthetic store for a real EHR changes the backend, not the modules (HAPI FHIR already runs as a second backend) | [data-model.md](data-model.md) |
