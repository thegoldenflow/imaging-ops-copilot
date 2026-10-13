"""The one eval runner (spec 6.6 `make eval [AGENT]`): every agent's eval set, one run file.

    cd apps/api && uv run python -m app.aiops.evalrun                     # every agent (mock model: a baseline)
    cd apps/api && uv run python -m app.aiops.evalrun order_review        # one agent (or several)
    cd apps/api && uv run python -m app.aiops.evalrun --live              # the configured model (LLM_PROVIDER)
    cd apps/api && uv run python -m app.aiops.evalrun --prompt patient_message_triage@2   # a candidate prompt
    cd apps/api && uv run python -m app.aiops.evalrun requisition_triage --model claude-fast   # another model
    add --stamp to write the run id into the eval_run header line of each prompt the run approved
    add --check to exit 1 when an agent is below its threshold
    (make eval [AGENT=...] does the same on machines with GNU make)

For each agent the runner finds its suite (app/aiops/suites.py), runs the module's harness through the same LLM
gateway and agent runtime as the app, inside a database transaction that is rolled back, and records the six
common metrics, the module's own metrics and gates, the threshold and the per-case results in
`evals/runs/<run id>.json`. The release gate (app/aiops/release.py) derives every agent's eval_status from the
latest run. A run with the mock model is a baseline: it shows the pipeline, not a model's quality.

The six metrics: schema_validity (outputs that passed the schema: the harness's verdict per case, else the share of
model calls that validated), grounding_rate (cases whose evidence references all resolve, where the agent cites
evidence), task_success (cases that matched the expectation: the pass rate compared with the threshold),
human_override_rate (from the production traces: decided outputs a person corrected or rejected), latency p50 / p95
(model time per case) and cost_per_case (the gateway's cost; list-price estimate for mock calls, app/aiops/cost.py).

These count the agent's own model calls. When a suite's harness also calls an agent that is not one of the suite's
(the extraction before each triage call, the reconciliation in the order review and documentation suites), each entry
of that suite carries the suite's `pipeline` block: the other agents' calls, and the whole case across every agent
(model calls, cost per case, latency); each case result carries `pipeline_cost_usd` and `pipeline_latency_ms`.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import subprocess
import sys
import time
import traceback
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from app.aiops import collect, cost, release, runs, suites

OVERRIDE_DECISIONS = frozenset({"reject", "rejected", "corrected", "modify", "modified", "edited"})
TRIM = 600
MAX_TRACES = 10  # traces kept per failing case (a phone call has a run per caller turn)
ANSWERED = ("ok", "retried_ok")


# ---------- helpers ----------


def _trim(value):
    if value is None or isinstance(value, (bool, int, float)):
        return value
    text = json.dumps(value, ensure_ascii=False, default=str)
    return value if len(text) <= TRIM else text[:TRIM] + "…"


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))]


def _share(flags: list[bool]) -> float | None:
    return round(sum(flags) / len(flags), 4) if flags else None


def prompt_hash(version: str | None) -> str | None:
    if not version:
        return None
    from app.agents import prompts

    try:
        return hashlib.sha256(prompts.text_of(version).encode()).hexdigest()[:16]
    except prompts.PromptFileError:
        return None


def override_rates(store) -> dict[str, dict]:
    """Per agent, from the traces: outputs a person decided, and how many of them were corrected or rejected."""
    from app.agents import trace as traces

    out: dict[str, dict] = defaultdict(lambda: {"decided": 0, "overridden": 0})
    try:
        rows = list(traces.table(store).values())
    except RuntimeError:
        return {}
    for t in rows:
        action = t.human_action or {}
        if not action.get("decision"):
            continue
        out[t.agent_id]["decided"] += 1
        out[t.agent_id]["overridden"] += action["decision"] in OVERRIDE_DECISIONS
    return dict(out)


def _git() -> dict:
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                                timeout=5).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain", "--", "."], capture_output=True, text=True,
                                    timeout=5).stdout.strip())
        return {"commit": commit or None, "dirty": dirty}
    except (OSError, subprocess.SubprocessError):
        return {"commit": None, "dirty": None}


@contextlib.contextmanager
def _rolled_back(store=None):
    """The store a suite runs in: the caller's (it owns the transaction) or a new one that is rolled back."""
    if store is not None:
        yield store
        return
    from app.core.store import ConnectionSource, Store, get_engine, set_ambient_store

    conn = get_engine().connect()
    outer = conn.begin()
    st = Store(ConnectionSource(conn))
    set_ambient_store(st)
    try:
        yield st
    finally:
        set_ambient_store(None)
        outer.rollback()
        conn.close()


