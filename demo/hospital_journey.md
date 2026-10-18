# Demo: one patient through the whole hospital (WP9, spec 8.2, under 10 minutes)

What it shows: one synthetic patient from a phone call to the coding of her stay, through every hospital system of
phases 6–8. Wang Xiuying (王秀英), 72, speaks Mandarin, takes warfarin for atrial fibrillation, falls at home and
breaks her left hip. Every step follows the same pattern: **the AI proposes, a person signs, and the audit trail
records it**. The guided page **Hospital storyline** (`/storyline`) shows each step's click path, the sentence to
say, and what the screen should show. The person you are playing is in the top right. Each step has a one-click
switch to the person who plays it, so the audience sees what that role may see and do.

Everything is synthetic: the patient, her daughter, the staff and the hospital. Steps 4, 8 and 12 are **roadmap
items (spec 8.1)** that run on preset data with no real logic. The page and the review cards mark them
"Roadmap (8.1): preset data".

## Setup (about 3 minutes, before the audience)

- **Database:** `docker compose up -d postgres`, then `uv run alembic upgrade head` in `apps/api`.
- **No key needed:** in `apps/api/.env`, `LLM_PROVIDER=mock` uses the recorded mock answers. A real provider works too, because every model call goes through the LLM gateway.
- **API and web app:** start the API (`uv run uvicorn app.main:app --port 9417`) and the web app (`npm run dev`).
- **Reset to the start:** sign in as the administrator (Casey Brooks), open **Hospital storyline** and press **Reset to the start**. This takes about a minute: the demo data is rebuilt and the storyline's preset is planted. The hospital clock then stands at 07:00 on the demo day. Wang Xiuying is expected in the ED but has not arrived. Orthopedics and Surgery are full, and exactly two Surgery patients are ready for discharge. The reset is `POST /api/storyline/reset`: a demo control like the header's Reset demo, available to any signed-in user and audited.
- **Temporal off (offline mode, the default):** leave `TEMPORAL_ADDRESS` unset. The story is walked step by step with the same cards, signatures and records. The page's Temporal card says "Temporal off: the story runs step by step (offline mode)".
- **Temporal on:** for a single process, use `TEMPORAL_ADDRESS=127.0.0.1:7233`, `TEMPORAL_NAMESPACE=hospital-demo` and `TEMPORAL_WORKER_IN_API=1`. To run the worker separately, start `docker compose --profile workflows up -d temporal` and then `uv run python -m app.workflows.worker`. Set the demo timers too: `WORKFLOW_FOLLOWUP_DELAY_S=20`, plus `WORKFLOW_SIGNOFF_TIMEOUT_S=600` and `WORKFLOW_ESCALATION_TIMEOUT_S=600` so that the escalations stay quiet while you talk. Her InpatientJourneyWorkflow (`journey-stay-j0001`) starts when the clerk registers her in step 1. Steps 2–12 are its activities, the signatures are its signals, and the follow-up call waits for its timer after the instructions are signed. The page's Temporal card links to the journey's timeline.
- **The cast:** the page switches to each person for you. Each switch is a demo login of that user, and the role's permissions apply from that moment.

| Part | User | Role (6.3) |
| --- | --- | --- |
| Registration clerk; health-records coder in step 12 | Maya Nakamura `U-CLER-01` | clerk |
| ED triage nurse | Eun-ji Abernathy `U-NURS-01` | nurse, ED |
| ED physician | Dr. Dmitri Salazar `U-DOC-01` | physician, ED |
| Bed manager and OR coordinator | Jordan Lee `U-OPS` | operations manager |
| Pharmacist | Rosa Pemberton `U-PHAR-01` | pharmacist |
| Orthopaedic surgeon, her attending | Dr. Victor Osei `U-SURG-02` | physician, Orthopedics |
| Surgery ward nurse (she recovers in a Surgery overflow bed) | Vera Uchida `U-NURS-13` | nurse, Surgery |
| Presenter (reset, overview) | Casey Brooks `U-ADMIN` | administrator |

Say once, at the start: "Everything here is synthetic. In every step the AI makes a suggestion or a draft, a named
person decides, and the audit trail records both. Three steps are roadmap items on preset data, and they say so."

**Demo mode (for an audience).** On the storyline page press **Demo mode**: a guide panel stays in the corner of
every page. For each step it first shows the introduction (what the step shows, who plays it, the sentence to say,
what the audience will see); **Start** switches to that person and opens their screen (and starts the clock's
fast-forward when the step waits for time). It then lists the clicks, gives the caller's lines as **Say it** buttons
(no copy and paste) and says whose signature the step waits for; the guide never signs, the presenter clicks the
real button. When the step is done it shows the trail and asks **Continue to the next step?** (**Not yet** pauses
it; **Resume** goes on). Fold the panel with its arrow when it covers a button. Exit with its cross.

