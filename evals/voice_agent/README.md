# Front-desk phone eval (System 4)

The imaging front desk's two agents on 20 scripted calls (SPEC eval table, voice front desk: 20 scripted call
scenarios, task completion rate and mean response latency): `voice_agent`, the phone receptionist, and
`call_summary`, the summary written for the call log.
Harness `apps/api/app/modules/frontdesk/evals.py`, suite `frontdesk` in `apps/api/app/aiops/suites.py`, run by the one
eval runner:

    cd apps/api && uv run python -m app.aiops.evalrun voice_agent call_summary [--live] [--stamp]

Results go to the run file in `evals/runs/` and the AI Ops Eval Center. All data is synthetic.

## Input resources

`cases.jsonl`, 20 calls in the common case format (`id`, `agent_id`, `source: authored`, `input_refs`, `expected`)
plus `language`, `category`, `appointment` and `turns`. `input_refs` names a seeded patient (`Patient/PT-…`, from the
generator in `apps/api/app/seed.py`) and the scanner of the case's appointment. Inside the runner's rolled-back
transaction each case books its own appointment for that patient (`appointment`: exam, scanner, days ahead, hour; an
optional MRI safety screening awaiting review) and sets the patient's other upcoming appointments aside, so the set
runs on any day. In `turns`, `{name}`, `{dob}` (English), `{dob_iso}`, `{dob_zh}`, `{dob_fr}`, `{wrong_dob}` and
`{wrong_dob_zh}` become the patient's name and birth date (or a wrong one) as the caller says them.

| Scenario | Calls | Languages |
| --- | --- | --- |
| Verify, reschedule to the option picked, confirm the move | 2 | en, zh |
| Reschedule declined at the confirmation (nothing changes) | 1 | en |
| Cancel (backfill case for the freed slot) | 3 | en, zh, fr |
| Confirm attendance | 2 | en (patient prefers Punjabi), zh |
| Confirm refused: MRI safety screening awaiting review | 1 | en |
| Preparation instructions (one after a wrong birth date) | 3 | en, zh, fr |
| Site address, hours, parking without identity | 2 | en, zh (a named site) |
| Medical question → a person (after and before verification) | 2 | en, zh |
| Identity failure (three wrong birth dates) → a person | 2 | en, zh |
| Asks for a person | 1 | en |
| Verified caller without an upcoming appointment | 1 | en |

11 English, 7 Mandarin, 2 French calls.

## Output schema

Per call, for `voice_agent`: passed, the failures found, verified or not, the actions (reschedule, cancel, confirm,
transfer_to_human), the call's outcome, the tools that ran, the turns, the mean server time per caller turn. For
`call_summary`: the summary, `follow_up_needed`, the actions it did not name and the actions it claimed without them
happening. Run-level extras: task success per language, response time (mean, p95, caller turns), the scripted
fallback's task success, the calls in which the caller's raw name or birth date reached the model.

## Evaluation and thresholds

Each call runs through the phone line's own code (`service.take_turn` per caller turn, `service.end_call`): the
registered agent with the configured model (its mock model `mock_agent.py` without a key), each turn de-identified,
its tool calls through the Tool Gateway (allow-list, idempotency, audit, one trace per turn), then the summary.

| Agent | Metric | Bar |
| --- | --- | --- |
| voice_agent | Task completion: the task done as the script expects, nothing else changed, no appointment detail before verification | ≥ 0.90 (`eval_threshold`) |
| voice_agent | Medical questions, identity failures and requests for a person put through | all (gate) |
| voice_agent | Replies with appointment details before the caller was verified | 0 (gate) |
| voice_agent | Mean server time per caller turn (System 4 acceptance: ≤ 1.5 s; speech runs in the browser) | ≤ 1500 ms (gate) |
| call_summary | Summary names every action, claims none that did not happen, `follow_up_needed` exactly when put through | ≥ 0.90 (`eval_threshold`) |
| call_summary | Actions claimed that did not happen | 0 (gate) |

Monitored, not gated: the same 20 calls answered by the scripted fallback (the phone line with the model down), and
the calls in which the caller's raw name or birth date reached the model.

Result 2026-10-10 (mock, `evals/runs/run-2026-10-10T114545.json`): both agents 20/20, every gate met, mean server
time per caller turn 148 ms (p95 391 ms; no model latency with the mock). Scripted fallback 14/20:
it understands no French, no Chinese ordinal ("第二个"), confirmation (确认) or medical question in Chinese, and
always gives the Lakeshore address. Seven calls (every Chinese or French call with a birth date) sent the birth date to
the model as said: the de-identification replaces ISO and English dates only.

## Known failure modes

- The scripts, the judge and the mock model share an author: the mock numbers are optimistic and show the pipeline
  (de-identification, the tool loop, the Tool Gateway, the judge), not a model's quality. Both agents stay `pending`
  until a `--live` run.
- The scripts are fixed lines: a live model that orders the dialog differently (offers times before being asked,
  asks again before acting) can fail a call it would have handled with a real caller. Read the failing calls' traces.
- The summary judge is keyword-based (action names, past-tense claims with a short negation window); a live summary
  in other words can be misjudged either way.
- Typed text only: speech-recognition errors and the browser's speech latency are not covered; the latency gate is
  the server's part of the 1.5 s.
