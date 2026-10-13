# Follow-up call eval (spec 7.4)

Run 2026-10-12T03:05:10 (gemini model): **passed**. 50 call scripts, 15 with a red flag. Red-flag recall **1.0** (gate 0.95). With the model down, the rules alone escalated **15 of 15** red-flag scripts (7.4: all, without the model); rule codes as labelled: 15 of 15. Rule hits on scripts without a red flag: none.

Over-escalation (recorded, not gated: a nurse calling back without need is the accepted cost): 13 of 35 scripts without a red flag (0.371), by model_unavailable 2, model 11.

Write-back: 50 call records, 24 calls with patient-reported Observations, 28 nurse Tasks.

Synthetic scripts written alongside the mock models: with the mock the numbers show that the pipeline works (dialog, identity check, rules, Tool Gateway, write-back), not a model's quality. Run with --live on the configured model before trusting them.

## Scripts

| Script | Patient | Red flag | What | Escalated | By | Priority | Rule codes | Classifier |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| fu-01 | en | yes | fever reading | yes | rules | urgent | fever | ok 1.0 |
| fu-02 | en | yes | emergency | yes | rules | stat | chest_pain | ok 1.0 |
| fu-03 | en | yes | emergency | yes | rules | stat | short_breath | ok 1.0 |
| fu-04 | en | yes | heavy drainage, told at the end | yes | rules | urgent | wound_drainage | ok 1.0 |
| fu-05 | en | yes | a fall | yes | rules | urgent | fall | ok 1.0 |
| fu-06 | en | yes | cannot eat or drink | yes | rules | urgent | cannot_eat_drink | ok 1.0 |
| fu-07 | en | yes | altered consciousness | yes | rules | stat | altered_consciousness | ok 1.0 |
| fu-08 | en | yes | asks for a person | yes | rules | urgent | wants_person | ok 1.0 |
| fu-09 | en | yes | heavy bleeding | yes | rules | stat | heavy_bleeding | ok 1.0 |
| fu-10 | zh | yes | the demo case | yes | rules | urgent | wound_drainage | ok 1.0 |
| fu-11 | zh | yes | fever in Chinese numerals | yes | rules | urgent | fever | ok 1.0 |
| fu-12 | zh | yes | chest tightness | yes | rules | stat | chest_pain | ok 1.0 |
| fu-13 | zh | yes | a fall, told at the end | yes | rules | urgent | fall | ok 1.0 |
| fu-14 | zh | yes | cannot eat or drink | yes | rules | urgent | cannot_eat_drink | ok 1.0 |
| fu-15 | zh | yes | yes to their own first warning sign | yes | rules | stat | heavy_bleeding | ok 1.0 |
| fu-16 | en | no | all clear | no | - | - | - | ok 1.0 |
| fu-17 | en | no | all clear | no | - | - | - | ok 1.0 |
| fu-18 | en | no | all clear, tired | no | - | - | - | ok 1.0 |
| fu-19 | en | no | all clear | yes | model_unavailable | routine | - | unavailable  |
| fu-20 | en | no | all clear | no | - | - | - | ok 1.0 |
| fu-21 | en | no | all clear | no | - | - | - | ok 1.0 |
| fu-22 | en | no | all clear | no | - | - | - | ok 1.0 |
| fu-23 | en | no | all clear | no | - | - | - | ok 1.0 |
| fu-24 | en | no | all clear | no | - | - | - | ok 1.0 |
| fu-25 | zh | no | all clear | no | - | - | - | ok 0.99 |
| fu-26 | zh | no | all clear | no | - | - | - | ok 1.0 |
| fu-27 | zh | no | all clear | no | - | - | - | ok 0.99 |
| fu-28 | zh | no | all clear, tired | no | - | - | - | ok 0.95 |
| fu-29 | zh | no | all clear | no | - | - | - | ok 1.0 |
| fu-30 | zh | no | all clear | no | - | - | - | ok 0.99 |
| fu-31 | zh | no | all clear | no | - | - | - | ok 0.99 |
| fu-32 | zh | no | all clear | no | - | - | - | ok 0.99 |
| fu-33 | en | no | low reading | no | - | - | - | ok 0.95 |
| fu-34 | en | no | negations | no | - | - | - | ok 1.0 |
| fu-35 | en | no | light drainage | yes | model | urgent | - | ok 0.95 |
| fu-36 | en | no | negated fall | no | - | - | - | ok 0.95 |
| fu-37 | zh | no | low reading | no | - | - | - | ok 0.95 |
| fu-38 | zh | no | light drainage | yes | model | urgent | - | ok 0.95 |
| fu-39 | zh | no | negations | no | - | - | - | ok 0.95 |
| fu-40 | zh | no | negated fall | no | - | - | - | ok 1.0 |
| fu-41 | en | no | dizziness | yes | model | urgent | - | ok 1.0 |
| fu-42 | en | no | a missed medicine | yes | model | urgent | - | ok 1.0 |
| fu-43 | en | no | ran out | yes | model | urgent | - | ok 1.0 |
| fu-44 | en | no | a question for the nurse | yes | model | urgent | - | ok 1.0 |
| fu-45 | en | no | did not know the follow-up | yes | model | urgent | - | ok 1.0 |
| fu-46 | zh | no | dizziness | yes | model | urgent | - | ok 1.0 |
| fu-47 | zh | no | a missed medicine | yes | model_unavailable | routine | - | unavailable  |
| fu-48 | zh | no | a question for the nurse | yes | model | urgent | - | ok 1.0 |
| fu-49 | zh | no | swelling | yes | model | urgent | - | ok 1.0 |
| fu-50 | zh | no | did not know the follow-up | yes | model | urgent | - | ok 1.0 |