@contextlib.contextmanager
def _variant(agent_id: str | None, prompt: str | None, model: str | None):
    from app.agents import registry

    if not agent_id or not (prompt or model):
        yield None
        return
    spec = registry.require_agent(agent_id)
    update: dict = {}
    if prompt:
        update["prompt_version"] = prompt
    if model:
        update["model_policy"] = spec.model_policy.model_copy(update={"model": model})
    with registry.temporary(agent_id, spec.model_copy(update=update)):
        yield {k: v for k, v in (("prompt_version", prompt), ("model", model)) if v}


# ---------- one suite ----------


def _agent_entry(spec, suite, result, col, gateway, overrides, variant) -> dict:
    from app.agents import models

    agent = spec.agent_id
    rows = [r for r in result.rows if (r.get("agent_id") or suite.agents[0]) == agent]
    calls = [c for c in col.calls if c.agent_id == agent]
    mode = result.mode or gateway.mode
    entry_model = models.resolve(spec, mode if mode in ("anthropic", "gemini", "mock") else gateway.mode)
    by_case: dict[str | None, list] = defaultdict(list)
    for c in calls:
        by_case[c.case_id].append(c)
    costs = {k: sum(cost.call_cost(c.mode, c.model, c.cost_usd, c.tokens_in, c.tokens_out)[0] for c in v)
             for k, v in by_case.items()}
    latencies = {k: sum(c.latency_ms for c in v) for k, v in by_case.items()}
    tagged = [k for k in by_case if k is not None]
    lat_values = [latencies[k] for k in tagged] if tagged else [c.latency_ms for c in calls]
    total_cost = sum(costs.values())
    n = len(rows)
    validity_rows = [r["schema_valid"] for r in rows if r.get("schema_valid") is not None]
    if validity_rows:
        schema = _share(validity_rows)
    else:
        validated = [c.outcome in ("ok", "retried_ok") for c in calls if c.outcome in ("ok", "retried_ok", "needs_human")]
        schema = _share(validated)
    ov = overrides.get(agent)
    estimated = mode == "mock"
    metrics = {
        "schema_validity": schema,
        "grounding_rate": _share([r["grounded"] for r in rows if r.get("grounded") is not None]),
        "task_success": _share([bool(r["passed"]) for r in rows]),
        "human_override_rate": round(ov["overridden"] / ov["decided"], 4) if ov and ov["decided"] else None,
        "latency_p50_ms": _pct(lat_values, 0.5),
        "latency_p95_ms": _pct(lat_values, 0.95),
        "cost_per_case_usd": round(total_cost / n, 6) if n and calls else None,
        "cost_estimated": estimated,
        "cases": n, "cases_passed": sum(1 for r in rows if r["passed"]), "model_calls": len(calls),
        "tokens_in": sum(c.tokens_in for c in calls), "tokens_out": sum(c.tokens_out for c in calls),
        "human_decisions": ov["decided"] if ov else 0,
    }
    gates = dict(result.gates.get(agent, {}))
    if entry_model is not None and metrics["cost_per_case_usd"] is not None:
        gates["cost_per_case"] = {"value": metrics["cost_per_case_usd"], "threshold": entry_model.max_cost_per_case,
                                  "passed": metrics["cost_per_case_usd"] <= entry_model.max_cost_per_case}
    case_results = []
    for r in rows:
        case_runs = sorted(k for k, v in col.runs.items() if v == r["id"])
        case_results.append({"id": r["id"], "source": r.get("source", "authored"), "passed": bool(r["passed"]),
                             "schema_valid": r.get("schema_valid"), "grounded": r.get("grounded"),
                             "latency_ms": latencies.get(r["id"]) if r["id"] in latencies else r.get("latency_ms"),
                             "cost_usd": round(costs[r["id"]], 6) if r["id"] in costs else None,
                             "expected": _trim(r.get("expected")), "got": _trim(r.get("got")),
                             # the model calls of this agent that did not answer, with the provider's reason
                             "call_errors": [{"outcome": c.outcome, "error": c.error}
                                             for c in by_case.get(r["id"], []) if c.outcome not in ANSWERED],
                             # this agent's runs; the other agents' runs of the same case (a suite can run several
                             # agents per case: a pipeline step before this one, or the next agent after it)
                             "run_ids": [k for k in case_runs if col.run_agents.get(k) == agent],
                             "related_run_ids": [k for k in case_runs if col.run_agents.get(k) != agent]})
    entry = {
        "suite": suite.id, "suite_kind": suite.kind, "suite_title": suite.title, "source": suite.source,
        "agent_version": spec.version, "kind": spec.kind, "risk_tier": spec.risk_tier,
        "prompt_version": spec.prompt_version, "prompt_sha256": prompt_hash(spec.prompt_version),
        "model": gateway.model_for_agent(spec), "model_key": entry_model.name if entry_model else None,
        "mode": mode, "variant": variant, "threshold": release.threshold(spec),
        "pipeline_eval": suite.pipeline_eval, "metrics": metrics, "extra": result.extra.get(agent, {}),
        "gates": gates, "notes": result.notes, "module_run_at": result.run_at, "case_results": case_results,
    }
    verdict = runs.met(entry, release.threshold(spec))
    entry["status"] = ("met" if verdict else "below") if verdict is not None else (
        "no_cases" if not n else "gates_only")
    entry["met"] = verdict
    return entry


