# Demo: documentation agent (WP7, 4 minutes)

What it shows: spec 7.3. The discharge summary is drafted from the FHIR record in chunks (diagnoses and procedures;
medications with the signed discharge medication table; observations and reports; the care plan), every item citing
the resources it comes from; the DischargeSummaryValidator checks every drug, diagnosis and procedure name against
those resources and blocks the signature while anything is untraceable — at the signing service, not only in the
browser. From the signed summary only, the patient instructions are rendered in English, Simplified and Traditional
Chinese from one structure. The nurse confirms an SBAR handoff for the shift. AI output is a draft a person signs.

Setup: `docker compose up -d postgres`, start the API and the web app (on this machine:
`LLM_PROVIDER=mock PW_API_PORT=9429 PW_WEB_PORT=5193 VITE_API_PROXY_TARGET=http://127.0.0.1:9429`). Press **Reset
demo**, then sign the planted cases' discharge medication reconciliations — what the pharmacist does in Medication
safety before a summary can be written (demo/med_safety.md, step 6) — in one go:

```bash
cd apps/api && uv run python scripts/documentation_demo.py --prepare --commit
```

No API key needed: the mock models run. Logins: Dr. Yara Lindqvist (physician, Medicine A), Jonah Eriksen (nurse,
Medicine A).

The planted cases (app/ehr/seed/documentation.py) are on Medicine A stays current when the data is seeded (the
hospital day depends on the weekday). On the Friday 2026-10-09 data: **Ming Liu** (MRN 40000713, Simplified Chinese,
stroke with atrial fibrillation, a catheter ablation, warfarin lowered to 3 mg), **Sarah Jensen** (MRN 40000209,
sepsis; her hypertension note says metoprolol "was discussed, not started"), **Frank Nolan** (MRN 40000235,
discharged two days ago, a blood culture still pending). `GET /api/documentation/demo` lists them.

Say once, at the start: "Everything is synthetic. The AI drafts; the physician and the nurse sign. Every sentence
points at the record it comes from."

## Script (web app)

1. **0:00 — Insufficient information.** Sign in as the physician, **Documentation**. Pick a patient whose medication
   table is "None" and press **Draft with AI**: "Insufficient information: nothing was drafted. Missing the signed
   discharge medication table" with a link to Medication safety. "The agent refuses rather than fill in a medication
   list nobody has reconciled."

2. **0:25 — The validator blocks a medicine (8.2 step 9).** Open **Sarah Jensen** → **Draft with AI**. In a few
   seconds the draft appears: **AI draft**, **Not evaluated** (the agent has not passed a live eval), and a red bar
   "1 untraceable item: signing is blocked".
   - Scroll to **Medications at discharge → New**: **metoprolol** is red — "the cited sources are not a
     MedicationRequest…", "medication 'metoprolol' does not match any medication in the input". Click it: on the
     right the source panel highlights what it cites — the *hypertension* Condition, whose note says metoprolol was
     discussed, not started. "A classic hallucination: discussed is not prescribed."
   - **Sign as physician** is disabled. "And not just here: the chart's Sign button gets the same refusal from the
     signing service" — **Patients** → MRN 40000209 → Documents shows "Signing blocked by the validator".

3. **1:15 — Sources and the fix.** Click a hospital-course line (for example the admission vital signs): its
   Observation lights up on the right. **Edit** → on the metoprolol item **Remove** → **Save and re-check**: the bar
   turns green ("Every item traces to its sources"). **Sign as physician** → "Signed by Dr. Yara Lindqvist; final".
   A note stays: "The AI draft had 1 untraceable item; it was fixed or removed before signing" — the raw draft and its
   validator report are kept in the document for audit and the eval's raw hallucination rate.

4. **1:50 — The warfarin patient, signed.** Open **Ming Liu** → **Draft with AI**: no red items; diagnoses (stroke,
   atrial fibrillation), the ablation, **warfarin 3 mg (was 5 mg)** under Changed, the blood thinner clinic in three
   days under Follow-up, red flags for bleeding and the puncture site. **Sign as physician**.

5. **2:20 — Bilingual instructions (8.2 step 10).** **Switch role** → the nurse → **Documentation** → **Patient
   instructions** → Ming Liu. The draft appeared on its own once the summary was signed ("written from the signed
   summary only, never the raw record").
   - It opens in **简体中文** (her `communication.language` is zh-CN): 您为什么住院 … 华法林（一种药物，可用于心房颤动等）3毫克 —
     the first time a term appears it carries an explanation, here from the bilingual knowledge graph; doses in 毫克,
     no abbreviations; the warfarin notes (按预约日期抽血, 每周吃的绿叶蔬菜量要大致相同); red flags with what to do (911 for heavy
     bleeding; call the unit for blood in the urine or a growing lump where the tube went in).
   - **English** and **繁體中文**: the same items, line for line — "one structure, three renderings; the model only
     picked codes from menus, so the languages cannot drift apart". The readability panel: Flesch-Kincaid grade (target
     6–8 or easier), "No medical abbreviations", terms explained at first use.
   - **Sign as nurse** → **Send SMS** ("SMS queued", her SMS consent is on file) → **Patient portal** → **Open the
     portal page**: what she sees, in Chinese.

