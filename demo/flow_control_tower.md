# Demo: hospital flow Control Tower (WP5, 5 minutes)

What it shows: spec 7.1. One screen answers three questions for the bed manager and the charge nurses: do we have enough beds today, where is the bottleneck, what do we do next. Three boards (ED, beds, OR) read from the synthetic hospital's FHIR store through the gateway, four prediction models, a rule engine that raises exceptions, an AI narrative with recommended actions that a person approves into Tasks, and a drill-down whose patient card follows the 6.3 roles. The day simulator (WP3) drives it all from the control bar.

Setup: `docker compose up -d postgres`, start the API and the web app (or the Playwright setup: API on 9001 with `LLM_PROVIDER=mock`). Press **Reset demo** first: the hospital starts at 07:00 on the seeding day (the nearest weekday at a weekend). No API key needed: the mock model writes the narrative from the rule engine's facts. Logins: Jordan Lee (operations manager = bed manager), Jonah Eriksen (nurse, Medicine A), Casey Brooks (administrator).

Say once, at the start: "Everything here is synthetic. The models' numbers show that the pipeline works, not clinical performance; AI output is a draft a person decides on."

## Order of the injected events

| When (hospital time) | Control bar | What it causes |
| --- | --- | --- |
| 07:00 | nothing yet | Orthopedics and Surgery full, Medicine A and B at 97%, ICU 11 of 12; a hip replacement in OR-04 predicted 42 min over its booking, Dr. Novak's hysterectomies in OR-02 53–79 min over, pre-op gaps on the 07:28 and 08:00 lists |
| 07:00, after approving | **ICU surge**, 6 patients (the default), **Inject** | six critically ill arrivals, 3 minutes apart; ICU (1 free bed) is full by about 09:30 and the rest board in the ED |
| 07:00 → about 10:30 | rate **5 h / min**, **Run** (about 40 s), then **Pause** | "Intensive Care Unit at 100% occupancy" (high) at about 09:30; "5 ED boarders waiting over 2 h" at about 10:30 |
| about 10:30 | **OR overrun**, **Inject** (optional) | the next elective case runs 50 min over and pushes its room's list |
| any time | **08:00 tomorrow** | the rest of the planned day in 30-minute steps in the background (about 1,300 events, 10–15 s): progress bar, boards moving |

## Script (web app)

1. **0:00 — The board at 07:00.** Sign in as the operations manager, **Control Tower** (first in the Hospital section; Home has the storyline too).
   - Top: the control bar (hospital time "Fri 07:00 · paused · hospital time"), the headline ("15 open exceptions (9 high). Most urgent: OR-04: Total replacement of hip predicted 42 min over."), the latest domain events.
   - Left, **ED**: patients with CTAS, waiting time, open orders, the target unit, and the admission probability with its three main factors ("Admission probability 0.02: CTAS 4, respiratory rate 19, temperature 36.3 °C" — the bed manager sees factor names only, the clinical values are for nurses and physicians). The KPI "Predicted wait, next hour" is the ED-wait model.
   - Middle, **Beds**: occupancy per unit, free and housekeeping beds, ALC, expected discharges in 24 h (the discharge model, "ready" = probability ≥ 0.7), ED patients waiting for the unit, and the **net gap** = expected admissions − expected discharges − free beds.
   - Right, **OR**: the room lanes (booking outlined, prediction filled, the block ends at 16:00), each case's booked vs predicted minutes and its pre-op checklist (consent, fasting, labs, blood group).
   - "Every number on this screen comes from FHIR through the gateway, read as the Control Tower's own registry entry; the whole board is one audited read."