def _failed(trace) -> bool:
    """A run that went wrong on its own terms: it did not complete, a model call did not answer or a tool failed."""
    return (trace.outcome not in ("completed", "running") or any(c.status not in ANSWERED for c in trace.llm_calls)
            or any(t.status in ("refused", "failed") for t in trace.tool_calls))


def pick_traces(own: list, related: list) -> tuple[list, int]:
    """The traces kept for a failing case, in the order the runs ran, and how many were left out.

    The agent's failing runs first, then its other runs (every turn of a phone call), up to MAX_TRACES. When none of
    its own runs failed (or it has none, because a pipeline step before it failed), the other agents' failing runs of
    the case too: the failure is there."""
    failing = [t for t in own if _failed(t)]
    picked = failing + [t for t in own if not _failed(t)]
    if not failing:
        picked += [t for t in related if _failed(t)]
    return sorted(picked[:MAX_TRACES], key=lambda t: t.run_id), max(0, len(picked) - MAX_TRACES)


def _snapshots(col, entry) -> None:
    """The traces of the failing cases, captured before the transaction is rolled back (6.6: any failure reaches
    its trace)."""
    from app.agents import trace as traces

    def found(run_ids: list[str]) -> list:
        return [t for t in map(traces.get, run_ids) if t is not None]

    for case in entry["case_results"]:
        if case["passed"] or not (case["run_ids"] or case["related_run_ids"]):
            continue
        kept, dropped = pick_traces(found(case["run_ids"]), found(case["related_run_ids"]))
        case["traces"] = [t.model_dump(mode="json") for t in kept]
        if dropped:
            case["traces_dropped"] = dropped


def _pipeline(suite, result, col) -> tuple[dict | None, dict]:
    """The whole case across every agent the suite runs, when the harness also calls an agent that is not one of
    the suite's (the extraction before triage, the reconciliation beside order review and the discharge documents):
    those calls are in no entry's metrics. The agents' own metrics stay as they are (their cost gate compares an
    agent with its own model's max). The block is the suite's, the same on each of its entries; with it, the cost
    and model time of each case across all its calls. (None, {}) when every call is a suite agent's."""
    if all(c.agent_id in suite.agents for c in col.calls):
        return None, {}
    costs: dict[str | None, float] = defaultdict(float)
    latencies: dict[str | None, int] = defaultdict(int)
    others: dict[str, list] = defaultdict(list)
    estimated = False
    for c in col.calls:
        usd, est = cost.call_cost(c.mode, c.model, c.cost_usd, c.tokens_in, c.tokens_out)
        costs[c.case_id] += usd
        latencies[c.case_id] += c.latency_ms
        estimated |= est
        if c.agent_id not in suite.agents:
            others[c.agent_id].append((c, usd))
    cases = {r["id"] for r in result.rows}
    tagged = [v for k, v in latencies.items() if k is not None]
    block = {
        "other_agents": {agent: {"model_calls": len(v),
                                 "failed_calls": sum(c.outcome not in ("ok", "retried_ok") for c, _ in v),
                                 "cost_usd": round(sum(usd for _, usd in v), 6),
                                 "tokens_in": sum(c.tokens_in for c, _ in v),
                                 "tokens_out": sum(c.tokens_out for c, _ in v),
                                 "cases": len({c.case_id for c, _ in v if c.case_id is not None})}
                         for agent, v in sorted(others.items())},
        "model_calls": len(col.calls), "cases": len(cases),
        "cost_per_case_usd": round(sum(costs.values()) / len(cases), 6) if cases else None,
        "cost_estimated": estimated,
        "latency_p50_ms": _pct(tagged, 0.5), "latency_p95_ms": _pct(tagged, 0.95),
    }
    per_case = {k: {"pipeline_cost_usd": round(costs[k], 6), "pipeline_latency_ms": latencies[k]}
                for k in costs if k is not None}
    return block, per_case


