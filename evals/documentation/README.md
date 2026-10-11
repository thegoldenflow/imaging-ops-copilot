# Documentation evals (spec 7.3)

The three documents of the documentation agent on 30 synthetic stays: the discharge summary (agent
`discharge_summary`, DischargeSummaryValidator), the bilingual patient instructions (`patient_instructions`) and the
SBAR handoff (`nursing_handoff`). Runner: `apps/api/scripts/documentation_eval.py` (`--live`, `--check`); cases from
`apps/api/scripts/gen_documentation_eval.py`; harness `apps/api/app/modules/documentation/evals.py`. Results:
`report.json`, `report.md`; the documents to rate: `documents.md`; the human rating sheet: `rating_sheet.md`. All
data is synthetic.

## Input resources

`cases.jsonl`, 30 cases. Each writes a patient into the EHR inside a rolled-back transaction
(`app/ehr/seed/doccases.py`): a Medicine A stay with its diagnoses, often a procedure, home medications, inpatient
orders, discharge prescriptions (continued, changed, new, stopped against the home list), vital-sign panels
(admission and the last 12 hours), abnormal laboratory results, sometimes a report, a pending test, an open task, a
flag and the discharge plan (CarePlan). 10 patients are Chinese-speaking (6 Simplified, 4 Traditional), 8 take
warfarin, 3 have a note naming a medicine that was only discussed. The run signs the discharge medication
reconciliation through WP6's service, then drafts, validates and signs the three documents through the
documentation service (the demo physician and nurse).

## Output schema

- Discharge summary: `{reason_for_admission, hospital_course[{date, text, source_refs}], discharge_diagnoses[],
  procedures[], medications{new[], changed[], stopped[], continued[]}, pending_results[], follow_up[], red_flags[]}`,
  every item with `source_refs[]` (app/modules/documentation/models.py).
- Patient instructions: one structure (`patient-instructions@1`: reason, medicines with purpose / times / cautions,
  follow-ups, red flags, daily care) rendered in English, Simplified and Traditional Chinese.
- Handoff: `{situation, background[], assessment[], recommendation[]}`, every statement with `source_refs[]`.
- Labels (`expected`): diagnosis and procedure codes, follow-up clinics, pending tests, the patient's language;
  medication categories are checked against the signed discharge medication table.

## Eval set and threshold

| Metric | Gate |
| --- | --- |
| Untraceable items after the validator (the oracle on what is signed) | 0 |
| Validator recall on the oracle's untraceable items in the raw drafts | 1.0 |
| Schema validity (every summary drafted and signed) | 1.0 |
| Bilingual consistency on 10 instructions (same fact items, same numbers, in three languages) | 1.0 |
| Injected hallucinations blocked (invented medication, made-up id, diagnosis and procedure not in the record, unprescribed drug in the course) | 5 of 5 |
| Human rating, four dimensions (completeness, accuracy, readability, format), 1-5 | mean >= 4 per dimension (rating_sheet.md, outstanding) |
| Automated proxies of the four dimensions | mean >= 4 (stand-in until the human rating) |

Monitoring, not gated: the raw (pre-validator) hallucination rate; the English Flesch-Kincaid grade (target 6-8 or
easier); Chinese without Latin letters; terms explained at first use and how many explanations came from the
knowledge graph; the summary's drafting time and model calls. A subset of the gates runs in the backend suite
(`tests/test_documentation_eval.py`); the five injected hallucinations also in `tests/test_documentation_validator.py`.

## Known failure modes

- The mock models and the cases share an author: a mock run is optimistic (proxies at 5) and shows the pipeline;
  `--live` and the human rating are outstanding, so the three agents stay `eval_status: pending`.
- The raw hallucinations the mock produces are of one kind (a medicine named in a note taken for a prescription). A
  live model can fail in other ways; the validator catches names and sources, not wrong facts about a traced item
  (a wrong date or dose next to a real drug name), which only the physician's review catches. Numbers are checked
  against the cited sources in the handoff only (a warning).
- Free text is scanned with the EHR's condition and procedure names and the knowledge graph's drug names: a
  negated mention ("no signs of pneumonia") is flagged red; a disease outside the EHR's list is not checked.
- The knowledge graph's facts are noisy, so most explanations are reviewed plain-language ones or the medicine's
  class; only facts that pass the selection rules come from the graph.
- English below grade 6 is accepted (easier); headings and table cells are left out of the grade.
