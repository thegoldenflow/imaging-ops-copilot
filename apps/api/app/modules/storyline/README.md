# Hospital storyline (spec 8.2, WP9)

One synthetic patient through the whole hospital in twelve steps, about ten minutes: Wang Xiuying (王秀英), 72,
Mandarin-speaking, on warfarin for atrial fibrillation, falls at home and breaks her hip. The reset can start the same
story with Margaret Ellis, who speaks English (`script.PATIENTS`, the reset's `language`: `zh`, the default, or `en`):
the same course, ids, cast, steps and checks; her name, her record's language, her daughter's and her own lines and
the step texts that name the language (steps 1, 10, 11: `script.steps(language)`) differ. Every step is "AI proposes
→ a person signs → audit trail". The guided page is `/storyline` (web `features/storyline/`); its **Demo mode** puts a presenter's guide
on every page (each step's introduction, the clicks, then "Continue to the next step?"). For a live interview the
guide can have the demo's automation do a step ("Do this step for me"), jump to a step, or present only the
highlights (steps 1, 5, 9, 11) with the automation doing the steps in between (`autoplay.py`). For an
English-speaking audience the Mandarin calls have English subtitles in demo mode: the caller's scripted lines have
fixed translations (`script.py`), the agent's replies their hand-written English (`Turn.gloss`, voice services). The
demo script is `demo/hospital_journey.md`.

## Input resources

- The hospital's EHR as the generator and the day simulator write it, plus the storyline's preset
  (`app/ehr/seed/journey.py`, the spec's `demo/seed_journey.py`): the patient `pat-j0001` and her imaging-centre
  record `PT-J0001` (a preset MPI link: the master patient index is an 8.1 roadmap card), Orthopedics and Surgery
  full with exactly two discharge-ready Surgery patients whatever the weekday, her expected ED visit `ed-j0001`, and
  a plan horizon long enough for the stay and the 48-hour follow-up.
- The storyline's EHR script (`app/ehr/storyline.py`): her course in the simulator's plan (seen, labs with the INR
  of 3.8, the decision to admit, the stay with its admission orders, the OR case, the night's vital signs with a
  NEWS2 of 7, the discharge prescriptions, the discharge), the admitting physician's diagnoses of the stay (her
  known atrial fibrillation and osteoporosis from the problem list, the hypertension note that names lisinopril)
  and the Surgery team's early discharges after the bed manager's approval.
- People's EHR actions on accepted suggestion cards (`app/ehr/accepted.py`): registration, triage, imaging orders.
- The modules of WP5–WP8 for steps 5–11, unchanged in how they work: the Control Tower, medication safety,
  documentation, voice services.

## Output schema

- `GET /api/storyline`: per step the AI proposal (a Task or an exception, its agent and run), the signature or
  approval, the EHR records written and their audit events; the hospital clock, Temporal's state, the journey; the
  patient with the caller's scripted lines (`call_lines`, `followup_lines`, `followup_closing`) and each line's
  English translation when it is not English (`translations`, fixed text: the subtitles).
- `POST /api/storyline/reset` (demo reset + preset; body `{"language": "zh" | "en"}`, none: `zh`; 422 for another
  language, before anything is rebuilt), `POST /api/storyline/advance` (fast-forward to the step's beat). The status
  gives the reset's choices (`languages`) and the story's patient (`patient.story_language`, `name_native`,
  `call_language`: her calls' and instructions' language).
- The demo's automation (`autoplay.py`): `POST /api/storyline/steps/{key}/autoplay` (the current step) and
  `POST /api/storyline/steps/{key}/jump` (every step from the current one up to the one before `key`), 409 when the
  storyline is not set up, a run is going or the step does not continue the story. A background job in the API
  that runs the walk (`walk.py`) through the app in process, as the cast; `GET /api/storyline` shows it as
  `autoplay` (the steps, the one it is doing and as whom, the results, where it stopped and why) and marks each step
  it did (`automated`: who started it, as whom). The presenter's press is a `simulator_event` audit event (action
  `autoplay`); the cast's tokens are registered as the automation's (`app/core/context.py`), so their audit events
  (reason), document signatures (signature extension `note`) and the walk's notes and reasons say "Demo automation on
  behalf of <name> (<role>), started by <presenter>".
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

- The demo's automation runs one job at a time per API process and keeps it in memory (as the fast-forward does):
  after a restart the page no longer shows which steps it did, though the records still say so. A reset stops a
  running job after its step. Its waits are the scripted report's (180 s per step, plus a whole summary draft for
  step 9), so against a stalling live model a step can take minutes; it stops with the walk's reason (the guide shows
  it) rather than hang.
- The automation finishes a step the presenter began from what the records show: a decided card, an approved
  exception, handled discrepancies and the pharmacist's signature, an edited summary, a follow-up call in progress.
  Anything else half-done (an open call-centre call with no registration request yet) is done again from the start.

- The preset balances Surgery to two discharge-ready patients with the Control Tower's model at 07:00 and two hours
  later; a story walked much more slowly than the script (the clock run for hours between steps) can drift from it.
  The preset's warnings are on the page.
- The story's patient boards while Orthopedics and Surgery are full. The bed the first early discharge frees is held
  for her from its cleaning until her admission a minute later (the plan's `bed_holds`), so someone else's admission
  or transfer that comes due in that minute takes another bed or waits. Before the hold such an admission took her
  bed (Thursday 10-15's data), and the walk and the page's Advance stopped at a beat already past (casebook 5.17). A
  bed manager who moves another patient into that bed by hand in that minute still could. When the second freed bed
  is cleaned first, her own retry takes it (Friday's data) and the hold lapses.
- A step with a beat waits (and offers Advance) while the clock is before it, unless the records show its moment
  came: its card, review or call (the beat is a minute or two after the event; with Temporal the journey's timer
  schedules the follow-up call early). For the bed step that is her bed request at the admission decision, not the
  Orthopedics exception: the Control Tower opens that (medium) at 07:00 because the preset fills the unit, and the
  step's AI narrates it only once she boards it (high). Before, the bed step took the 07:00 exception for its
  proposal, showed itself ready and offered no Advance (live run 10-18).
- A step's signature time ("signed by … at") is hospital time, read from the record the person's act wrote (the
  card's Task, the document's signature, the bed manager's decision, the OR booking's `or-adjustment` extension, the
  nurse's action on the follow-up call). The audit events listed under it keep their own time, the wall clock (the
  hash-chained log's). Before, step 7 took its signature time from its audit event (04:31 live on 10-18, among
  hospital times days later), and step 11 from a wall-clock nurse action.
- Past the generated 36-hour plan only the storyline's events, the ward rounds and housekeeping happen: the rest of
  the hospital stands still on day 2 to day 5 (nothing else is planned there).
- With Temporal on, the follow-up call waits for the journey's timer (`WORKFLOW_FOLLOWUP_DELAY_S`): set it to a few
  seconds for the demo, or the call is scheduled 48 hours of wall clock after the instructions are signed.
