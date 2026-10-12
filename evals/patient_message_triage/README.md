# Patient-message triage eval (specs 6.4, 6.5)

The reference agent `patient_message_triage` (app/agents/library/patient_message_triage.py) sorts a message from a patient or family into a category and an urgency for the ward nurse. This folder is its eval set, and the first one that grows from human review: when the agent answers below its registry `confidence_threshold` (0.7), the LowConfidenceReviewWorkflow (spec 6.5) asks a nurse to confirm or correct the answer and appends the result here as a case with source `human_review`, then runs the regression below.

## Input resources

`cases.jsonl`, one case per line: `input` holds the prompt variables exactly as the agent sends them (a de-identified encounter context, the channel, the rule layer's red flags, the message in its untrusted block); identifiers appear only as placeholders such as `[PERSON_1]`. `expected` holds the judged fields. Twelve `authored` cases are synthetic messages written with the agent (`apps/api/scripts/build_triage_eval.py`, which keeps the human-review cases when it rewrites the authored ones); `human_review` cases also carry the model's own answer (`model_output`), the corrected fields, the reviewer's role, the confidence and the threshold.

## Output schema

The regression (`apps/api/scripts/triage_regression.py`, or the workflow's triggerRegression step; code in `app/agents/evalsets.py`) sends every case through the agent's current prompt and the LLM gateway and writes `report.json` and `report.md`: per case the expected and the answered category and urgency (urgency after the rule layer, as the agent applies it) and pass / fail, the totals per source, accuracy, gate, model mode.

## Eval set and threshold

Judged: category and urgency. Gate: 80% of cases match. Result with the mock provider (2026-10-09): 11 of 12 authored cases (0.917, passed); the miss is "parking for family members", which the prompt's definitions make "other" and the mock's keyword list calls "administrative". The labels follow the prompt's definitions, not the mock, so the mock does not score 100%. A human-review case the agent still gets wrong lowers the score until the prompt (a new `prompt_version`) handles it: that is the loop's purpose.

## Known failure modes

- With the mock provider the result shows the pipeline (cases, gateway, judging, report), not a model's quality; run the script with a configured provider for a real measurement.
- Twelve authored cases are few; categories at the edges (visiting hours, parking, belongings) are where people and models disagree, and the human-review cases are expected to come mostly from there.
- The context in the authored cases is a minimal encounter; real runs carry the de-identified encounter context of the stay.
