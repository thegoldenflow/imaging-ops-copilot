"""Write evals/README.md: every agent's eval set, metric, threshold and latest result, plus the platform suites, the
module reports and the human rating sheets (spec 6.6, 8.4 WP10 "eval report summary").

    cd apps/api && uv run python scripts/eval_summary.py            # rewrite evals/README.md
    cd apps/api && uv run python scripts/eval_summary.py --stdout   # print it instead

It only reads: the run files in evals/runs/ (through app/aiops/runs.py, as the release gate and the AI Ops page do),
the agent registry (thresholds, effective eval status), the module reports and the rating sheets. It never runs an
eval and never touches evals/results/ or the run files. Refresh it after a new run:

    cd apps/api && uv run python -m app.aiops.evalrun [AGENT ...] [--live]
    cd apps/api && uv run python scripts/eval_summary.py

No database is needed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents import registry  # noqa: E402
from app.aiops import release, runs  # noqa: E402

API = Path(__file__).resolve().parents[1]
REPO = API.parents[1]
EVALS = REPO / "evals"
OUT = EVALS / "README.md"
RATING_SHEETS = (
    ("control_tower", "Control Tower narratives (7.1): accuracy 1-5 per narrative, gate mean >= 4",
     "evals/control_tower/narrator/rating_sheet.md"),
    ("discharge_summary, patient_instructions, nursing_handoff",
     "Documentation (7.3): completeness, accuracy, readability, format 1-5 per document, gate every mean >= 4",
     "evals/documentation/rating_sheet.md"),
)


def _json(rel: str) -> dict | None:
    path = REPO / rel
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _fmt(value) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.4g}"
    return str(value)


def _share(value) -> str:
    return f"{value:.0%}" if isinstance(value, float) and value <= 1 else _fmt(value)


def _pipeline(entry: dict) -> str:
    """The whole case, when the suite also runs agents that are not its own (their calls are in no agent's metrics)."""
    p = entry.get("pipeline")
    if not p:
        return ""
    est = "≈" if p.get("cost_estimated") else ""
    others = ", ".join(f"{a} {o['model_calls']} calls {est}${o['cost_usd']:.4f}" for a, o in p["other_agents"].items())
    return (f". Whole case, every agent the suite runs: {p['model_calls']} model calls, {est}$"
            f"{_fmt(p['cost_per_case_usd'])} per case (not in this agent's metrics: {others})")


def _evaluated(agent_id: str, prompt_version: str | None) -> tuple[dict, dict] | None:
    """The newest non-variant run that judged this prompt version (a run that found no eval set is skipped)."""
    for run in runs.summaries():
        entry = run["agents"].get(agent_id)
        if not entry or entry.get("variant") or run.get("variant"):
            continue
        if prompt_version is not None and entry.get("prompt_version") != prompt_version:
            continue
        if entry.get("status") in ("met", "below"):
            return run, entry
    return None


def _gates(entry: dict) -> tuple[str, list[str]]:
    gates = entry.get("gates") or {}
    failed = [name for name, g in gates.items() if not g.get("passed")]
    detail = [f"{name} {_fmt(g.get('value'))} (bar {_fmt(g.get('threshold'))}){'' if g.get('passed') else ' FAILED'}"
              for name, g in gates.items()]
    summary = f"{len(gates) - len(failed)}/{len(gates)}" if gates else "none"
    return summary, detail


def agent_rows() -> tuple[list[str], list[str], list[str]]:
    table = ["| Agent | Tier | Eval set | Threshold (task_success) | Latest result | Module gates met | Run | Release status |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    details: list[str] = []
    notes: list[str] = []
    for agent_id, spec in sorted(registry.agents().items()):
        if not spec.is_agent:
            continue
        latest = runs.latest_for(agent_id, prompt_version=spec.prompt_version)
        judged = _evaluated(agent_id, spec.prompt_version)
        status = spec.eval_status.status
        bar = f"{release.threshold(spec):.2f}"
        if judged is None:
            why = (latest[1].get("note") if latest else None) or "no run yet"
            table.append(f"| `{agent_id}` | {spec.risk_tier} | none | {bar} | not evaluated: {why} | | "
                         f"{latest[0]['run_id'] if latest else ''} | {status} |")
            continue
        run, entry = judged
        m = entry.get("metrics") or {}
        cases = f" ({m.get('cases')} cases)" if m.get("cases") else ""
        if m.get("task_success") is not None:
            result = f"{m['task_success']:.3f} ({m.get('cases_passed')}/{m.get('cases')})"
        else:
            result = "module gates only (report suite)"
        gates, detail = _gates(entry)
        mode = entry.get("mode") or run.get("mode")
        table.append(f"| `{agent_id}` | {spec.risk_tier} | `{entry.get('source')}`{cases} | {bar} | {result} | {gates} | "
                     f"{run['run_id']} ({mode}) | {status} |")
        extra = entry.get("extra") or {}
        extras = ", ".join(f"{k} {_fmt(v)}" for k, v in extra.items() if isinstance(v, (int, float)) and
                           not isinstance(v, bool))
        details.append(f"- `{agent_id}` ({entry.get('prompt_version')}, model {entry.get('model')}, {mode}): "
                       + ("; ".join(detail) if detail else "no module gates")
                       + (f". Extra: {extras}" if extras else "") + _pipeline(entry) + ".")
        if latest and latest[0]["run_id"] != run["run_id"]:
            notes.append(f"- `{agent_id}`: the newest run that names it ({latest[0]['run_id']}, status "
                         f"`{latest[1].get('status')}`) did not judge it, so the release gate and the AI Ops page say "
                         f"\"{spec.eval_status.note}\" The result above is from {run['run_id']}, the newest run that "
                         f"judged {spec.prompt_version}. A new run of the agent "
                         f"(`uv run python -m app.aiops.evalrun {agent_id}`) brings the two together.")
    return table, details, notes


def platform_rows() -> list[str]:
    out = ["| Suite | What it checks | Result | Source |", "| --- | --- | --- | --- |"]
    for run in runs.summaries():
        data = runs.load(run["run_id"])
        inj = (data.get("platform") or {}).get("injection")
        if inj:
            out.append(f"| Prompt injection (6.4) | attack tool calls a fully compromised model asks for; gate: none "
                       f"executed | {inj.get('cases_passed')}/{inj.get('cases')} cases refused, "
                       f"{inj.get('unauthorised_tool_calls')} unauthorised calls executed | `evals/injection/` "
                       f"({run['run_id']}, {run.get('mode')}) |")
            break
    wf = _json("evals/workflows/report.json")
    if wf:
        crit = wf.get("criteria") or []
        ok = sum(c.get("status") == "passed" for c in crit)
        tests = wf.get("tests") or {}
        out.append(f"| Durable workflows (6.5) | the 6.5 acceptance criteria (retry without a duplicate side effect, "
                   f"worker restart, sign-off timeout, low-confidence loop, replay) | {wf.get('status')}: {ok}/{len(crit)} "
                   f"criteria, {tests.get('passed')}/{tests.get('total')} tests | `evals/workflows/report.md` "
                   f"({wf.get('run_at')}) |")
    sim = _json("evals/simulator/day_run.json")
    if sim:
        out.append(f"| Day simulator (6.2) | a planned day with and without the ICU surge: event order, bed "
                   f"consistency, valid FHIR | {'passed' if sim.get('passed') else 'failed'} | "
                   f"`evals/simulator/day_run.json` ({sim.get('generated_at')}) |")
    flow = _json("apps/api/models/flow/report.json")
    if flow:
        parts = [f"{name} {m['metric'].upper()} {m['model']} vs baseline {m['baseline']}"
                 + ("" if m.get("passed") else " FAILED") for name, m in flow.get("models", {}).items()]
        out.append(f"| Flow models (7.1) | the four Control Tower models against the spec's baselines (synthetic "
                   f"data) | {'passed' if flow.get('passed') else 'failed'}: {'; '.join(parts)} | "
                   f"`apps/api/models/flow/report.md` ({flow.get('trained_at')}) |")
    for kind in ("offline", "temporal"):
        story = _json(f"evals/storyline/report_{kind}.json")
        if story:
            out.append(f"| Hospital storyline (8.2), {kind} | the 12 steps walked through the API: proposal, signature, "
                       f"audit | {'passed' if story.get('passed') else 'failed'}: {story.get('steps_ok')}/12 steps, "
                       f"walk {story.get('walk_seconds')} s | `evals/storyline/report_{kind}.md` ({story.get('run_at')}) |")
    return out


def module_report_rows() -> list[str]:
    out = ["| Report | Result | Gate | Source |", "| --- | --- | --- | --- |"]
    cc = _json("evals/call_center/report.json")
    if cc:
        out.append(f"| Call centre intents ({cc.get('cases')} scripts) | intent accuracy {cc.get('intent_accuracy')}, "
                   f"identity failures put through {_share(cc.get('identity_failures_transferred'))} of "
                   f"{cc.get('identity_failure_cases')} | intent >= {cc.get('intent_gate')}, all identity failures "
                   f"put through | `evals/call_center/report.md` ({cc.get('mode')}, {cc.get('run_at')}) |")
    fu = _json("evals/followup/report.json")
    if fu:
        over = fu.get("over_escalation") or {}
        rules = fu.get("rules_only") or {}
        out.append(f"| Follow-up calls ({fu.get('cases')} scripts, {fu.get('red_flag_cases')} with red flags) | "
                   f"red-flag recall {fu.get('recall')}; rules alone escalate {rules.get('escalated_by_rules')}/"
                   f"{rules.get('cases')}; over-escalation {over.get('rate')} | recall >= {fu.get('recall_gate')} | "
                   f"`evals/followup/report.md` ({fu.get('mode')}, {fu.get('run_at')}) |")
    vb = _json("evals/voice_bilingual/report.json")
    if vb:
        out.append(f"| Bilingual voice ({vb.get('cases')} scripts in English and Chinese pairs) | pairs with the same "
                   f"outcome {vb.get('pairs_consistent')} | every pair consistent | `evals/voice_bilingual/report.md` "
                   f"({vb.get('mode')}, {vb.get('run_at')}) |")
    return out


def _sheet(rel: str) -> tuple[int, int]:
    """(rows scored, rows) of a rating sheet: a row counts when every score column is filled in."""
    lines = [ln for ln in (REPO / rel).read_text(encoding="utf-8").splitlines() if ln.startswith("|")]
    header = [c.strip() for c in lines[0].strip("|").split("|")]
    cols = [i for i, h in enumerate(header) if h.lower().startswith("score") or h in ("C", "A", "R", "F")]
    rows = [[c.strip() for c in ln.strip("|").split("|")] for ln in lines[2:]]
    scored = sum(all(i < len(r) and r[i] for i in cols) for r in rows)
    return scored, len(rows)


def render() -> str:
    found = runs.summaries()
    live = [r for r in found if runs.is_live(r.get("mode"))]
    table, details, notes = agent_rows()
    sheets = [(who, what, rel, *_sheet(rel)) for who, what, rel in RATING_SHEETS if (REPO / rel).exists()]
    unscored = [s for s in sheets if s[3] < s[4]]
    out = [
        "# Eval summary",
        "",
        "Generated by `apps/api/scripts/eval_summary.py` from the run files in `evals/runs/`, the agent registry and the "
        "module reports; do not edit it by hand. It only reads results. Refresh it after a run:",
        "",
        "```bash",
        "cd apps/api",
        "uv run python -m app.aiops.evalrun            # every agent, mock model (or: make eval AGENT=order_review)",
        "uv run python -m app.aiops.evalrun --live     # the configured model (Claude or Gemini key in .env)",
        "uv run python scripts/eval_summary.py",
        "```",
        "",
        "## What these numbers are",
        "",
    ]
    if live:
        out.append(f"- {len(live)} of the {len(found)} committed runs used a real model (latest: {live[0]['run_id']}, "
                   f"{live[0]['mode']}); the others are mock baselines.")
    else:
        out.append(f"- **All {len(found)} committed runs are mock-model baselines.** The mock provider returns built-in "
                   "outputs written alongside the eval cases, so these numbers show that the pipeline works (context "
                   "assembly, de-identification, schema validation, guards, evidence checks, review queues, write-back), "
                   "not a model's quality. Mock costs are list-price estimates.")
        out.append("- **Runs on a real model (Claude or Gemini) are still to be done.** Until then the release gate keeps "
                   "every agent `pending` (de-identification, a pipeline eval, is the exception), so AI output carries "
                   "**Not evaluated** in demo mode and prod mode refuses those agents.")
    if unscored:
        out.append("- **The human rating sheets are still to be scored:** "
                   + "; ".join(f"{what} (`{rel}`: {scored} of {total} rows scored)" for _, what, rel, scored, total in unscored)
                   + ".")
    out += [
        "- All data is synthetic. Even a live run would show performance on synthetic cases written alongside the rules, "
        "not clinical performance.",
        "",
        "Status words: **pending** = met its bar on the mock model (or not judged yet); **passed** = met its bar on a "
        "live model, or a pipeline eval; **failed** = below its threshold or a module gate failed. `task_success` is a "
        "strict per-case rate; thresholds (`eval_threshold`) are in each agent's YAML in `apps/api/config/agents/` with "
        "the reason in a comment (default 0.8).",
        "",
        "## Agents",
        "",
        *table,
        "",
    ]
    if notes:
        out += ["Notes:", "", *notes, ""]
    out += ["### Module gates per agent", "", *details, "",
            "## Platform suites and model reports", "", *platform_rows(), "",
            "## Module reports behind the voice agents", "",
            "The runner reads these reports for `call_center` and `followup_calls` (their own script runs them: "
            "`cd apps/api && uv run python scripts/voice_eval.py`).", "", *module_report_rows(), "",
            "## Human ratings", "",
            "| Agents | Rating | Sheet | Scored |", "| --- | --- | --- | --- |"]
    out += [f"| {who} | {what} | `{rel}` | {scored} of {total} |" for who, what, rel, scored, total in sheets]
    out += ["", "## Where the eval sets live", "",
            "- Hospital agents: `evals/<agent or module>/cases.jsonl` with a README of four sections (input resources, "
            "output schema, eval set and threshold, known failure modes); reports `report.md` / `report.json` next to "
            "them.",
            "- Imaging agents (phases 2–4): `evals/datasets/*.json`, legacy per-task results in `evals/results/` "
            "(rewritten by every run of those agents).",
            "- Runs: `evals/runs/run-<timestamp>.json`, one per runner invocation ([runs/README.md](runs/README.md)); "
            "the release and prompt gates read them.",
            "- In the app: **AI Ops → Eval Center** (per-case results and the traces of failing cases) and **Compare**.",
            ""]
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stdout", action="store_true", help="print instead of writing evals/README.md")
    args = parser.parse_args()
    text = render()
    if args.stdout:
        sys.stdout.write(text)
        return
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {OUT.relative_to(REPO)}")


if __name__ == "__main__":
    from _stdio import utf8_stdio

    utf8_stdio()
    main()
