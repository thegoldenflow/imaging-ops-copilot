# Eval runs (spec 6.6)

One file per run of the one eval runner:

    cd apps/api && uv run python -m app.aiops.evalrun [AGENT ...] [--live] [--prompt <agent>@<n>] [--model <entry>] [--stamp]

(`make eval AGENT=...` on machines with GNU make.) Each `run-<YYYY-MM-DDTHHMMSS>.json` holds every agent evaluated
in that run: agent and prompt version, the prompt's hash, the model, the mode, the six common metrics
(schema_validity, grounding_rate, task_success, human_override_rate, latency p50 / p95, cost_per_case), the module's
own metrics and gates, the threshold, the verdict and the per-case results (failing cases with the traces captured
before the run's transaction was rolled back). Field list: docs/data-model.md, "AI Ops (WP10b)".

- **Release gate**: each agent's eval_status is derived from its newest run of the prompt version in use
  (app/aiops/release.py): below its `eval_threshold` or a module gate failed → failed; met with a live model →
  passed; met in mock mode → pending (a mock run is a baseline that shows the pipeline, not a model's quality), except
  pipeline evals (de-identification).
- **Prompt gate**: every prompt file's `eval_run` header line names the run that evaluated exactly its text; the
  version in use must have met its agent's threshold there (`uv run python -m app.aiops.promptgate`,
  tests/test_prompt_gate.py).
- **Whole case**: the metrics count the agent's own model calls. A suite whose harness also calls an agent that is
  not its own (triage extracts each requisition first; order review and documentation reconcile the medications)
  adds a `pipeline` block to each of its entries (those agents' calls, and the model calls, cost per case and latency
  of the whole case across every agent) and `pipeline_cost_usd` / `pipeline_latency_ms` to each case result.
- **Variants**: a run with `--prompt` or `--model` (a candidate for the comparison view) never changes the release
  status of the version in use.

The committed runs are mock baselines (no API key in this repository). Delete superseded runs only when no prompt
header refers to them.
