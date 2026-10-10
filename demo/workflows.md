# Demo: durable workflows (WP4c, 3 minutes)

What it shows: spec 6.5. The cross-department flows run as Temporal workflows. A step that fails is retried and its side effect is not repeated; a worker that is killed comes back and the journey continues from where it stopped; a wait for a person is a signal racing a timer that escalates. All side effects go through the Tool Gateway, every retry and skip is audited.

Setup (about 2 minutes, before the audience):

- `docker compose up -d postgres` and `docker compose --profile workflows up -d temporal` (the single-container dev server, SQLite, UI on http://127.0.0.1:8233, namespace `hospital-demo`).
- In `apps/api/.env`: `TEMPORAL_ADDRESS=127.0.0.1:7233`, `LLM_PROVIDER=mock` (no key needed), and demo timers `WORKFLOW_SIGNOFF_TIMEOUT_S=60`, `WORKFLOW_ESCALATION_TIMEOUT_S=60`, `WORKFLOW_FOLLOWUP_DELAY_S=20`, `WORKFLOW_VERIFY_AFTER_S=30`.
- Terminal 1: the API (`uv run uvicorn app.main:app --port 9417`) and the web app. Terminal 2: the worker, on its own so it can be killed: `uv run python -m app.workflows.worker`.
- Press **Reset demo**. Logins: Jordan Lee (operations manager = bed manager), Rosa Pemberton (pharmacist), Dr. Yara Lindqvist (physician, Medicine A), Maya Nakamura (clerk).

Say once, at the start: "Everything is synthetic. AI output is a draft a person decides on; these workflows are the plumbing that carries the decisions between departments."

## Script (web app)

1. **0:00 — The workflow view.** As the operations manager, **Workflows** (Hospital section). The banner: "Temporal online · namespace hospital-demo · 1 worker polling". Three kinds: patient journeys (one per inpatient stay, started by the admission event), capacity exceptions (one per Control Tower exception), low-confidence reviews.
   - "Domain events from the EHR go into an outbox in the same transaction; a dispatcher turns them into workflow starts and signals. The workflow id is the business key, so a resent HL7 message can't start a second journey."

2. **0:20 — A deliberate failure.** In **Deliberate failure (demo)**: step `medRecAdmission`, **2 failures**, **Inject**. "The next two attempts of the MedRec step will fail *after* the draft is written — the worst case for duplicates."
   Then **Start a journey** with a Medicine A inpatient's encounter id (the Control Tower's Medicine A bed grid shows them; or advance the simulator until a patient is admitted and the journey appears by itself). Open it.
   - The timeline: **Triage assist · Done** ("CTAS 2 at ED triage …"), **Order review · Waiting for sign-off** — owner pharmacist, "placeholder until WP6".

3. **0:45 — The pharmacist.** Switch role to the pharmacist, **Review queue**: "Workflow sign-off · Review the admission orders (… placeholder until WP6's OrderReviewService)". **Confirm**. "Her decision is audited and published as `task.decided`; the bridge signals the journey that was waiting for exactly this Task."

4. **1:00 — The failure and the recovery.** Back as the operations manager on the journey (it refreshes by itself):
   - **Admission medication reconciliation · Failed, retrying** — "Attempt 1 of 4 failed: InjectedFailure …; retrying", a second later attempt 2, then **Waiting for sign-off** with "attempt 3". "Temporal retried after 1 s and 4 s. The first attempt had already written the draft; the retries asked the Tool Gateway again with the same arguments, the idempotency key matched, and they got the first draft back."
   - Open the patient's chart (Patients, as the physician): **one** medication reconciliation draft, preliminary, co-signature pending.

5. **1:30 — Kill the worker.** In terminal 2, Ctrl+C (or close it). The banner: "no worker polling: workflows wait (nothing is lost)".
   - As the pharmacist and then the physician, **Sign** the MedRec draft on the chart. The timeline still says **Waiting for sign-off**: nobody runs the workflow, the signals wait in Temporal.
   - Restart the worker (`uv run python -m app.workflows.worker`). Within 10–25 seconds the journey moves: MedRec **Done** "Signed by the physician", **Bed assignment · Done**, **Ward monitoring · Waiting (until discharge)**. "The new process had nothing in memory: Temporal replayed the history, no completed step ran again — no second Task, no second draft."

6. **2:15 — Escalation and the bed manager's controls.** Open a journey that has waited for the pharmacist longer than a minute: **escalated** (a high-priority Task for the pharmacist), after another minute **ops manager told**. On a waiting step, **Skip** with a reason (e.g. Ward monitoring: "discharged on paper"); the step turns **Skipped**. "Only the bed manager and the administrator see Retry and Skip, and each one is in the audit log."

7. **2:35 — The Control Tower loop.** **Control Tower**, an exception: the drawer shows "Workflow capacity-EXC-… · Approval (2/7)". **Approve**: the Tasks first say "queued for the workflow", then their ids appear; an hour later (30 s with the demo timer) the workflow re-checks the occupancy and records the outcome (occupancy then → now, whether the condition still holds, how far the Tasks got), which 6.6 will count. A Medicine A bed's patient card links to that patient's journey.

8. **2:55 — Close.** "Temporal's UI on port 8233 shows the raw histories; the audit log has every retry, skip and deliberate failure."

## Scripted run (no browser)

`scripts/workflow_demo.py` does steps 2–5 against the dev server with a worker process it starts, kills (TerminateProcess) and restarts:

    docker compose --profile workflows up -d temporal
    TEMPORAL_ADDRESS=127.0.0.1:7233 LLM_PROVIDER=mock DATABASE_URL=<a demo database> uv run python scripts/workflow_demo.py

Output of the run on 2026-10-09 (dev server `temporalio/temporal:1.8.3`, demo database seeded Friday 2026-10-09):

```
[   0.5 s] journey journey-stay-01003 for inpatient stay-01003 (Medicine A), Temporal 127.0.0.1:7233 namespace hospital-demo
[   0.5 s] deliberate failure set: the next 2 attempts of medRecAdmission fail after their work committed
[   0.5 s] worker process 18276 started
[  14.2 s] pharmacist confirmed the order review (Task/task-00523)
[  20.7 s] MedRec step done on attempt 3 of 4; MedRec documents in FHIR for the encounter: 1 (DocumentReference/documentrefe-00009)
[  20.7 s] worker process 18276 killed while the journey waits for the MedRec co-signature
[  25.0 s] pharmacist and physician signed; the signals are stored in Temporal, no worker runs: timeline still 'waiting'
[  25.0 s] worker process 11348 started (a new process with nothing in memory)
[  41.5 s] journey resumed after the sign-off: ward monitoring reached 16.5 s after the restart
[  41.5 s] step activities before the kill: journey.context, journey.triageAssist, journey.orderReview, journey.medRecAdmission; after the restart only journey.bedAssignment, journey.predictDuration ran; completed steps run again: none
```

The 16 seconds after the restart are Temporal handing the dead worker's sticky task to the new worker (about 10 s) plus the new process starting.

The acceptance report is `evals/workflows/report.md` (`scripts/workflow_eval.py`; `--real-server` adds this run).