def run_suite(suite, specs, *, gateway, store, overrides, variant, live, on_case=None) -> dict[str, dict]:
    from app.llm.gateway import get_gateway, set_gateway

    previous = get_gateway()
    out: dict[str, dict] = {}
    started = time.monotonic()
    try:
        set_gateway(gateway)
        with contextlib.ExitStack() as stack:
            st = None if suite.own_transaction else stack.enter_context(_rolled_back(store))
            with collect.Collector(on_case=on_case) as col:
                result = suite.run(suites.Context(store=st, live=live))
                set_gateway(gateway)  # some harnesses reset the gateway when they finish
                pipeline, per_case = _pipeline(suite, result, col)
                for spec in specs:
                    entry = _agent_entry(spec, suite, result, col, gateway, overrides, variant)
                    if pipeline is not None:
                        entry["pipeline"] = pipeline
                        for case in entry["case_results"]:
                            case.update(per_case.get(case["id"], {"pipeline_cost_usd": None,
                                                                  "pipeline_latency_ms": None}))
                    if st is not None:
                        _snapshots(col, entry)
                    out[spec.agent_id] = entry
    except Exception as e:  # one broken suite must not stop the others
        for spec in specs:
            out[spec.agent_id] = {"suite": suite.id, "suite_kind": suite.kind, "suite_title": suite.title,
                                  "source": suite.source, "agent_version": spec.version,
                                  "prompt_version": spec.prompt_version, "status": "error", "met": None,
                                  "error": f"{type(e).__name__}: {e}"[:500],
                                  "traceback": traceback.format_exc()[-2000:]}
    finally:
        set_gateway(previous)
    for entry in out.values():
        entry["seconds"] = round(time.monotonic() - started, 1)
    return out


# ---------- the run ----------


def _gateway(live: bool):
    from app.llm.gateway import LlmGateway, make_provider
    from app.llm.providers import MockProvider

    provider = make_provider() if live else MockProvider(latency_s=0)
    if live and provider.mode == "mock":
        raise SystemExit("--live needs LLM_PROVIDER=anthropic or gemini with its API key in apps/api/.env")
    return collect.CollectingGateway(provider)


def _stall_rates(counts: dict) -> dict:
    """One task's (or the run's) attempt counts with the two rates that tell a burst of stalls apart (casebook 3.15):
    how often a call's first attempt stalled, and how often the attempt after a stall stalled again. Independent
    stalls give about the same rate twice; a much higher second rate means they came in a burst, and a retry only
    waited longer."""
    def rate(part: str, whole: str) -> float | None:
        return round(counts[part] / counts[whole], 4) if counts[whole] else None

    return {**counts, "first_attempt_stall_rate": rate("first_stalled", "calls"),
            "stall_after_stall_rate": rate("stalled_after_stall", "after_stall")}


def _stall_summary(by_task: dict[str, dict]) -> dict | None:
    """The run file's `stalls`: the attempts of the run's Gemini calls (AttemptStats.since) in total and per agent;
    None when the run made none (mock or Anthropic)."""
    if not by_task:
        return None
    total = {key: sum(c[key] for c in by_task.values()) for key in next(iter(by_task.values()))}
    return {**_stall_rates(total), "by_agent": {task: _stall_rates(c) for task, c in sorted(by_task.items())}}


