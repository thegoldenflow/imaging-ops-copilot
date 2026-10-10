# Demo: orders and medication safety (WP6, 3 minutes)

What it shows: spec 7.2. Every new order goes through deterministic rules first (allergy, interaction, renal dosing, dose bounds by age and weight, a repeated test); then the `order_review` agent comments on it with its confidence and evidence. Only rules block; the AI never does, and nobody's order is changed by the AI layer. At admission and discharge the medication lists are reconciled: omissions, dose or frequency changes and duplicate therapy by rule, "new without indication" judged by the `medication_reconciliation` agent. The pharmacist handles every item with a reason and signs; a physician co-signs; the record becomes final through the signing service. A discharge reconciliation also produces the discharge medication table the discharge summary (7.3) reads.

Setup: `docker compose up -d postgres`, start the API and the web app (Playwright setup on this machine: `LLM_PROVIDER=mock PW_API_PORT=9421 PW_WEB_PORT=5183 VITE_API_PROXY_TARGET=http://127.0.0.1:9421`). Press **Reset demo** first; the API's hospital loop reviews the seeded orders and opens the reconciliations within a few seconds, the AI commentary follows in the background (the queue also triggers the review sweep). No API key needed: the mock models run. Logins: Rosa Pemberton (pharmacist), Dr. Yara Lindqvist (physician, Medicine A), Jonah Eriksen (nurse).

Say once, at the start: "Everything is synthetic. The drug tables are demo simplifications, not clinical guidance — a hospital would plug in a commercial drug knowledge base. AI items are advice with a confidence; a pharmacist or physician decides every one."

The planted cases (app/ehr/seed/medsafety.py) are on whichever stays are current when the data is seeded (the hospital day depends on the weekday): the 8.2 case is the most recent hip-fracture admission (on the Friday data: a 90-year-old man on Orthopedics, MRN 40000806), the Medicine A admission reconciliation has a dose change, duplicate statins and quetiapine with no indication. Search the queue by MRN or drug.

## Script (web app)

1. **0:00 — The queue.** Sign in as the pharmacist, **Medication safety**.
   - Left, the queue: **Blocking** first (two allergies: ibuprofen for a patient allergic to aspirin, piperacillin for a penicillin allergy with anaphylaxis), then major and moderate flags, the longest waiting first. Pill icon = medication order, clipboard = reconciliation; a sparkle = an AI flag. "Laboratory and imaging orders are the physicians' queue; the pharmacist sees medication orders and reconciliations hospital-wide."