6. **3:20 — Shift handoff.** **Shift handoff** tab → Ming Liu → **Draft handoff**: S (day 7 on Medicine A, the latest
   vital signs), B (diagnoses, the ablation, current medications), A (the 12-hour trend; "Intake and output: not
   recorded" — the agent never fills in what is not there), R (open tasks and flags). Click a statement: its sources
   light up. **Confirm handoff** → "Confirmed: final" — one signature, no co-signature.

7. **3:45 — Close.** The medical director's line (8.3): "The documentation agent can't invent a medication: every
   drug name in a draft has to trace back to a FHIR resource, or the validator blocks the signature."

Without AI consent (optional): on the chart, withdraw the patient's AI processing consent; Documentation then offers
**Open the template** — no model call, the sources listed to cite, the same validator on what the physician writes.

## Script (terminal, 1 minute)

`cd apps/api && uv run python scripts/documentation_demo.py` (rolled back unless `--commit`). Output on the data
seeded Friday 2026-10-09 (mock model):

```text
AI mode: mock. Physician Dr. Yara Lindqvist, nurse Jonah Eriksen.
Planted cases: warfarin_zh stay-01041, hallucination stay-00942, discharged stay-01073

1. stay-01041: draft before the discharge medication table is signed -> insufficient:
   missing the signed discharge medication table (the discharge medication reconciliation in Medication safety, signed by the pharmacist and co-signed by a physician)

2. AI draft dsum-stay-01041 in 0.69 s (5 model calls: 4 chunks + synthesis; run RUN-01030):
   Reason: Admitted with cerebrovascular accident. ['Condition/cond-005690', 'Encounter/stay-01041']
   2026-10-03: Vital signs (admission): BP 116/77, heart rate 98 /min, respiratory rate 17 /min, temperature 36.9 °C, SpO2 95 (2 sources)
   2026-10-04: Catheter ablation of tissue of heart. Creatinine 1.44 mg/dL (high). CT Head: findings documented in the imagin (3 sources)
   2026-10-06: CT Head: findings documented in the imaging report. (1 sources)
   Diagnoses: Cerebrovascular accident ['Condition/cond-005690']; Atrial fibrillation ['Condition/cond-doc-afib']
   new: aspirin 81 mg oral once daily; atorvastatin 20 mg oral once daily
   changed: warfarin 3 mg oral once daily
   stopped: acetaminophen
   continued: lisinopril 10 mg oral once daily
   Validator: 0 red, 20 of 20 items traceable.
   Signed by Dr. Yara Lindqvist: final.

3. stay-00942: AI draft dsum-stay-00942; the validator marks 2 issue(s) red:
   - medications.new[1]: the cited sources are not a MedicationRequest or MedicationStatement or DocumentReference
   - medications.new[1]: medication 'metoprolol' does not match any medication in the input
   The chart's Sign button (signing service): refused - the validator blocks signing: 1 untraceable item(s) block signing
   The physician removes medications.new[1] (metoprolol).
   Re-checked: 0 red; signed: final. The raw AI draft's issues stay on record (2).

4. Patient instructions pinstr-stay-01041 from the signed summary (default language zh-Hans); the same 5 blocks in each language:
   [en] aspirin 81 mg | To protect your heart and blood vessels | Morning | This is a new medicine.
   [zh-Hans] 阿司匹林（一种预防血栓的药物）81毫克 | 保护心脏和血管 | 早上 | 这是新药。
   [zh-Hant] 阿司匹林（一種預防血栓的藥物）81毫克 | 保護心臟和血管 | 早上 | 這是新藥。
   English Flesch-Kincaid grade 5.4; Chinese: no abbreviations True, terms explained True (1 from the knowledge graph).
   Signed by Jonah Eriksen: final.
   print: sent True
   sms: sent True (SMS consent on file)
   portal: sent True /documentation/portal/pinstr-stay-01041

5. Handoff sbar-stay-01041-20261009D:
   S: Day 7 of the stay on Medicine A. Latest vital signs (06:00): BP 129/70 mmHg, heart rate 77 /min, respiratory rate 20 /min, temperature 36.7
   B: Admitted with cerebrovascular accident.
   B: Admitted with atrial fibrillation.
   A: Over this shift: Heart rate 76 /min then 77 /min; SpO2 95.9 % then 94.3 %; Temperature 36.7 °C then 36.7 °C; Respiratory rate 15 /min then 2
   A: Pain score 0 then 1 out of 10.
   R: No open tasks or flags; continue routine observations.
   Confirmed: final.

Rolled back (use --commit to keep it).
```

Evals: `evals/documentation/report.md` (30 cases; untraceable after the validator 0, the 5 injected hallucinations
blocked, bilingual consistency 1.0; mock results are optimistic) and `rating_sheet.md` for the human rating.
