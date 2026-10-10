# Order review: AI commentary (spec 7.2)

Run 2026-10-09T19:50:53, model mode **mock**, 60 labelled synthetic cases, 58.6 s. Result: **passed**.

| Metric | Value | Gate |
| --- | --- | --- |
| Schema validity (commentary matches the output schema) | 100.00% | 100% |
| Grounding (every evidence reference resolves in FHIR) | 100.00% | 100% |
| Precision of flagged concern types | 0.946 (52 / 55) | >= 0.80 |
| Recall of flagged concern types | 0.897 (52 / 58) | >= 0.85 |
| Latency p50 / p95 (rules + commentary, ms) | 452 / 1030 | - |

## "New without indication" (reported separately)

The 44 cases whose order is a new medication (not on the home list) were also reconciled: the order's row is "new" when the order names a diagnosis on the list (rule) or the medication_reconciliation agent finds one with enough confidence, else "new without indication".

| Metric | Value |
| --- | --- |
| Accuracy (indicated / not) | 1.000 |
| Precision of "without indication" | 1.000 (8 flagged) |
| Recall of "without indication" | 1.000 (8 labelled) |
| Decided by | ai: 35, rule: 9 |

## Per concern type

| Type | TP | FP | FN | Precision | Recall |
| --- | --- | --- | --- | --- | --- |
| age_related | 8 | 0 | 0 | 1.00 | 1.00 |
| allergy | 4 | 0 | 0 | 1.00 | 1.00 |
| dose | 5 | 0 | 0 | 1.00 | 1.00 |
| duplicate_test | 3 | 1 | 0 | 0.75 | 1.00 |
| duplicate_therapy | 4 | 1 | 0 | 0.80 | 1.00 |
| indication | 2 | 0 | 4 | 1.00 | 0.33 |
| interaction | 14 | 0 | 0 | 1.00 | 1.00 |
| monitoring | 7 | 0 | 2 | 1.00 | 0.78 |
| renal | 5 | 1 | 0 | 0.83 | 1.00 |

## Misses and extra flags

| Case | Title | Expected | Predicted | Missed | Extra |
| --- | --- | --- | --- | --- | --- |
| or-010 | Gabapentin started with an eGFR of 25 and no neuropathic diagnosis | indication, renal | renal | indication | - |
| or-013 | Metformin continued with an eGFR of 45 | - | renal | - | renal |
| or-019 | Lorazepam started for sleep at 84 | age_related, indication | age_related | indication | - |
| or-022 | Lorazepam added to morphine at 70 | age_related, indication, interaction | age_related, interaction | indication | - |
| or-024 | Potassium chloride added to spironolactone, K 4.0 | indication, interaction | interaction | indication | - |
| or-026 | Amiodarone started on digoxin | interaction, monitoring | interaction | monitoring | - |
| or-045 | Clopidogrel added to aspirin after a myocardial infarction | - | duplicate_therapy | - | duplicate_therapy |
| or-048 | Ciprofloxacin for a urinary infection on warfarin, no recent INR | interaction, monitoring | interaction | monitoring | - |
| or-056 | Serial troponin three hours after the first | - | duplicate_test | - | duplicate_test |

## Notes

- The cases and labels are synthetic and written by the author of the mock model (app/modules/med_safety/mocks.py), so a mock run is optimistic: it shows the pipeline (context, schema, guards, evidence resolution, the review queue), not a model's clinical quality. `scripts/med_safety_eval.py --live` runs the configured model on the same path.
- Flagged concerns are compared by type per case (interaction, renal, allergy, dose, duplicate_therapy, duplicate_test, indication, monitoring, age_related, other).
- The rule layer itself is covered by unit tests (tests/test_med_rules.py: at least five positive and five negative cases per rule, 100% line coverage).
