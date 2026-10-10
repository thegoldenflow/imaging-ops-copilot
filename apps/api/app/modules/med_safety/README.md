# Orders and medication safety (spec 7.2)

The order review service and medication reconciliation, with the pharmacist workbench. Risk tier `clinical_ds`:
the AI flags and drafts, a pharmacist or physician decides and signs. Web: `apps/web/src/features/med-safety/`
(route `/med-safety`). API: `/api/med-safety/...` (`router.py`). Demo: `demo/med_safety.md`.

| File | What |
| --- | --- |
| `rules.py` | the five order-review rules over plain facts, the rule tables' loader (`apps/api/config/med_rules/`) |
| `reconcile.py` | the reconciliation's rule layer (admission, discharge), the AI's judgements applied, the discharge medication table |
| `facts.py` | FHIR resources to plain facts (pure) |
| `service.py` | review, commentary, reconciliation, intake sweep, decisions, signing, views per role (the entry points) |
| `review_agent.py`, `medrec_agent.py` | the runtime agents `order_review` and `medication_reconciliation` (agent code: only the run) |
| `mocks.py` | the mock models (demo without an API key) |
| `tools.py` | handlers of getMedicationContext, getServiceOrderContext (read), submitOrderReview, draftMedRec (recommend) |
| `knowledge.py` | keyword retrieval over `apps/api/knowledge/guidelines/` (synthetic summaries) |
| `document.py`, `discharge.py` | the MedRec record's content; the discharge medication table for 7.3 |
| `models.py`, `evals.py` | tables `order_reviews` and `medrec_sessions` (migration 0009); the eval harness |

## Input resources

Read through `FhirGateway` only: the module as its agents (`agent:order_review`, `agent:medication_reconciliation`:
the registry's data scope, hospital-wide, every search audited), people as themselves (6.3; the pharmacist reads
medications, allergies, laboratory results, body weight and diagnoses hospital-wide, a physician their units).

- Order review: the order (MedicationRequest intent order, or a lab / imaging ServiceRequest); the patient's
  other active orders and home medications (MedicationStatement), active AllergyIntolerance (with the
  `drug-class` extension), the latest eGFR and body weight, age (Patient.birthDate), and for a test the same code
  ordered in the 72 hours before with its result (Observation `obs-<order>`, DiagnosticReport basedOn).
- The AI commentary's context (tools): getMedicationContext (encounter, patient, orders and discharge
  prescriptions, home medications, allergies, latest eGFR, creatinine, INR, potassium, weight, the diagnosis list)
  and getServiceOrderContext; de-identified by the runtime; refused without the patient's `ai_processing` consent.
- Reconciliation: admission, home medications vs MedicationRequest intent order; discharge, MedicationRequest
  intent plan (category discharge) vs the home list and the inpatient orders; the diagnosis list for indications.

## Output schema

- Rule findings: `{rule, severity (blocking | major | moderate | minor | info), title, detail, recommendation,
  evidence_refs[], entry, source}`; only the allergy rule blocks.
- AI commentary (the spec's schema plus a confidence per concern): `{concerns: [{type, severity (major | moderate
  | minor), explanation, evidence_refs[], confidence}], alternatives: [{suggestion, rationale}],
  guideline_refs: ["<file>#<section>"]}`; into the reviewer's queue as an `ai-review` Task (focus the order) via
  submitOrderReview. Evidence only from the run's context and the rules' evidence, resolved in FHIR before display;
  guideline references only from the retrieved sections; no claim that an order changed.
- Reconciliation agent: `{judgements: [{item_id, indicated, indication_ref, confidence, explanation}], notes:
  [{item_id, explanation}]}`; indicated with confidence >= 0.7 (registry `confidence_threshold`) stays "new",
  anything else is "new without indication" for the pharmacist.
- Reconciliation rows: match, omission, dose_change, duplicate_therapy, new, new_without_indication; each with its
  source (rule or AI with confidence), explanation, evidence, and the pharmacist's action (accept / modify /
  reject), reason, change.
- The record: DocumentReference type `medrec`, category admission or discharge, text/plain plus the JSON attachment
  `medrec-data` (items and, for a discharge, the discharge medication table); preliminary until the pharmacist
  signs and a physician co-signs through the signing service (authenticator the pharmacist).
- The discharge medication table for 7.3: `discharge.discharge_medication_table(fhir, encounter_id)` and
  `GET /api/med-safety/encounters/{id}/discharge-medications` (new / changed / stopped / continued, each with its
  source references).
- Decisions: order review accept_order / request_change with a reason (an `approve` audit event, on the ai-review
  Task when there is one; a change request is an `order-change-request` Task for the physician); reconciliation
  items `write` audit events, signatures `sign` events. The order's status never changes here.

## Eval location and thresholds

| Eval | Where | Gate | Mock result (2026-10-09) |
| --- | --- | --- | --- |
| Rule layer | `tests/test_med_rules.py`, `tests/test_medrec_rules.py` | >= 5 positive and 5 negative per rule; 100% line coverage of `rules.py` and `reconcile.py` | passed |
| AI commentary | `evals/order_review/` (60 labelled cases) | schema 100%, grounding 100%, precision >= 0.80, recall >= 0.85 | 100%, 100%, 0.945, 0.897: passed |
| New without indication | same, reported separately (44 new medications) | reported | accuracy 1.00 (35 judged by the AI, 9 by the rule) |
| Reconciliation | `evals/medrec/` (20 cases, 57 rows) | classification accuracy >= 0.90 | 1.00: passed |

Runner: `scripts/med_safety_eval.py` (`--live`, `--check`); cases from `scripts/gen_med_safety_eval.py`. The
backend suite runs both as gates (`tests/test_med_safety_eval.py`). The agents' eval_status stays `pending`: the
mock was written by the author of the labels, so its scores are optimistic; a live run is outstanding.

## Known failure modes

- The rule tables are demo simplifications (15 renal drugs, 25 pairs, 15 dose limits): anything not in them is
  not flagged by a rule. Class-level allergy matching only (no penicillin-cephalosporin cross-reactivity rule).
- Duplicate-test rule is literal: planned repeats (serial troponins, an INR while dosing warfarin) are flagged as
  "already resulted" (info); the AI may restate them (a false positive in the eval).
- Duplicate therapy counts intended combinations too (dual antiplatelet therapy after a stent, warfarin with
  enoxaparin while bridging): the pharmacist accepts them with a reason.
- Indications: the generator's inpatient medications for a diagnosis name it (rule); home medications and many
  admission orders do not, so the AI judges them; gastroprotection, delirium management and electrolyte
  replacement are reasons, not diagnoses, and come out as "new without indication".
- The intake sweep reviews orders authored in the last 24 hours and reconciles stays admitted in the last 48 hours
  or with discharge prescriptions; stays admitted during the simulated day get no discharge prescriptions.