2. **0:20 — The interaction case (8.2 step 6).** Search `naproxen` (or the MRN) and open **Interaction (major): naproxen + warfarin**.
   - Middle: the banner (eGFR, weight, **INR 3.8** in red, allergies, diagnoses), the order (naproxen 500 mg twice daily, "the review never changes the order").
   - Rule findings, each with its source: **Rule: Drug interaction** — major, the one-line mechanism (NSAIDs inhibit platelets and injure the gastric mucosa on top of warfarin), **View evidence** opens the warfarin order and the home medication. A second finding: naproxen + enoxaparin.
   - **AI commentary** with **Not evaluated** (the agent's eval status is pending until a live model run): each concern has its type, severity and **confidence** (interaction 0.92, INR 3.8 above range 0.85, age 0.75), **View evidence** reads the cited INR result and orders as you, plus the alternative "scheduled acetaminophen" and the guideline sections it cites (`anticoagulation#nsaids-antiplatelets`) — "synthetic summaries, retrieved locally, never the internet".
   - Type a reason, **Ask the prescriber to change it**: a Task for the physician; the decision is the `approve` audit event on the AI's review Task.

3. **1:10 — The omission case.** On the right of the same patient: **Admission reconciliation** — home medication | order | discrepancy type | action.
   - **Omission** (rule): amlodipine 5 mg daily is on the home list (the provincial drug history stand-in) but not ordered — "the home antihypertensive missed at admission". **Duplicate therapy**: warfarin and enoxaparin. The naproxen row shows the major interaction flag next to the order, and its indication judged by the AI (**AI · confidence 0.85**, fracture pain).
   - The **Sign** button is disabled. Amlodipine: **Modify** — "Resume amlodipine 5 mg daily", reason "Held by mistake at admission". Duplicate therapy: **Accept**, "Bridging until warfarin restarts".

4. **1:50 — Sign and co-sign.** Every item handled: **Sign as pharmacist** → "Signed by the pharmacist; a physician's co-signature makes the record final", the record `DocumentReference/medrec-<stay>-admission` still **preliminary**; the modify item became a Task for the prescriber.
   - The Medicine A case: open the reconciliation with quetiapine: **New without indication** (AI, confidence 0.80: "No diagnosis on the list explains quetiapine"). Handle every row, sign; **Switch role** → the physician → **Medication safety** → the item under **to co-sign** → **Co-sign as physician**: the record is **final**, "authenticated by Rosa Pemberton" (the pharmacist), co-signed by the physician. "The signing service makes it final, not the browser; each signature is a `sign` audit event."

5. **2:30 — Roles.** As the physician, open the piperacillin allergy (Medicine A): the buttons are disabled — "A blocking flag (allergy) must be handled by a pharmacist." As the nurse there is no **Medication safety** entry and the API answers 403.

6. **2:45 — Discharge.** (Optional) a discharge reconciliation (its rows include **hospital use** omissions — IV antibiotics, as-needed opioids — accepted in one click with a reason each): once signed and co-signed, its **discharge medication table** (new / changed / stopped / continued) is what the discharge summary reads: `GET /api/med-safety/encounters/<stay>/discharge-medications`.

Close with the medical director's line (8.3): "Anything that touches diagnosis or treatment is clinical decision support: the rules flag, the AI comments with its evidence and confidence, and a pharmacist decides — the AI can never block or change an order on its own."

## Script (terminal, 1 minute)

`cd apps/api && uv run python scripts/med_safety_demo.py` (rolled back unless `--commit`). Output on the data seeded Friday 2026-10-09 (mock model):

```text
AI mode: mock. Pharmacist: Rosa Pemberton.

1. Intake: 203 new orders reviewed by the rules, 52 admission and 25 discharge reconciliations opened.
   Pharmacist queue: 2 blocking, 96 to review. Top 3:
   - [BLOCKING] Allergy: ibuprofen (allergic to aspirin) (+1 more) (MRN 40000877, MEDA, waiting 1108 min)
   - [BLOCKING] Allergy: piperacillin (allergic to Penicillin) (MRN 40000540, MEDA, waiting 180 min)
   - [major] Interaction (major): ibuprofen + enoxaparin (MRN 40000572, SURG, waiting 1426 min)

2. The 8.2 case: 90-year-old male, Fracture of neck of femur, INR 3.8; order: naproxen 500 mg oral twice daily.
   Rule Drug interaction [major]: Interaction (major): naproxen + warfarin. NSAIDs inhibit platelet function and injure the gastric mucosa on top of warfarin's anticoagulation, a high risk of major (especially gastrointestinal) bleeding
   Rule Drug interaction [major]: Interaction (major): naproxen + enoxaparin. NSAIDs inhibit platelets on top of heparin anticoagulation, raising the bleeding risk (including spinal or epidural haematoma). Enoxaparin is on an active order
   AI commentary (done, run RUN-00504, review Task task-00233):
   - AI interaction [major, confidence 0.92]: Interaction (major): naproxen + warfarin. NSAIDs inhibit platelet function and injure the gastric mucosa on top of warfarin's anticoagulatio (evidence: MedicationRequest/medrx-005784, MedicationStatement/medst-001396)
   - AI interaction [major, confidence 0.92]: Interaction (major): naproxen + enoxaparin. NSAIDs inhibit platelets on top of heparin anticoagulation, raising the bleeding risk (including (evidence: MedicationRequest/medrx-005787)
   - AI age_related [minor, confidence 0.75]: At 90, naproxen carries more risk (falls, delirium, bleeding or kidney injury); use the lowest dose for the shortest time. (evidence: Patient/pat-0806)
   - AI monitoring [major, confidence 0.85]: The INR is 3.8, above the usual range; review warfarin and any added bleeding risk before giving this. (evidence: Observation/obs-ms-82-inr)
   - AI renal [minor, confidence 0.65]: eGFR 45 is above the rule's threshold but reduced; monitor kidney function on naproxen. (evidence: Observation/obs-0002472)
   - Alternative: Scheduled acetaminophen 650-1000 mg every 6 hours instead of the NSAID (no more than 3 g a day in an older adult)
   Guidelines: anticoagulation#nsaids-antiplatelets, analgesia#multimodal-fracture
   Pharmacist: request_change -> prescriber Task task-00234; the order is still active (the AI layer never edits orders).

3. Admission reconciliation MR-00538 (done; draft record DocumentReference/medrec-stay-01167-admission):
   - match                   [rule    ] home: sitagliptin 100 mg oral once daily   order: sitagliptin 100 mg oral once daily
   - match                   [rule    ] home: warfarin 5 mg oral once daily        order: warfarin 5 mg oral once daily
   - match                   [rule    ] home: metoprolol 50 mg oral twice daily    order: metoprolol 50 mg oral twice daily
   - match                   [rule    ] home: salbutamol 0.1 mg inhaled four times daily order: salbutamol 0.1 mg inhaled four times daily
   - omission                [rule    ] home: amlodipine 5 mg oral once daily      order: -
   - new                     [rule    ] home: -                                    order: enoxaparin 40 mg subcutaneous once daily
   - new                     [rule    ] home: -                                    order: acetaminophen 1000 mg oral four times daily
   - new                     [rule    ] home: -                                    order: hydromorphone 2 mg oral four times daily as needed
   - new                     [AI 0.85 ] home: -                                    order: naproxen 500 mg oral twice daily
   - duplicate_therapy       [rule    ] home: -                                    order: warfarin 5 mg oral once daily
   Pharmacist handled 2 discrepancies and signed: pharmacist_signed; prescriber Tasks task-00235.
   Dr. Keira Delacroix (ORTH) co-signed: final; DocumentReference/medrec-stay-01167-admission docStatus final, authenticator Rosa Pemberton.

4. Discharge reconciliation MR-00546 on Medicine A (stay-00942), signed and co-signed. Discharge medication table (final, DocumentReference/medrec-stay-00942-discharge):
   new      : amoxicillin 500 mg oral three times daily
   changed  : -
   stopped  : -
   continued: pantoprazole 40 mg oral once daily

Rolled back (use --commit to keep it).
```
