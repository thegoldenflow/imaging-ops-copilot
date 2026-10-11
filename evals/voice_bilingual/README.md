# Bilingual voice eval (spec 7.4)

The same scenarios in English and Chinese through the call centre and the follow-up call: 10 English and 10 Chinese
scripts in pairs. Runner: `apps/api/scripts/voice_eval.py`; cases from `apps/api/scripts/gen_voice_eval.py`; harness
`apps/api/app/modules/voice_services/evals.py`. Results: `report.json`, `report.md`. All data is synthetic.

## Input resources

`cases.jsonl`, 20 scripts: `{id, scenario, kind: call|followup, language, who, script[], expect}`. Call-centre
scenarios: FAQ, appointment lookup, prep instructions, a clinical question, a request for a person. Follow-up
scenarios: all clear, fever, heavy wound drainage, a missed medicine, asking for a person. Each pair has the same
script in both languages.

## Output schema

Per script: the expected outcome reached (intent and outcome for a call; escalated or not, priority, rule codes for a
follow-up), every agent reply in the caller's language (a Chinese reply contains Chinese, an English one none), the
write-back (Communication, Task, Observation as expected); per pair: the
same outcome in both languages.

## Eval set and threshold

| Metric | Gate |
| --- | --- |
| Scripts with the expected outcome, language and write-back | 20 of 20 |
| Pairs with the same outcome in both languages | 10 of 10 |

Result 2026-10-10 (mock): 20 of 20, 10 of 10 pairs.

## Known failure modes

- Traditional Chinese is covered by the unit tests and one demo caller, not by a pair here.
- Code-switching within one sentence (English drug names in Chinese) is not scored.
- Mock model: the numbers show the pipeline and the phrase tables, not a model's language quality.
