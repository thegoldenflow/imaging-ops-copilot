# Documentation agent: eval report (spec 7.3)

Run 2026-10-13T15:25:07, model mode **mock**, 30 synthetic stays, 101.0 s. Result: **passed** (automated gates; the human rating is outstanding: rating_sheet.md).

The mock model was written by the author of the cases, so these numbers are optimistic: they show that the pipeline works (chunks, segment notes, synthesis, citations, validator, signing, bilingual rendering), not what a model scores. `scripts/documentation_eval.py --live` runs the configured model through the same path.

## Gates

| Gate | Value | Passed |
| --- | --- | --- |
| Untraceable items after the validator (what is signed), gate 0 | 0 | True |
| Validator recall on the oracle's untraceable items, gate 1.0 | 1.0 | True |
| Schema validity (summaries drafted and signed), gate 1.0 | 1.0 | True |
| Bilingual consistency on 10 instructions (same fact items and numbers in EN, zh-Hans, zh-Hant), gate 1.0 | 1.0 | True |
| Injected hallucinations blocked, gate 5 of 5 | 5 | True |
| Automated proxies of the four rating dimensions, every mean >= 4 | see below | True |

## Monitoring

- Raw (pre-validator) hallucination rate: **0.0138** (6 untraceable of 436 fact items in the AI drafts; the oracle counted 6). Recorded, not a gate (spec 7.3). With the mock they all come from the three cases whose note names a medicine that was only discussed.
- Patient instructions: English Flesch-Kincaid grade mean 5.34, max 6.8 (target 6-8; at or below 8 in 100% of documents; below 6 counts as easier); Chinese without abbreviations 100%, every term explained at first use 100%; explanations from the knowledge graph 17% (the rest are reviewed plain-language explanations or the medicine's class: the graph's facts are noisy).
- Validator red items left on the drafts: instructions 0, handoffs 0.
- Documents drafted: summaries drafted 30 (signed 30), instructions 30, handoffs 30 of 30 cases (a document the model could not draft fails its case; a draft the validator blocked at signing is 'unsigned' and keeps its pre-validator metrics; the reason is in the cases table).
- Discharge summary drafting time (pipeline, zero-latency mock): median 0.656 s, max 4.375 s; model calls per summary {5: 30} (one per chunk of resources, then the synthesis). With a live model the time is about calls x per-call latency: under 30 s while a structured call takes under about 5.5 s. Not yet measured with a live model (this run used the mock).

## Automated proxies (1-5, mean over the cases)

| Document | Completeness | Accuracy | Readability | Format |
| --- | --- | --- | --- | --- |
| summary | 5.0 | 5.0 | 5.0 | 5.0 |
| instructions | 5.0 | 5.0 | 5.0 | 5.0 |
| handoff | 5.0 | 5.0 | 5.0 | 5.0 |

Proxies: completeness = expected diagnoses, procedures, follow-ups, pending tests and every discharge medication present (instructions: every summary medicine and follow-up, red flags, daily care; handoff: four sections, open tasks and flags in the recommendation, vital signs in the assessment, pain and intake/output stated or declared not recorded); accuracy = the discharge medication table's categories and the expected codes (instructions: no red item; handoff: the share of statements and not-recorded declarations with no validator issue); readability = the course's grade level (instructions: grade <= 8, Chinese checks; handoff: statements of at most 45 words); format = dated, ordered course and every item cited (instructions: five blocks, identical item keys in three languages; handoff: a one-sentence situation, no statement stating an absence). The handoff's proxies each measure one thing (evals/documentation/README.md).

## Injected hallucinations

| Sample | Path | Blocked | Validator |
| --- | --- | --- | --- |
| invented medication citing another drug's order | medications.new[1] | True | untraceable_medication: medication 'rivaroxaban' does not match any medication in the input |
| medication citing a made-up resource id | medications.continued[2] | True | unknown_ref: cites MedicationRequest/ev-fabricated-1, which is not one of this document's inputs; untraceable_medication: medication 'digoxin' does not match any medication in the  |
| diagnosis that is not in the record | discharge_diagnoses[4] | True | untraceable_diagnosis: diagnosis 'Pulmonary embolism' does not match any diagnosis in the input |
| procedure with no source | procedures[1] | True | no_source: no source cited: every fact needs the resource it comes from; untraceable_procedure: procedure 'Coronary artery bypass grafting' does not match any procedure in the inpu |
| unprescribed drug named in the hospital course | hospital_course[3] | True | untraceable_drug: drug 'metronidazole' in the text is not a medication of the input |

## Bilingual consistency

