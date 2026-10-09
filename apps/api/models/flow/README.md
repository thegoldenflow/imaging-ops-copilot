# Flow models (Control Tower, spec 7.1)

Four models the Control Tower uses to look ahead: who in the ED will be admitted, who on the wards will go home
within 24 hours, how long an OR case will really take, and how long the next hour's ED patients will wait.

**Synthetic data only.** Every number in `report.md` comes from the generated hospital (60 days of simulated ED
visits, stays and OR cases). The ground truth was written by the same project (`app/ehr/seed/simulate.py`), so the
models learn rules we wrote. The metrics show that the pipeline works (data read from FHIR, a time split, the
spec's baselines and gates, explanations); they say nothing about clinical or operational performance in a real
hospital. A real deployment would retrain on the hospital's own history, validate retrospectively and run in
shadow mode before anyone relies on it.

## Input resources

`scripts/build_flow_dataset.py` reads FHIR through `FhirGateway` as the Control Tower (registry entry
`control_tower`, one audited hospital-wide search per resource type) and writes `data/<model>.csv` (not in git):

| Model | One row per | Features | Label | From |
| --- | --- | --- | --- | --- |
| `admission` | ED visit at triage | age, CTAS, complaint, arrival mode, first vital signs (HR, systolic BP, RR, temperature, SpO₂, pain, GCS), admissions in the previous 12 months | ED disposition = admitted | Encounter (EMER, IMP), Observation (CTAS, vital-sign panel), Patient |
| `discharge` | inpatient at 07:00 and 19:00 | admission reason, days in hospital, expected LOS and their ratio, open orders, pending results, orders in the last 24 h, ALC flag | discharged within 24 h | Encounter (IMP), ServiceRequest, Flag |
| `or_duration` | finished OR case | procedure, surgeon, ASA class, age, elective / emergent, booked start hour | minutes in the OR | Procedure, Appointment, Patient |
| `ed_wait` | the ED every 30 minutes | patients in the ED, waiting to be seen, boarders, CTAS mix (1–2, 3, 4–5), hour, weekday, physicians on duty | mean wait (triage → physician) of the patients triaged in the next hour | Encounter (EMER), Observation (CTAS), ServiceRequest (bed requests), the ED roster (`reference.ed_physicians_on_duty`) |

The same feature functions (`app/modules/control_tower/features.py`) score the live boards, so training and
serving never compute a feature two ways.

## Output schema

`<model>.joblib`: `{name, kind, features, model, trained_at, metrics, importance, sklearn}`; the model is
scikit-learn's `HistGradientBoostingClassifier` / `Regressor` (max 200 iterations, 15 leaves, learning rate 0.05).
Scoring (`flowmodels.FlowModels.predict`) returns the probability or the minutes plus the three largest
per-feature contributions, from which the one-sentence explanation is written
(`Admission probability 0.82: CTAS 2, age 72, SpO₂ 91%`). Contributions are path attributions on the trees
(Saabas: each split's change in the expected value goes to its feature); with the expected value they add up
exactly to the raw prediction (tested). For the bed manager the sentence names the factors without their
clinical values.

## Evaluation and thresholds

`scripts/train_flow_models.py [--rebuild] [--check]` trains each model on the earlier dates and tests on the
last 20% of the dates (a time split, never random), then writes `report.json` and `report.md`:

| Model | Baseline (spec) | Gate (spec) |
| --- | --- | --- |
| admission | CTAS <= 2 predicts admission | AUROC >= baseline + 0.05 |
| discharge | days in hospital >= expected LOS predicts discharge | AUROC >= baseline + 0.05 |
| or_duration | median minutes of the same procedure in the training data | MAE <= 90% of the baseline's |
| ed_wait | mean wait of the patients seen in the last 4 hours | MAE <= 90% of the baseline's |

The final model is refitted on all rows after the held-out metrics are taken. The backend suite checks the
committed report against the gates, that the explanations add up, and retrains the duration model on the test
database (`tests/test_control_tower.py`).

## Known failure modes

- The generated hospital is the only data: rules the generator does not have (staffing shortages, seasonal
  surges, transfers in) are invisible to the models.
- `ed_wait` is the weakest (its target averages the next hour's few patients with different CTAS levels); its
  margin over the baseline is the smallest of the four and depends on the seeded day.
- The ED roster is master data (`ed_physicians_on_duty`), not read from FHIR; a real deployment would read the
  scheduling system.
- LightGBM, which the spec names, is not used: it is not a dependency of this repository and adding it is a
  stack change that needs the owner's confirmation. scikit-learn's histogram gradient boosting is the same
  family of model; LightGBM's `pred_contrib` would give Shapley values instead of path attributions.
- Models are files trained on one seeded day; a demo reset on another weekday changes the data, not the models
  (retrain with `--rebuild` to follow it).
