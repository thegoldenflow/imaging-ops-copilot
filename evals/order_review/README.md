# Order review evals (spec 7.2)

The AI commentary of the order review (runtime agent `order_review`) on 60 labelled synthetic orders, and the
"new without indication" judgement of the reconciliation agent on the same orders. Runner:
`apps/api/scripts/med_safety_eval.py` (`--only order_review`, `--live` for the configured model, `--check` to fail
below a gate); cases from `apps/api/scripts/gen_med_safety_eval.py`. Results: `report.json`, `report.md`. All data
is synthetic.

## Input resources

`cases.jsonl`, 60 cases. Each is a small patient written into the EHR inside a rolled-back transaction
(`app/ehr/seed/medcases.py`): age, sex, weight, eGFR, INR, potassium, allergies, diagnoses (the stay's first),
home medications, other active orders, earlier tests with their results, and the order under review (54
medication orders, 6 lab or imaging orders). The run uses the real path: the rules, the agent through the runtime
(context through getMedicationContext / getServiceOrderContext, de-identified), the guards, the evidence check in
FHIR, the queue Task. Cases cover every rule (interaction, renal, allergy, dose bounds, duplicate test) and what
only context shows (duplicate therapy, age-related risk, monitoring, a missing indication), plus 17 orders that
need no concern.

## Output schema

`{concerns: [{type, severity: major|moderate|minor, explanation, evidence_refs[], confidence}],
alternatives: [{suggestion, rationale}], guideline_refs[]}` (the spec's schema plus a confidence per concern).
Concern types: interaction, renal, allergy, dose, duplicate_therapy, duplicate_test, indication, monitoring,
age_related, other. Labels: `expected.concerns` (the types a pharmacist reviewer would want flagged) and, for
medication orders, `expected.indicated` (whether the diagnosis list holds an indication).

## Eval set and threshold

| Metric | Gate |
| --- | --- |
| Schema validity of the commentary | 100% |
| Grounding: every evidence reference resolves in FHIR (and every guideline reference is a retrieved section) | 100% |
| Precision of the flagged concern types (per case, by type) | >= 0.80 |
| Recall of the flagged concern types | >= 0.85 |
| "New without indication" (orders that are new medications, reconciled at admission): accuracy, precision, recall | reported separately |

Also in the backend suite (`tests/test_med_safety_eval.py`). The rule layer has its own unit tests
(`tests/test_med_rules.py`: >= 5 positive and 5 negative cases per rule, 100% line coverage).

## Known failure modes

- The labels and the mock model (`app/modules/med_safety/mocks.py`) come from the same author: the mock scores are
  optimistic and show the pipeline, not a model's clinical quality. The agent's eval_status stays `pending` until
  `--live` has run on the configured model.
- The mock misses indications for drug classes it has no map for (benzodiazepines, gabapentinoids, electrolytes),
  and monitoring that follows from an interaction rather than the drug itself (digoxin levels after amiodarone, an
  INR after a fluoroquinolone); it over-flags dual antiplatelet therapy and planned repeat tests that the rules
  already marked.
- Concerns are compared by type only, not by wording or the drug they name.
