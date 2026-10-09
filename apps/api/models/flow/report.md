# Flow models: training report

Trained 2026-10-09T05:03:06 on data as of 2026-10-09T05:30 (scikit-learn 1.9.1, HistGradientBoosting). **Synthetic data**: these numbers show that the pipeline works (data from FHIR, time split, baseline, gate); they say nothing about clinical or operational performance on a real hospital.

| Model | Metric | Model | Baseline | Gate | Passed | Train / test rows | Test dates |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Admission from triage | AUROC | 0.8634 | 0.7296 | AUROC at least 0.05 above the baseline | yes | 2591 / 660 | 2026-09-27 .. 2026-10-09 |
| Discharge within 24 h | AUROC | 0.859 | 0.6172 | AUROC at least 0.05 above the baseline | yes | 8875 / 2424 | 2026-09-27 .. 2026-10-08 |
| Surgical duration (minutes) | MAE | 14.01 | 24.82 | MAE at least 10% below the baseline's | yes | 417 / 116 | 2026-09-27 .. 2026-10-08 |
| ED wait in the next hour (minutes) | MAE | 21.83 | 25.49 | MAE at least 10% below the baseline's | yes | 1900 / 497 | 2026-09-27 .. 2026-10-09 |

## Per model

### Admission from triage (`admission`)

- Baseline: CTAS <= 2 predicts admission
- Features: age, ctas, complaint, arrival_mode, heart_rate, systolic_bp, resp_rate, temperature, spo2, pain, gcs, admissions_12m
- Largest mean contributions on the test dates: ctas 0.9933, complaint 0.4583, age 0.4304, admissions_12m 0.2995, resp_rate 0.2272
- Example explanation: "Admission probability 0.67: age 93, fall, 7 admissions in 12 months"

### Discharge within 24 h (`discharge`)

- Baseline: days in hospital >= expected length of stay predicts discharge
- Features: reason, days_in, expected_los, los_ratio, open_orders, pending_results, orders_24h, alc
- Largest mean contributions on the test dates: los_ratio 1.8277, expected_los 0.5471, alc 0.3951, orders_24h 0.2799, days_in 0.1619
- Example explanation: "Discharge within 24 h 0.38: 237% of the expected stay, 7 days expected, 0 new orders in 24 h"

### Surgical duration (minutes) (`or_duration`)

- Baseline: median duration of the same procedure in the training data
- Features: procedure, surgeon, asa, age, emergent, start_hour
- Largest mean contributions on the test dates: procedure 36.5511, surgeon 17.5954, asa 10.6696, age 6.7521, emergent 5.1117
- Example explanation: "Predicted 144 min (booked 105): Dr. Elena Marsh, ASA 4, emergent"

### ED wait in the next hour (minutes) (`ed_wait`)

- Baseline: mean wait of the patients seen in the last 4 hours
- Features: census, waiting, boarders, ctas_1_2, ctas_3, ctas_4_5, hour, weekday, physicians
- Largest mean contributions on the test dates: census 4.6982, physicians 4.6111, hour 2.2265, ctas_4_5 1.9346, weekday 1.7962
- Example explanation: "Predicted wait 56 min: 2 physicians on duty, 2 in the ED, 1 at CTAS 4-5"

