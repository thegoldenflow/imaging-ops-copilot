# Flow models: training report

Trained 2026-10-09T15:53:08 on data as of 2026-10-09T05:30 (scikit-learn 1.9.1, HistGradientBoosting). **Synthetic data**: these numbers show that the pipeline works (data from FHIR, time split, baseline, gate); they say nothing about clinical or operational performance on a real hospital.

| Model | Metric | Model | Baseline | Gate | Passed | Train / test rows | Test dates |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Admission from triage | AUROC | 0.8516 | 0.717 | AUROC at least 0.05 above the baseline | yes | 2591 / 660 | 2026-09-27 .. 2026-10-09 |
| Discharge within 24 h | AUROC | 0.8686 | 0.6228 | AUROC at least 0.05 above the baseline | yes | 9133 / 2327 | 2026-09-27 .. 2026-10-08 |
| Surgical duration (minutes) | MAE | 15.62 | 27.77 | MAE at least 10% below the baseline's | yes | 438 / 113 | 2026-09-28 .. 2026-10-09 |
| ED wait in the next hour (minutes) | MAE | 20.67 | 24.55 | MAE at least 10% below the baseline's | yes | 1907 / 497 | 2026-09-27 .. 2026-10-09 |

## Per model

### Admission from triage (`admission`)

- Baseline: CTAS <= 2 predicts admission
- Features: age, ctas, complaint, arrival_mode, heart_rate, systolic_bp, resp_rate, temperature, spo2, pain, gcs, admissions_12m
- Largest mean contributions on the test dates: ctas 1.0023, age 0.5554, complaint 0.3758, admissions_12m 0.3136, spo2 0.3125
- Example explanation: "Admission probability 0.14: SpO₂ 89%, CTAS 4, age 59"

### Discharge within 24 h (`discharge`)

- Baseline: days in hospital >= expected length of stay predicts discharge
- Features: reason, days_in, expected_los, los_ratio, open_orders, pending_results, orders_24h, alc
- Largest mean contributions on the test dates: los_ratio 1.9506, expected_los 0.5254, orders_24h 0.2957, alc 0.2314, days_in 0.1946
- Example explanation: "Discharge within 24 h 0.29: 383% of the expected stay, ALC, day 15.3 in hospital"

### Surgical duration (minutes) (`or_duration`)

- Baseline: median duration of the same procedure in the training data
- Features: procedure, surgeon, asa, age, emergent, start_hour
- Largest mean contributions on the test dates: procedure 38.5367, surgeon 17.2144, asa 11.4966, age 6.7691, emergent 5.3819
- Example explanation: "Predicted 101 min (booked 75): Laparoscopic cholecystectomy, Dr. Samuel Achebe, ASA 4"

### ED wait in the next hour (minutes) (`ed_wait`)

- Baseline: mean wait of the patients seen in the last 4 hours
- Features: census, waiting, boarders, ctas_1_2, ctas_3, ctas_4_5, hour, weekday, physicians
- Largest mean contributions on the test dates: census 3.1145, physicians 2.9802, hour 2.7086, weekday 2.0993, ctas_3 1.7318
- Example explanation: "Predicted wait 47 min: 1 in the ED, 2 physicians on duty, 0 boarding"