| Case | Language | Fact items | Keys match | Number mismatches |
| --- | --- | --- | --- | --- |
| doc-02 | zh-CN | 19 | True | 0 |
| doc-05 | zh-CN | 11 | True | 0 |
| doc-08 | zh-CN | 10 | True | 0 |
| doc-11 | zh-CN | 13 | True | 0 |
| doc-14 | zh-CN | 12 | True | 0 |
| doc-17 | zh-CN | 11 | True | 0 |
| doc-20 | zh-TW | 17 | True | 0 |
| doc-23 | zh-TW | 17 | True | 0 |
| doc-26 | zh-TW | 10 | True | 0 |
| doc-29 | zh-TW | 23 | True | 0 |

## Cases

| Case | Title | Raw untraceable | After validator | Summary C/A/R/F | Instructions C/A/R/F | Handoff C/A/R/F | FK grade | Draft |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| doc-01 | Heart attack, on warfarin | 0 of 21 | 0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 5.4 | 0.875 s, 5 calls |
| doc-02 | Heart failure, on warfarin, zh-CN | 0 of 17 | 0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 4.6 | 0.5 s, 5 calls |
| doc-03 | Pneumonia | 0 of 11 | 0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 5.7 | 0.5 s, 5 calls |
| doc-04 | COPD exacerbation, tempting note | 2 of 16 (untraceable_medication, wrong_source_type) | 0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 5.0 | 0.5 s, 5 calls |
| doc-05 | Sepsis, zh-CN | 0 of 13 | 0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 5.5 | 0.546 s, 5 calls |
| doc-06 | Urinary tract infection, on warfarin | 0 of 17 | 0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 5.4 | 1.86 s, 5 calls |
| doc-07 | Stroke | 0 of 12 | 0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 5.8 | 0.812 s, 5 calls |
| doc-08 | Cellulitis, zh-CN | 0 of 11 | 0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 6.7 | 0.547 s, 5 calls |
| doc-09 | Broken hip, on warfarin | 0 of 21 | 0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 3.7 | 1.437 s, 5 calls |
| doc-10 | Acute cholecystitis | 0 of 14 | 0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 5.3 | 0.5 s, 5 calls |
| doc-11 | Heart attack, zh-CN | 0 of 15 | 0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 5.2 | 0.656 s, 5 calls |
| doc-12 | Heart failure | 0 of 12 | 0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 5.1 | 0.563 s, 5 calls |
| doc-13 | Pneumonia, on warfarin | 0 of 17 | 0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 5.0 | 0.547 s, 5 calls |
| doc-14 | COPD exacerbation, zh-CN | 0 of 14 | 0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 5.4 | 0.547 s, 5 calls |
| doc-15 | Sepsis, tempting note | 2 of 13 (untraceable_medication, wrong_source_type) | 0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 5.5 | 0.594 s, 5 calls |
| doc-16 | Urinary tract infection | 0 of 12 | 0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 6.8 | 0.531 s, 5 calls |
| doc-17 | Stroke, zh-CN | 0 of 13 | 0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 5.7 | 2.438 s, 5 calls |
| doc-18 | Cellulitis, on warfarin | 0 of 16 | 0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 6.4 | 2.844 s, 5 calls |
| doc-19 | Broken hip | 0 of 15 | 0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 3.5 | 4.375 s, 5 calls |
| doc-20 | Acute cholecystitis, zh-TW | 0 of 14 | 0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 5.5 | 1.078 s, 5 calls |
| doc-21 | Heart attack | 0 of 16 | 0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 4.9 | 0.641 s, 5 calls |
| doc-22 | Heart failure | 0 of 12 | 0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 5.0 | 0.656 s, 5 calls |
| doc-23 | Pneumonia, on warfarin, zh-TW | 0 of 16 | 0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 5.5 | 0.906 s, 5 calls |
| doc-24 | COPD exacerbation | 0 of 14 | 0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 5.0 | 1.469 s, 5 calls |
| doc-25 | Sepsis, tempting note | 2 of 14 (untraceable_medication, wrong_source_type) | 0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 6.5 | 0.61 s, 5 calls |
| doc-26 | Urinary tract infection, zh-TW | 0 of 12 | 0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 5.9 | 0.703 s, 5 calls |
| doc-27 | Stroke, on warfarin | 0 of 17 | 0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 4.9 | 0.594 s, 5 calls |
| doc-28 | Cellulitis | 0 of 11 | 0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 5.9 | 0.781 s, 5 calls |
| doc-29 | Broken hip, zh-TW | 0 of 16 | 0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 3.9 | 0.75 s, 5 calls |
| doc-30 | Acute cholecystitis | 0 of 14 | 0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 5.0/5.0/5.0/5.0 | 5.4 | 1.422 s, 5 calls |
