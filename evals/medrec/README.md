# Medication reconciliation evals (spec 7.2)

The reconciliation (rule layer plus the `medication_reconciliation` agent's indication judgements) on 20
synthetic admissions and discharges. Runner: `apps/api/scripts/med_safety_eval.py` (`--only medrec`, `--live`,
`--check`); cases from `apps/api/scripts/gen_med_safety_eval.py`. Results: `report.json`, `report.md`. All data
is synthetic.

## Input resources

`cases.jsonl`, 20 cases (10 admission, 10 discharge). Each writes a patient into the EHR inside a rolled-back
transaction (`app/ehr/seed/medcases.py`): diagnoses, home medications (MedicationStatement), inpatient orders
(MedicationRequest intent order, some naming their diagnosis) and for a discharge the prescriptions
(MedicationRequest intent plan). The run reconciles the stay (`service.reconcile`) and lets the agent judge the new
medications and write the draft record (`service.settle`).

## Output schema

One row per medication, keyed `ok-`, `om-`, `dc-`, `new-<rxcui>` and `dup-<class>`, typed match, omission,
dose_change, duplicate_therapy, new or new_without_indication. Labels: `expected.items`, the type of every row.

## Evaluation and thresholds

| Metric | Gate |
| --- | --- |
| Classification accuracy over the union of expected and produced rows | >= 0.90 |

Every reconciliation must also leave its draft record (DocumentReference, docStatus preliminary). Also in the
backend suite (`tests/test_med_safety_eval.py`); the rule layer's own unit tests are `tests/test_medrec_rules.py`.

## Known failure modes

- Omission, dose or frequency change and duplicate therapy are deterministic rules, so most rows cannot be wrong
  on these cases; only new medications depend on the model. The mock and the labels share an author: a mock run is
  optimistic, `--live` is outstanding.
- Matching is by RxNorm ingredient: a formulary substitution (omeprazole at home, pantoprazole in hospital) shows
  as an omission plus a new medication, with a hint, for the pharmacist to accept.
- Intended combinations (bridging, dual antiplatelet therapy) are flagged as duplicate therapy.