**Advance the clock.** Steps 4, 5, 6, 8, 9 and 11 wait for hospital time to pass: the INR result, the admission
decision, a free bed, the first night after surgery, the discharge, and 48 hours after discharge. Press **Advance the
clock** on the step. The hospital's day simulator runs to that moment in the background and the page follows its
progress. A fast-forward takes from a few seconds (the INR result) to one or two minutes (the discharge on day 3, the 48 hours after it), so press it while you are still talking about the previous step.

## Script (web app, Hospital storyline)

Each step card on the page has a **Switch to … and open …** button, the click path, a **Say** box, a **The screen
should show** list and a three-column trail: **AI proposed**, **Signed by**, **Audit**. The trail fills in as soon
as the step is done, and the step's badge turns **Done**.

1. **0:00 — The call (7.4 call centre, clerk).** **Switch to Maya Nakamura**, then on **Call centre** press **Start call**. Click the daughter's two phrases on the storyline page to copy them, and paste each one as a turn:
   "你好，我妈妈在家摔倒了，救护车正在把她送到你们急诊。" and "我是她女儿。她叫王秀英，……出生，健康卡尾号4821。"
   - The screen shows the reply in Chinese, the call's language switched to 简体中文, and the identity check "Verified" (name, birth date and the last four digits of the card).
   - Say: "Her daughter calls in Mandarin. The call centre answers in Chinese, checks who the patient is and pre-fills the ED registration. Nothing is recorded yet."
   - Under **Requests**, the card **ED registration · pre-filled from the call** (AI) shows Wang Xiuying, MRN 40720001, complaint fall, arriving by ambulance. Press **Confirm**, then **End call**: the summary begins "Call from the patient's daughter".
   - The EHR registers the visit `ed-j0001` (EMER) with the clerk as the operator. Permissions: the clerk sees that she is registered, never why.

2. **1:00 — Triage (7.1, triage nurse).** **Switch to Eun-ji Abernathy**, then **Review queue**, then **Triage suggestion: CTAS 2**.
   - The card lists the reasons for CTAS 2: SpO₂ 91%, heart rate 112, age 72 on warfarin after a fall. It also reads "Admission probability 0.82: CTAS 2, SpO₂ 91%, heart rate 112".
   - **Accept.** The EHR records the triage (CTAS, vital signs, a fall-risk flag).
   - Say: "The nurse accepts the suggestion. The Control Tower now predicts admission at 0.82, with its three main reasons."
   - Switching to the bed manager shows the same ED row as an MRN with the factor names but not the values.

3. **1:50 — Imaging orders (imaging pipeline, ED physician).** **Switch to Dr. Dmitri Salazar**, then **Review queue**, then **Imaging orders: hip X-rays and CT hip**.
   - The imaging centre's own triage and protocol agents propose urgency P2 with its reason and the protocols XR-HIP-2V and CT-HIP-FX.
   - **Accept.** Two ServiceRequests (asap) are placed, and an ORM^O01 goes to the imaging centre.

4. **2:35 — INR 3.8 (8.1 roadmap stub, ED physician).** **Advance the clock** (the INR result comes back about 45 minutes after the orders), then **Review queue**, then **Critical value: INR 3.8**.
   - The card carries the **Roadmap** badge: a preset threshold of INR ≥ 3.5 before surgery, no real routing logic.
   - **Acknowledge.** Say: "This is a roadmap item: the routing runs on a preset rule, and the architecture for the real one is in the data model."

