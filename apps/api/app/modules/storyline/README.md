# Hospital storyline (spec 8.2, WP9)

One synthetic patient through the whole hospital in twelve steps, about ten minutes: Wang Xiuying (王秀英), 72,
Mandarin-speaking, on warfarin for atrial fibrillation, falls at home and breaks her hip. Every step is "AI proposes
→ a person signs → audit trail". The guided page is `/storyline` (web `features/storyline/`); its **Demo mode** puts a presenter's guide
on every page (each step's introduction, the clicks, then "Continue to the next step?"; web only, the same
API). The demo script is `demo/hospital_journey.md`.

## Input resources

- The hospital's EHR as the generator and the day simulator write it, plus the storyline's preset
  (`app/ehr/seed/journey.py`, the spec's `demo/seed_journey.py`): the patient `pat-j0001` and her imaging-centre
  record `PT-J0001` (a preset MPI link: the master patient index is an 8.1 roadmap card), Orthopedics and Surgery
  full with exactly two discharge-ready Surgery patients whatever the weekday, her expected ED visit `ed-j0001`, and
  a plan horizon long enough for the stay and the 48-hour follow-up.
- The storyline's EHR script (`app/ehr/storyline.py`): her course in the simulator's plan (seen, labs with the INR
  of 3.8, the decision to admit, the stay with its admission orders, the OR case, the night's vital signs with a
  NEWS2 of 7, the discharge prescriptions, the discharge) and the Surgery team's early discharges after the bed
  manager's approval.
- People's EHR actions on accepted suggestion cards (`app/ehr/accepted.py`): registration, triage, imaging orders.
- The modules of WP5–WP8 for steps 5–11, unchanged in how they work: the Control Tower, medication safety,
  documentation, voice services.

## Output schema

- `GET /api/storyline`: per step the AI proposal (a Task or an exception, its agent and run), the signature or
  approval, the EHR records written and their audit events; the hospital clock, Temporal's state, the journey.
- `POST /api/storyline/reset` (demo reset + preset), `POST /api/storyline/advance` (fast-forward to the step's beat).
- The cards (`stubs.py`), each written through the Tool Gateway by a registered module (`config/agents/`):
  `ed_decision_support` (triage, imaging orders; clinical_ds), and the three 8.1 roadmap stubs on preset data
  `critical_value_routing`, `deterioration_alert`, `coding_assist`.
- With Temporal, her InpatientJourneyWorkflow starts at the registration (`journey-stay-j0001`, version 4 from the
  ED, `JourneyInput.ed_visit`) and strings steps 2–12 together.

## Eval set and threshold

There is no model of its own to judge. The eval is the walk: `scripts/storyline_report.py` drives the twelve steps
through the HTTP API as the cast (`walk.py`) and writes `evals/storyline/report_<mode>.json` and `.md` (each
step's time and result; modes offline, temporal, in_process). Threshold: all twelve steps pass, with Temporal on
and off. The backend test `tests/test_storyline.py` runs the same walk in process; the Playwright test
`apps/web/e2e/hospital-storyline.spec.ts` walks the page itself.

## Known failure modes

- The preset balances Surgery to two discharge-ready patients with the Control Tower's model at 07:00 and two hours
  later; a story walked much more slowly than the script (the clock run for hours between steps) can drift from it.
  The preset's warnings are on the page.
- The story's patient boards while Orthopedics and Surgery are full. The bed the first early discharge frees is held
  for her from its cleaning until her admission a minute later (the plan's `bed_holds`), so someone else's admission
  or transfer that comes due in that minute takes another bed or waits. Before the hold such an admission took her
  bed (Thursday 10-15's data), and the walk and the page's Advance stopped at a beat already past (casebook 5.17). A
  bed manager who moves another patient into that bed by hand in that minute still could. When the second freed bed
  is cleaned first, her own retry takes it (Friday's data) and the hold lapses.
- Past the generated 36-hour plan only the storyline's events, the ward rounds and housekeeping happen: the rest of
  the hospital stands still on day 2 to day 5 (nothing else is planned there).
- With Temporal on, the follow-up call waits for the journey's timer (`WORKFLOW_FOLLOWUP_DELAY_S`): set it to a few
  seconds for the demo, or the call is scheduled 48 hours of wall clock after the instructions are signed.
