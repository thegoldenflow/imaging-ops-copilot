# Call-centre eval (spec 7.4)

The hospital call centre (agent `call_center`, module `app/modules/voice_services/callcenter.py`) on 40 scripted
inbound calls, text-simulated. Runner: `apps/api/scripts/voice_eval.py` (`--live`, `--check`); cases from
`apps/api/scripts/gen_voice_eval.py`; harness `apps/api/app/modules/voice_services/evals.py`. Results: `report.json`,
`report.md`. All data is synthetic.

## Input resources

`cases.jsonl`, 40 scripts (26 English, 14 Chinese): `{id, caller, language, turns[], intent, category}`. `caller`
names a planted demo caller (app/ehr/seed/voice.py: patients with booked appointments in the fracture, anticoagulation,
heart, surgery and lung clinics, one with none); `{id}` in a turn is replaced by the caller's identity line (name,
birth date, last four card digits) in the call's language, `{bad_id}` by a wrong one. Intents: reschedule 7, cancel
6, lookup 6, FAQ 7, prep 5, clinical 6, transfer 3; 6 of the 40 are identity failures (two wrong answers). Each
script runs through the call centre's own code (the dialog, the identity check, the Tool Gateway, the write-back)
inside a rolled-back transaction, with the configured model (`mock` without a key).

## Output schema

Per script: the intent recognised on the opening turn and its source (model or rules), whether it matches the label,
the call's outcome (resolved, requested, transferred), its actions (identity_verified, reschedule_requested,
cancel_requested, lookup, faq, prep_given, nurse_line, transfer, …) and the server time per caller turn.

## Eval set and threshold

| Metric | Gate |
| --- | --- |
| Intent accuracy (opening turn, 40 scripts) | ≥ 0.90 |
| Identity-failure scripts put through to a person | 1.0 (6 of 6) |

Monitored: accuracy per intent and per language, server time per turn (p50, p95; speech runs in the browser).
Result 2026-10-10 (mock): accuracy 1.0, identity failures 6 of 6 transferred; server time per turn p50 0.08 s on an
idle machine (0.33 s, p95 2.1 s while the backend suite ran alongside). The gates also run
in the backend suite (`tests/test_voice_eval.py`).

## Known failure modes

- The scripts and the mock model share an author: with the mock the numbers show the pipeline works, not a model's
  quality. `call_center` stays `eval_status: pending` until a `--live` run.
- The scripts are typed text; speech-recognition errors (names, numbers, mixed English and Chinese) are not covered.
- Only the opening turn's intent is scored; later turns (which appointment, which time, yes/no) are code and are
  covered by the unit tests (`tests/test_voice_services.py`).