2. **0:50 — The most urgent exception.** Click the first card in the **Exceptions** strip (OR-04 hip replacement).
   - The drawer: **AI-generated** and **Not evaluated** ("the narrator's human rating is still pending, so demo mode flags it and prod mode would switch it off — the rules, boards and drawer work without it").
   - "From the rule engine": 105 min booked, 147 predicted, the room's list predicted to end at 18:40. "The model may only use these numbers; if it writes one the engine did not produce, or says something was done, the guard sends it back once and then shows the engine's own sentence."
   - Recommended actions from the engine's menu, each with an owner role: re-sequence the room (bed manager), tell Orthopedics the patient comes later (charge nurse), confirm the duration with the surgeon, Dr. Elena Marsh (physician). Evidence: Appointment, Location, Encounter references, "checked in the EHR before they are shown".
   - **Approve 2 actions → Tasks** (untick the third): "Approved by Jordan Lee", Task ids for the bed manager and the charge nurse. "The AI wrote a recommendation; I approved it; the Tasks are written by the agent only after my approval is recorded, and they go to the roles that do the work."

3. **1:40 — An ICU surge.** In the control bar: scenario **ICU surge**, 6 patients, **Inject**; rate **5 h / min**, **Run**.
   - "ICU runs at 11 of 12 this morning, as a real one does." Watch the ED fill (six CTAS 1–2 arrivals), ICU go to 100%, the KPI "Waiting for a bed" climb. "The board refreshes within two seconds of each simulator step: the API ticks the hospital every second and rebuilds the snapshot when the store changes."
   - At about 09:30: "Intensive Care Unit at 100% occupancy" (high). At about 10:30: "5 ED boarders waiting over 2 h". **Pause**.
   - Open the boarders exception: the facts list the boarders and their waits, the menu offers the ED surge plan, a nurse for the boarders, early discharge rounds. **Defer** one hour: "it comes back as a reminder when the hospital clock passes 11:30". Or **Reject** with a reason (required).

4. **2:50 — Drill-down and roles.** Click **Medicine A** in the Beds column: the bed grid (occupied, free, housekeeping, closed; a green ring = discharge-ready; ALC tags). Click an occupied bed.
   - As the bed manager the card shows **MRN, bed, admitted, expected discharge** and a box "Hidden for your role": name, age, reason for admission, flags, vital signs, discharge prediction. "That is 6.3: the operations manager sees a patient as an MRN and a bed. The card is read through the gateway as me, so the server hides it, not the browser."
   - **Switch role** → the nurse (Medicine A) → Control Tower → Medicine A → the same bed: name, age, reason for admission, flags, vital signs at 06:00, the discharge prediction with its three main factors ("Discharge within 24 h 0.xx: ..."), a link to the chart. In the ED column the MRNs are hidden ("not your unit").
   - (Optional) as the physician: ICU → a bed without an MRN → "Outside your units" with the way to break-glass. (Some ICU patients are Medicine A admissions under the demo physician, ICU first and the ward later; their beds show the MRN and the card opens.)

5. **3:50 — Fast-forward.** As the operations manager: **08:00 tomorrow**. The progress bar runs while the boards move through the afternoon, evening and night (cases finish, discharges, housekeeping). "This is 1,300 HL7 messages turned into domain events, in 30-minute steps, each committed on its own, so the screen follows along."

6. **4:30 — The audit trail.** Switch to the administrator, **Audit log**, tab **Approvals**: the `accept` on the review Task by the operations manager, module `control_tower`; tab **Tool calls**: `createFlowTask` per Task. "Chain intact" at the top. (Optional: **AI agents** → the `control_tower` run: inputs, model, prompt `control_tower@1`, tool calls, Provenance.)

Close with the operations director's line (8.3): "The control tower answers three questions: do we have enough beds today, where is the bottleneck, and what do we do next — with a recommended action the charge nurse approves in one click."

## Script (terminal, 1 minute)

`cd apps/api && uv run python scripts/control_tower_demo.py` (rolled back at the end; `--commit` keeps it). Output on the data seeded 2026-10-09 (a Friday), mock model:

```text
== 1. The hospital at the start of the day ==
hospital time Fri 07:00; board built in 656 ms (refresh 1125 ms incl. rules)
ED      3 patients, 1 waiting to be seen, 0 waiting for a bed (0 over 2 h), predicted wait next hour 50 min, 0 at risk of leaving
Beds    98% occupied (129/132), 3 free, 0 cleaning, net gap -25 (1 in - 23 out - 3 free)
        ORTH  100% 24/24  free  0  cleaning 0  ALC 4  out 5 (0 ready)  in 1 (ED 0)  gap -4
        ICU    92% 11/12  free  1  cleaning 0  ALC 0  out 0 (0 ready)  in 0 (ED 0)  gap -1
OR      12 cases (1 done, 0 in the OR), block utilisation 70%, 4 at risk of cancellation
15 open exceptions:
  [high] OR-04: Total replacement of hip predicted 42 min over
  [high] OR-02: Hysterectomy predicted 53 min over
  [high] OR-04 07:28: pre-op incomplete (1)
  [med ] Orthopedics at 100% occupancy
  [med ] Surgery at 100% occupancy
  [low ] Medicine A at 97% occupancy
  ...

== 2. The most urgent exception: AI narrative, approve -> Tasks -> audit ==
  narrative (model, NOT EVALUATED): Total replacement of hip in OR-04 is predicted to take 147 minutes against 105 booked, 42 minutes over. The room's list is predicted to end at 18:40.
  - [operations_manager] Ask the OR coordinator to re-sequence OR-04's list after this case
  - [nurse] Tell Orthopedics the patient will come about 42 min later than booked
  - [physician] Confirm the expected duration with Dr. Elena Marsh
  evidence (resolved in FHIR): Appointment/appt-000562, Location/OR-04, Encounter/stay-01125, Appointment/appt-000567
  approved -> Task task-00095 for operations_manager: Ask the OR coordinator to re-sequence OR-04's list after this case
  approved -> Task task-00096 for nurse: Tell Orthopedics the patient will come about 42 min later than booked
  audit: approve/accept by U-OPS on Task task-00094

== 3. Inject an ICU surge of 6 critically ill arrivals, run 3.5 hours ==
injected 6 patients (ICU had 1 free beds)
  +60 min -> 08:00: board and rules refreshed 921 ms after the step began (snapshot 296 ms); ICU 92%, 2 boarding (0 over 2 h)
  +90 min -> 09:30: board and rules refreshed 1014 ms after the step began (snapshot 313 ms); ICU 100%, 5 boarding (0 over 2 h)
  +60 min -> 10:30: board and rules refreshed 1000 ms after the step began (snapshot 296 ms); ICU 100%, 5 boarding (5 over 2 h)
10 open exceptions:
  [high] 5 ED boarders waiting over 2 h
  [high] Intensive Care Unit at 100% occupancy
  ...

== 5. Fast-forward to 08:00 tomorrow, in steps of 30 hospital minutes ==
1337 domain events to Sat 08:00 in 11.1 s
```

The "refreshed" times include the simulator step itself (60–90 hospital minutes of events in one transaction); the snapshot is about 0.25–0.45 s. In the API, a running simulator commits every second and the board polls every second, so a change reaches the screen in under 2 s (Playwright checks it: `e2e/control-tower.spec.ts`).

## What to say when asked

- "Where is LightGBM?" — The spec names it; it is not a dependency here, and adding a library family needs the owner's sign-off. scikit-learn's histogram gradient boosting is the same kind of model; the explanation is a path attribution on its trees, exact to the prediction. The report and README are in `apps/api/models/flow/`.
- "Does the AI move patients?" — No. It writes a recommendation into a review Task. Only a person's approval creates Tasks, and a bed move is a separate privileged tool that needs its own recorded approval (WP4b).
- "How full is ICU normally?" — On a weekday morning the generated 12-bed ICU holds about 8 patients (8–11 on the five hospital weekdays; about 64% around the clock over the 60-day history), and on some planned days it fills on its own (Friday evening on this data), which raises the ICU exception without a scripted surge. The surge is the pressure test on top. See docs/data-model.md, "Generator calibration".
