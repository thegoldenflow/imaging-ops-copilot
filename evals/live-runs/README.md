# Live model runs (archive)

Run files from evaluations on a real model (Gemini 3.5 Flash through Vertex AI), kept for the record. The release
gate and the prompt gate read only `evals/runs/` (or `EVAL_RUNS_DIR`), so nothing here changes an agent's release
status or the tests that expect the committed mock baselines. The findings and comparisons are in
[../live-run-2026-10-12.md](../live-run-2026-10-12.md); the problems behind them in
[../../docs/debugging-casebook.md](../../docs/debugging-casebook.md).

| File | What it is |
| --- | --- |
| `run-2026-10-11T235239.json` | Pilot: referral_weekly_summary and patient_message_triage |
| `run-2026-10-11T235646.json` | First full live run, every agent (hospital suites failed on a dev database four migrations behind) |
| `run-2026-10-12T014102.json` | Hospital agents and prompt injection on a migrated database, with the `maxItems` fix |
| `run-2026-10-12T024105.json` | Documentation agents with `LLM_TIMEOUT_S=90` (stopped at case 1: thinking filled the output budget) |
| `run-2026-10-12T225622.json` | The nine agents below their bar, after the thinking-level and eval-harness merges |
| `run-2026-10-13T021309.json` | After the stall retry and per-agent timeouts |
| `voice-thinking-ab/LOW`, `voice-thinking-ab/MINIMAL` | Interleaved A/B of the voice tier's thinking level (LOW 40/40, MINIMAL 29/40) |
| `2026-10-12-outputs/` | The committed eval outputs (`evals/results/`, voice reports, `evals/README.md`) as the first live runs rewrote them, before the mock baselines were restored |

To look at one in the app's Eval Center, point the API at this folder: `EVAL_RUNS_DIR=evals/live-runs` (the
newest run per agent then becomes its status in that process only). New live runs: write them here with
`EVAL_RUNS_DIR=../../evals/live-runs` from `apps/api` unless the owner decides a live run should set the release status.
