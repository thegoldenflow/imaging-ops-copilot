# Follow-up call eval (spec 7.4)

The follow-up call 48 hours after discharge (agent `followup_calls`, `app/modules/voice_services/followup.py`, rules
in `redflags.py`) on 50 scripted calls. Runner: `apps/api/scripts/voice_eval.py` (`--live`, `--check`); cases from
`apps/api/scripts/gen_voice_eval.py`; harness `apps/api/app/modules/voice_services/evals.py`. Results:
`report.json`, `report.md`. All data is synthetic.

## Input resources

`cases.jsonl`, 50 scripts: `{id, patient: en|zh, red_flag, codes[], answers{question key or kind: text}, note}`.
`patient` names a planted follow-up stay (app/ehr/seed/voice.py: an English and a Simplified Chinese patient
discharged 48 hours ago with signed WP7 instructions); the script's questions come from those instructions, and each
answer not given in the case is the plain all-clear for its kind ("no", "yes, I know", "It looks fine.", "没有", "知道"
…) in the patient's language. 15 scripts carry a red flag (9 English, 6
Chinese: fever as a reading or in Chinese numerals, chest pain or tightness, shortness of breath, heavy bleeding,
heavy wound drainage, a fall, cannot eat or drink, confusion, asking for a person, a yes to the patient's own warning
sign, a red flag told only at the end); 35 have none, including negations ("no chest pain"), normal temperatures,
worries that are not red flags and questions for the nurse.

## Output schema

Per script: escalated or not, by whom (`rules`, `model`, `low_confidence`, `model_unavailable`), the priority (stat,
urgent, routine), the rule codes, the classifier's status and confidence; the write-back (the call's Communication,
patient-reported Observations, nurse Tasks).

## Evaluation and thresholds

| Metric | Gate |
| --- | --- |
| Red-flag recall (the 15 red-flag scripts escalated) | ≥ 0.95 |
| With the model down, the rules alone escalate the red-flag scripts | 15 of 15 |
| Rule hits on scripts without a red flag | none |
| Rule codes as labelled | recorded |

Over-escalation (scripts without a red flag that reach a nurse) is recorded, not gated: a needless call-back is the
accepted cost of recall. Result 2026-10-10 (mock): recall 1.0, rules alone 15 of 15, no rule hit on a script without
a red flag, over-escalation 9 of 35 (0.257, all by the classifier). The gates also run in the backend suite
(`tests/test_voice_eval.py`).

## Known failure modes

- The scripts and the mock share an author: recall 1.0 with the mock shows the pipeline; paraphrases the rules do not
  know are left to the classifier, whose live recall is unmeasured until `--live`.
- Over-escalation with the mock comes from its conservative confidence on open worries; a live model's rate may
  differ either way.
- The scripts cover two planted patients; instructions with other medicines and red flags are covered by the unit
  tests only.