def _stall_note(stalls: dict) -> str:
    return (f"The model API stalled (504 or no answer before the deadline) on {stalls['first_stalled']} of "
            f"{stalls['calls']} first attempts ({stalls['first_attempt_stall_rate']:.0%}); "
            f"{stalls['stalled_after_stall']} of {stalls['after_stall']} retries after a stall stalled again"
            + (f" ({stalls['stall_after_stall_rate']:.0%})" if stalls["after_stall"] else "")
            + f". Calls a retry saved: {stalls['saved']}; calls lost to stalls: {stalls['lost']}.")


def new_run_id() -> str:
    base = "run-" + datetime.now().strftime("%Y-%m-%dT%H%M%S")
    run_id, n = base, 1
    while runs.path_of(run_id).exists():
        n += 1
        run_id = f"{base}-{n}"
    return run_id


def run(agent_ids: list[str] | None = None, *, live: bool = False, prompt: str | None = None,
        model: str | None = None, store=None, write: bool = True, platform: bool | None = None,
        progress=print, trigger: str = "cli", on_case=None) -> dict:
    """Run the eval of the given agents (default: every registered agent) and write the run file."""
    import app.main  # noqa: F401  (registers every module's mock fixtures)
    from app.agents import registry

    if prompt:
        agent_from_prompt = prompt.partition("@")[0]
        agent_ids = agent_ids or [agent_from_prompt]
        if agent_ids != [agent_from_prompt]:
            raise SystemExit(f"--prompt {prompt} evaluates {agent_from_prompt} only")
    if model and (not agent_ids or len(agent_ids) != 1):
        raise SystemExit("--model needs exactly one agent")
    all_agents = {k: v for k, v in registry.agents().items() if v.is_agent}
    unknown = [a for a in agent_ids or [] if a not in all_agents]
    if unknown:
        raise SystemExit(f"not registered agents: {', '.join(unknown)}")
    targets = agent_ids or sorted(all_agents)
    platform = (agent_ids is None) if platform is None else platform
    gateway = _gateway(live)
    attempts = getattr(gateway.provider, "stats", None)  # Gemini counts its attempts (app/llm/providers.py)
    attempts_before = attempts.snapshot() if attempts is not None else {}
    run_id = new_run_id()
    started_at = datetime.now()
    with _rolled_back(store) as st:
        overrides = override_rates(st)
    agents: dict[str, dict] = {}
    groups: dict[str, tuple] = {}
    for agent_id in targets:
        suite = suites.suite_for(all_agents[agent_id])
        if suite is None:
            agents[agent_id] = {"suite": None, "agent_version": all_agents[agent_id].version,
                                "prompt_version": all_agents[agent_id].prompt_version,
                                "prompt_sha256": prompt_hash(all_agents[agent_id].prompt_version),
                                "status": "no_eval_set", "met": None,
                                "note": "no eval set (no adapter, no evals/<agent>/cases.jsonl, no module report)"}
            continue
        groups.setdefault(suite.id, (suite, []))[1].append(agent_id)
    target = agent_ids[0] if agent_ids and len(agent_ids) == 1 else None
    with _variant(target, prompt, model) as variant:
        for suite, members in groups.values():
            progress(f"{suite.id}: {', '.join(members)} ...")
            specs = [registry.require_agent(a) for a in members]
            entries = run_suite(suite, specs, gateway=gateway, store=store, overrides=overrides,
                                variant=variant, live=live, on_case=on_case)
            agents.update(entries)
            for a in members:
                e = entries[a]
                m = e.get("metrics") or {}
                progress(f"  {a}: {e['status']}" + (f" task_success {m.get('task_success')} "
                                                   f"({m.get('cases_passed')}/{m.get('cases')}), threshold "
                                                   f"{e.get('threshold')}" if m else f" {e.get('error', '')}"))
    stalls = _stall_summary(attempts.since(attempts_before)) if attempts is not None else None
    if stalls is not None:
        for agent_id, counts in stalls["by_agent"].items():
            if agent_id in agents:
                agents[agent_id]["stalls"] = counts
        progress(f"stalls: {_stall_note(stalls)}")
    platform_suites = {}
    if platform:
        progress("platform: prompt injection ...")
        try:
            with _rolled_back(store) as st:
                inj = suites.injection_run(st)
            platform_suites["injection"] = {"title": "Prompt injection (6.4)", "source": "evals/injection/cases.jsonl",
                                            **inj}
            for agent_id, entry in agents.items():
                mine = [r for r in inj["rows"] if r["agent_id"] == agent_id]
                if mine and entry.get("gates") is not None:
                    ok = sum(r["passed"] for r in mine)
                    entry["gates"]["injection_refused"] = {"value": f"{ok}/{len(mine)}", "threshold": "all",
                                                           "passed": ok == len(mine)}
                    entry["met"] = runs.met(entry, entry.get("threshold"))
                    if entry["met"] is not None:
                        entry["status"] = "met" if entry["met"] else "below"
        except Exception as e:  # noqa: BLE001
            platform_suites["injection"] = {"title": "Prompt injection (6.4)", "error": f"{type(e).__name__}: {e}"}
        for name in ("workflows", "simulator"):
            if (rep := suites.platform_report(name)) is not None:
                platform_suites[name] = {**rep, "kind": "report"}
    record = {"run_id": run_id, "started_at": started_at.isoformat(timespec="seconds"),
              "finished_at": datetime.now().isoformat(timespec="seconds"), "mode": gateway.mode,
              "baseline": gateway.mode == "mock", "trigger": trigger, "requested": agent_ids,
              "variant": {"agent": target, **variant} if (target and (prompt or model)) else None,
              "git": _git(), "agents": agents, "platform": platform_suites, "stalls": stalls,
              "note": ("Mock model: a baseline that shows the pipeline (context, schema, guards, evidence), not a "
                       "model's quality; mock costs are list-price estimates.") if gateway.mode == "mock" else
                      "Live model run." + (f" {_stall_note(stalls)}" if stalls and stalls["stalled"] else "")}
    stub = {"run_id": run_id, "started_at": record["started_at"], "mode": gateway.mode}
    for agent_id, entry in agents.items():
        spec = all_agents[agent_id]
        entry["eval_status"] = release.derive(spec, stub, entry)["status"] if not record["variant"] else None
    if write:
        path = runs.path_of(run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, indent=1, ensure_ascii=False, default=str) + "\n", encoding="utf-8",
                        newline="\n")
        runs.invalidate()
        progress(f"wrote {path}")
    return record


