# Medication reconciliation (spec 7.2)

Run 2026-10-09T19:50:53, model mode **mock**, 20 synthetic reconciliations (admission and discharge), 11.3 s. Result: **passed**.

| Metric | Value | Gate |
| --- | --- | --- |
| Classification accuracy (every row's discrepancy type) | 1.000 (57 / 57) | >= 0.90 |

Row types: match, omission, dose_change, duplicate_therapy (rules), new, new_without_indication (the indication judged by the medication_reconciliation agent).

## Confusion (expected -> produced)

| Expected | Produced | Rows |
| --- | --- | --- |
| dose_change | dose_change | 6 |
| duplicate_therapy | duplicate_therapy | 5 |
| match | match | 12 |
| new | new | 15 |
| new_without_indication | new_without_indication | 5 |
| omission | omission | 14 |

## Cases

| Case | Kind | Title | Rows | Correct | Wrong |
| --- | --- | --- | --- | --- | --- |
| mr-001 | admission | Hip fracture on warfarin: an antihypertensive missed, a dose change, naproxen and enoxaparin | 6 | 6 | - |
| mr-002 | admission | Omeprazole at home, pantoprazole in hospital | 3 | 3 | - |
| mr-003 | admission | Metformin doubled, insulin started | 2 | 2 | - |
| mr-004 | admission | Simvastatin added to atorvastatin | 3 | 3 | - |
| mr-005 | admission | Sertraline left off, tramadol for back pain | 2 | 2 | - |
| mr-006 | admission | Levothyroxine twice a day, allopurinol missed, colchicine for gout | 3 | 3 | - |
| mr-007 | admission | Quetiapine continued in Alzheimer's disease | 1 | 1 | - |
| mr-008 | admission | Lorazepam started with no diagnosis for it | 1 | 1 | - |
| mr-009 | admission | Heart failure: furosemide doubled, spironolactone held, potassium started | 3 | 3 | - |
| mr-010 | admission | COPD exacerbation: tiotropium held, prednisone started | 3 | 3 | - |
| mr-011 | discharge | Pneumonia: home naproxen stopped, IV antibiotic to amoxicillin | 5 | 5 | - |
| mr-012 | discharge | Heart failure: furosemide increased at discharge | 2 | 2 | - |
| mr-013 | discharge | Hip fracture on warfarin: enoxaparin continued with warfarin | 3 | 3 | - |
| mr-014 | discharge | Omeprazole at home, pantoprazole and omeprazole at discharge | 3 | 3 | - |
| mr-015 | discharge | Diabetes: insulin added to metformin at discharge | 2 | 2 | - |
| mr-016 | discharge | After a myocardial infarction: statin switched, dual antiplatelets | 5 | 5 | - |
| mr-017 | discharge | Lisinopril doubled at discharge | 1 | 1 | - |
| mr-018 | discharge | Lorazepam started in hospital and prescribed at discharge | 2 | 2 | - |
| mr-019 | discharge | Fracture: as-needed morphine and ondansetron stop, tramadol at discharge | 4 | 4 | - |
| mr-020 | discharge | Sepsis: two IV antibiotics stop, amoxicillin at discharge | 3 | 3 | - |

## Notes

- Synthetic cases and labels by the author of the mock model: optimistic; `--live` runs the configured model. Omission, dose or frequency change and duplicate therapy are rules and deterministic; only the indication of new medications depends on the model.
