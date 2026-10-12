"""The release gate (spec 6.6): an agent's eval_status comes from its latest eval run, not from a hand-edited YAML.

For every agent the newest run in `evals/runs/` that evaluated its current prompt version (no comparison variant)
decides:

- below the threshold (`eval_threshold` in the agent's YAML, default 0.8) or a module gate failed -> `failed`;
- met with a live model (anthropic, gemini) -> `passed`;
- met in mock mode -> `pending`: a mock run is a baseline that shows the pipeline, not a model's quality (the
  repository's rule since phase 2), except a suite marked as a pipeline eval, whose metric does not depend on
  the model's judgement (the de-identification pipeline);
- not evaluated in that run (no eval set) -> `pending`.

Before an agent's first run the YAML's `eval_status` stays in force (the value set by hand before WP10b). The
runtime and the LLM gateway read the effective status through the registry, so a failed run watermarks the
agent's output in demo mode and refuses it in prod mode (`registry.gate`). Nothing is written into the YAML
files: the run files are the record.
"""

from __future__ import annotations

from app.aiops import runs

DEFAULT_THRESHOLD = 0.8


def threshold(spec) -> float:
    return spec.eval_threshold if spec.eval_threshold is not None else DEFAULT_THRESHOLD


def derive(spec, run: dict, entry: dict) -> dict:
    """The eval_status fields a run gives an agent."""
    verdict = runs.met(entry, threshold(spec))
    success = (entry.get("metrics") or {}).get("task_success")
    mode = entry.get("mode") or run.get("mode")
    base = {"run_id": f"evals/runs/{run['run_id']}.json", "source": "run", "task_success": success,
            "threshold": threshold(spec), "mode": mode, "run_at": run.get("started_at")}
    if verdict is None:
        why = entry.get("note") or entry.get("status") or "not evaluated"
        return {**base, "status": "pending", "note": f"Not evaluated in the latest run ({why})."}
    rate = f"task_success {success:.3f}" if success is not None else "module gates"
    failed_gates = [k for k, g in (entry.get("gates") or {}).items() if not g.get("passed")]
    if not verdict:
        detail = f"{rate} against threshold {threshold(spec):.2f}" + (
            f"; gates failed: {', '.join(failed_gates)}" if failed_gates else "")
        return {**base, "status": "failed", "note": f"Below the bar in the latest run ({detail})."}
    if runs.is_live(mode) or entry.get("pipeline_eval"):
        why = "live model" if runs.is_live(mode) else "pipeline eval (does not depend on the model's judgement)"
        return {**base, "status": "passed", "note": f"{rate} met threshold {threshold(spec):.2f} ({why})."}
    return {**base, "status": "pending",
            "note": f"Mock baseline met threshold {threshold(spec):.2f} ({rate}); a run on the configured model "
                    f"(--live) is needed to pass."}


def effective(spec):
    """The registry entry with the eval_status of its latest run (unchanged before the first run, for modules
    and for entries a test installed)."""
    if spec is None or not spec.is_agent:
        return spec
    found = runs.latest_for(spec.agent_id, prompt_version=spec.prompt_version)
    if found is None:
        return spec
    run, entry = found
    status = spec.eval_status.model_copy(update=derive(spec, run, entry))
    return spec.model_copy(update={"eval_status": status})
