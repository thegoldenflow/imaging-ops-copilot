# Regression eval: patient_message_triage

Run 2026-10-09T18:42:27 · agent 0.1.0 · prompt patient_message_triage@1 · model mode mock

**passed**: 11 of 12 cases match (accuracy 0.9167, gate 0.8).

| Source | Cases | Passed |
| --- | --- | --- |
| authored | 12 | 11 |

| Case | Source | Expected | Got | |
| --- | --- | --- | --- | --- |
| authored-chest-pain | authored | category clinical, urgency urgent | category clinical, urgency urgent | pass |
| authored-reschedule | authored | category scheduling, urgency routine | category scheduling, urgency routine | pass |
| authored-work-form | authored | category administrative, urgency routine | category administrative, urgency routine | pass |
| authored-wound | authored | category clinical, urgency urgent | category clinical, urgency urgent | pass |
| authored-visiting | authored | category scheduling, urgency routine | category scheduling, urgency routine | pass |
| authored-fall | authored | category clinical, urgency urgent | category clinical, urgency urgent | pass |
| authored-parking | authored | category other, urgency routine | category administrative, urgency routine | FAIL |
| authored-fever-zh | authored | category clinical, urgency urgent | category clinical, urgency urgent | pass |
| authored-thanks | authored | category other, urgency routine | category other, urgency routine | pass |
| authored-bill | authored | category administrative, urgency routine | category administrative, urgency routine | pass |
| authored-pills | authored | category clinical, urgency soon | category clinical, urgency soon | pass |
| authored-followup-zh | authored | category scheduling, urgency routine | category scheduling, urgency routine | pass |

Judged fields: category, urgency. Cases from human review are appended by the LowConfidenceReviewWorkflow (source human_review); a mismatch on one of them means the agent still disagrees with the person who corrected it. With the mock provider the result shows the pipeline, not a model's quality.