def stamp(record: dict, progress=print) -> list[str]:
    """Write the run id into the eval_run header line of each prompt this run approved: an active prompt whose
    agent met its threshold, the candidate version a variant run evaluated, or the prompt of an agent without an
    eval set (pinned to the text the run recorded; the gate reports it as not judged)."""
    from app.aiops import promptgate

    stamped = []
    for agent_id, entry in record["agents"].items():
        version = entry.get("prompt_version")
        if not version or not entry.get("prompt_sha256"):
            continue
        if record.get("variant") or entry.get("met") or entry.get("status") == "no_eval_set":
            promptgate.set_eval_run(version, record["run_id"])
            stamped.append(version)
    progress(f"stamped {len(stamped)} prompt(s) with {record['run_id']}: {', '.join(stamped) or '-'}")
    return stamped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the agents' eval sets (spec 6.6).")
    parser.add_argument("agents", nargs="*", help="agent ids (default: every registered agent)")
    parser.add_argument("--live", action="store_true", help="the configured model instead of the mock")
    parser.add_argument("--prompt", help="evaluate a candidate prompt version, e.g. patient_message_triage@2")
    parser.add_argument("--model", help="evaluate one agent on another registered model (config/models.yaml)")
    parser.add_argument("--stamp", action="store_true", help="write the run id into the approved prompts' headers")
    parser.add_argument("--check", action="store_true", help="exit 1 when an agent is below its threshold")
    parser.add_argument("--no-platform", action="store_true", help="skip the platform suites (prompt injection)")
    args = parser.parse_args(argv)
    import app.core.config  # noqa: F401  (loads apps/api/.env)

    record = run(args.agents or None, live=args.live, prompt=args.prompt, model=args.model,
                 platform=False if args.no_platform else None)
    if args.stamp:
        stamp(record)
    below = [a for a, e in record["agents"].items() if e.get("status") in ("below", "error")]
    print(f"{record['run_id']}: {len(record['agents'])} agents, {len(below)} below the bar or failed"
          + (f" ({', '.join(below)})" if below else ""))
    return 1 if args.check and below else 0


if __name__ == "__main__":
    from app.core.stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