5. **3:10 — No orthopaedic bed (7.1 Control Tower, bed manager).** **Advance the clock** to the admission decision, then **Switch to Jordan Lee**, then **Control Tower**.
   - The exception card reads **Orthopedics at 100% occupancy** (high). Its narrative is written by the AI from the rule engine's facts as soon as the step's **Advance** brought the exception (the storyline page's step shows "AI working", then when it was generated), and its actions include **Early discharge rounds on Surgery (2 ready)**.
   - Choose that action and press **Approve**. Tasks go to the owner roles, and the Surgery team discharges the two ready patients early (scripted).
   - **Advance the clock** until she has a bed: the first Surgery bed the discharges free, after housekeeping, is hers.
   - Permissions: the bed manager decides for the whole hospital but sees MRN and bed only ("Hidden for your role"). The Surgery nurse sees the clinical card for the same bed.

6. **4:15 — Admission MedRec (7.2, pharmacist and surgeon).** **Switch to Rosa Pemberton**, then **Medication safety**.
   - Her naproxen order shows a **major** interaction with warfarin. **Request a change.**
   - In **Admission reconciliation**: warfarin is held, and the home amlodipine is missing from the admission orders (an omission). Handle each discrepancy and press **Sign**.
   - **Switch to Dr. Victor Osei** and **Co-sign**. The record `medrec-stay-j0001-admission` is final.

7. **5:20 — The OR case (7.1 OR board, OR coordinator).** As Jordan Lee, **Control Tower**, then the **OR list**, then her case.
   - It is booked for 90 minutes and predicted at **115**. The pill **block overtime +N min** shows that it would run past the 16:00 block end.
   - **Adjust booking to 115 min.** The booking becomes 115 minutes, recorded through the FHIR gateway and audited. Only the operations manager may change a booked case.

8. **6:00 — The first night (8.1 roadmap stub, ward nurse).** **Advance the clock** to 02:00 after the surgery, then **Switch to Vera Uchida**, then **Review queue**.
   - The **NEWS2 7** card carries the **Roadmap** badge: the score is preset, not computed. A NEWS2 flag is on her stay.
   - **Record the response.**

9. **6:35 — Discharge summary (7.3, surgeon).** **Advance the clock** to the discharge, then **Switch to Dr. Victor Osei**, then **Documentation**, then her stay, then **Draft with AI**.
   - Every line of the draft cites its source. One medicine, **lisinopril**, came from a note rather than an order: the validator marks it red ("no matching source") and **Sign** is blocked.
   - **Remove** it, then **Sign**. `dsum-stay-j0001` is final.

10. **7:45 — Patient instructions (7.3, ward nurse).** **Switch to Vera Uchida**, then **Documentation**, then her patient instructions.
    - They are in 简体中文 by default, from her record. The warfarin section says 每周吃的绿叶蔬菜量要大致相同 (eat about the same amount of green vegetables each week), followed by the wound red flags.
    - **Sign.**

11. **8:30 — Follow-up call 48 hours later (7.4, ward nurse).** **Advance the clock** 48 hours (with Temporal on, the journey's timer schedules the call). Then **Follow-up calls**, her call, **Place call**.
    - Answer as the patient with the two phrases from the storyline page: her birth date, then "还行，就是伤口一直在渗液，纱布都湿透了。"
    - The rule layer escalates at once: a red flag for heavy wound drainage, quoting her words, and a Task `followup-escalation` (urgent) for the nurses of her last unit.
    - **Call back.** The call never gives medical advice.

12. **9:30 — Coding (8.1 roadmap stub, coder).** **Switch to Maya Nakamura**, then **Review queue**, then **Coding suggestion**.
    - The card suggests S72.0 fracture of neck of femur (ICD-10-CA), with W19, I48 and Z92.1, and the CCI 1.VA.53 rubric. It carries the **Roadmap** badge.
    - **Accept.**

**9:55 — Close.** Back on the storyline page, all twelve steps show **Done**, each with its AI proposal, its signer and its
audit events. Say: "Twelve suggestions, twelve people's decisions, and the records written back to the EHR by the
people who are allowed to write them."

## The two paths

- **Offline (no Temporal).** The storyline service (`app/modules/storyline/service.py`) reacts to the story's domain events. It writes the next card, for example the triage card after the registration or the critical value after the INR result, and runs the scripted Surgery team, discharge MedRec and admission note. Each step is walked by hand with the same cards and records.
- **Temporal on.** The same events also start and signal `InpatientJourneyWorkflow` (version 4 from the ED, `workflow.patched("journey-ed-phase")`). Triage, imaging orders, the critical value and the coding suggestion are sign-off steps that wait for the card's decision. The bed assignment waits for the admission. MedRec, the order review, the documents and the follow-up call are the journey's steps from WP4c–WP8. The follow-up comes after the 48-hour timer, compressed by `WORKFLOW_FOLLOWUP_DELAY_S`.

## The roadmap stubs (spec 8.1)

These run on preset data, carry the Roadmap badge, and are registered agents with their own registry entries:

| Step | Agent (`config/agents/`) | Preset data | What a real one needs |
| --- | --- | --- | --- |
| 4 | `critical_value_routing` | INR threshold table (≥ 3.5 before surgery) | the lab's critical-value list, on-call routing, read-back |
| 8 | `deterioration_alert` | NEWS2 7 in the flowsheet | the score computed from charted vital signs, response escalation |
| 12 | `coding_assist` | ICD-10-CA S72.0, W19, I48, Z92.1; CCI 1.VA.53 | a grouper, the coding standards, the coder's query workflow |

The patient's link to her imaging-centre record (`PT-J0001`) is a preset MPI record, labelled "MPI link preset
(8.1)" on the page.

## Scripted run (no browser)

`scripts/storyline_report.py` walks all twelve steps through the HTTP API as the cast and writes
`evals/storyline/report_<mode>.json` and `.md` (offline, temporal or in_process), with each step's time and result:

    uv run python scripts/storyline_report.py --api http://127.0.0.1:9417 --report    # a running API (Temporal on or off)
    uv run python scripts/storyline_report.py --in-process --report                   # this process, rolled back

The end-to-end test `apps/web/e2e/hospital-storyline.spec.ts` walks the page itself.

## If something looks wrong

- **The preset note on the page lists a warning.** This happens when Surgery did not have exactly two discharge-ready patients. Reset again; the preset balances the units for the day's data.
- **She waits for a bed after the early rounds.** Housekeeping cleans the freed bed first, so press **Advance the clock** again. If someone else's admission took the bed, she gets the next free Surgery bed at the next retry, 30 minutes later.
- **After day 2 the rest of the hospital stands still.** Only the storyline's events, the rounds and housekeeping happen past the generated 36 hours.
