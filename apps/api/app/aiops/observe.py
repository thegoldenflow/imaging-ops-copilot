"""Observability and cost (spec 6.6), aggregated from the agent traces, Tasks and workflow runs. No other log.

Per trace (one agent run, or one model call of an embedded agent): requests, model calls, tokens, cost (the
gateway's; list-price estimate for mock calls, app/aiops/cost.py), latency (the run's duration), timeouts and
unavailable models, schema failures (output that failed validation twice), retries (validated on the second try,
tool calls that needed more than one attempt), tool failures, tool calls the gateway refused, retrieval failures
(read tools that failed or were refused), fallbacks (a tool's fallback, or a run whose model was unavailable so
the module fell back to its rules or a template).

Business metrics from the same traces, the Tasks the agents wrote and the workflow runs: adoption (decided outputs
a person accepted, approved, signed or confirmed), human override (corrected or rejected), escalation (runs that
ended needing a person), Task completion (agent-written Tasks completed), cost per completed workflow (the runs
of the workflow's encounter, exception or reviewed run).

Dimensions: agent, model, unit (the encounter's unit; `imaging` for the imaging systems' calls, `hospital` for
hospital-wide runs) and day (wall clock).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from app.aiops import cost as costs
from app.aiops.evalrun import OVERRIDE_DECISIONS

ADOPT_DECISIONS = frozenset({"accept", "approve", "approved", "sign", "confirmed"})
FAILED_OUTCOMES = frozenset({"failed", "refused", "unavailable", "needs_human"})


def window(days: int, now: datetime | None = None) -> datetime:
    """Start of the window: today (days=1) or the last N days, from midnight."""
    now = now or datetime.now()
    return (now - timedelta(days=max(1, days) - 1)).replace(hour=0, minute=0, second=0, microsecond=0)


def traces_since(store, start: datetime) -> list:
    from app.agents import trace as traces

    table = traces.table(store)
    if hasattr(table, "since"):
        return table.since("started_at", start)
    return [t for t in table.values() if t.started_at >= start]


def _units(store, encounter_ids: set[str]) -> dict[str, str]:
    """Encounter -> unit, from the FHIR store's plain search columns (no resource bodies read)."""
    if not encounter_ids:
        return {}
    try:
        from sqlalchemy import select

        from app.ehr.fhirstore import fhir_resources as t

        rows = store.conn().execute(select(t.c.id, t.c.unit).where(t.c.resource_type == "Encounter",
                                                                   t.c.id.in_(sorted(encounter_ids)))).all()
        return {rid: unit for rid, unit in rows if unit}
    except Exception:  # detached store (scripts without a database)
        return {}


def _task_statuses(store, task_ids: set[str]) -> dict[str, str]:
    if not task_ids:
        return {}
    try:
        from sqlalchemy import select

        from app.ehr.fhirstore import fhir_resources as t

        out = {}
        ids = sorted(task_ids)
        for i in range(0, len(ids), 500):
            rows = store.conn().execute(select(t.c.id, t.c.status).where(t.c.resource_type == "Task",
                                                                         t.c.id.in_(ids[i:i + 500]))).all()
            out.update({rid: status for rid, status in rows})
        return out
    except Exception:
        return {}


def record(t, units: dict[str, str]) -> dict:
    """One trace's operational facts (references only)."""
    from app.agents import registry

    spec = registry.agent(t.agent_id)
    cost_usd = 0.0
    estimated = False
    timeouts = schema_failures = retries = 0
    latency_llm = 0
    for c in t.llm_calls:
        usd, est = costs.call_cost(t.mode, c.model, c.cost_usd, c.tokens_in, c.tokens_out)
        cost_usd += usd
        estimated = estimated or est
        timeouts += c.status == "unavailable"
        schema_failures += c.status == "needs_human"
        retries += c.status == "retried_ok"
        latency_llm += c.latency_ms
    tool_failed = sum(1 for c in t.tool_calls if c.status == "failed")
    tool_refused = sum(1 for c in t.tool_calls if c.status == "refused")
    retrieval = sum(1 for c in t.tool_calls if c.side_effect_level == "read" and c.status in ("failed", "refused"))
    fallbacks = sum(1 for c in t.tool_calls if c.status == "fallback") + (t.outcome == "unavailable")
    retries += sum(1 for c in t.tool_calls if c.attempts > 1)
    duration = int((t.finished_at - t.started_at).total_seconds() * 1000) if t.finished_at else None
    unit = units.get(t.encounter_id or "") or ("imaging" if spec is not None and spec.kind == "embedded_agent"
                                               and t.agent_id not in ("deid_check",) else "hospital")
    decision = (t.human_action or {}).get("decision")
    failures = []
    if t.outcome in FAILED_OUTCOMES:
        failures.append(f"outcome {t.outcome}")
    if timeouts:
        failures.append("model unavailable or timed out")
    if schema_failures:
        failures.append("schema validation failed")
    if tool_failed:
        failures.append(f"{tool_failed} tool call(s) failed")
    if tool_refused:
        failures.append(f"{tool_refused} tool call(s) refused by the gateway")
    return {
        "run_id": t.run_id, "agent_id": t.agent_id, "model": t.model or "-", "mode": t.mode, "unit": unit,
        "day": t.started_at.date().isoformat(), "started_at": t.started_at.isoformat(timespec="seconds"),
        "outcome": t.outcome, "encounter_id": t.encounter_id, "prompt_version": t.prompt_version,
        "llm_calls": len(t.llm_calls), "tokens": t.tokens_in + t.tokens_out, "cost_usd": cost_usd,
        "estimated": estimated, "latency_ms": duration if duration is not None else latency_llm,
        "timeouts": timeouts, "schema_failures": schema_failures, "retries": retries, "tool_failures": tool_failed,
        "tool_refusals": tool_refused, "retrieval_failures": retrieval, "fallbacks": fallbacks,
        "decision": decision, "adopted": decision in ADOPT_DECISIONS if decision else None,
        "overridden": decision in OVERRIDE_DECISIONS if decision else None,
        "escalated": t.outcome == "needs_human", "failed": bool(failures), "failures": failures,
        "task_refs": [r.split("/", 1)[1] for r in t.output_refs if r.startswith("Task/")],
        "context_id": t.context_id,
    }


def _pct(values: list[int], q: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))]


def _ratio(a: int, b: int) -> float | None:
    return round(a / b, 4) if b else None


def aggregate(records: list[dict], task_status: dict[str, str]) -> dict:
    """The operational and business metrics over a set of trace records."""
    decided = [r for r in records if r["decision"]]
    tasks = [tid for r in records for tid in r["task_refs"]]
    known = [tid for tid in tasks if tid in task_status]
    latencies = [r["latency_ms"] for r in records if r["latency_ms"] is not None]
    runs = len(records)
    return {
        "requests": runs,
        "model_calls": sum(r["llm_calls"] for r in records),
        "tokens": sum(r["tokens"] for r in records),
        "cost_usd": round(sum(r["cost_usd"] for r in records), 6),
        "cost_estimated": any(r["estimated"] for r in records),
        "latency_p50_ms": _pct(latencies, 0.5),
        "latency_p95_ms": _pct(latencies, 0.95),
        "timeouts": sum(r["timeouts"] for r in records),
        "schema_failures": sum(r["schema_failures"] for r in records),
        "tool_failures": sum(r["tool_failures"] for r in records),
        "tool_refusals": sum(r["tool_refusals"] for r in records),
        "retrieval_failures": sum(r["retrieval_failures"] for r in records),
        "fallbacks": sum(r["fallbacks"] for r in records),
        "retries": sum(r["retries"] for r in records),
        "failed_runs": sum(1 for r in records if r["failed"]),
        "failure_rate": _ratio(sum(1 for r in records if r["failed"]), runs),
        "schema_validity": _ratio(sum(r["llm_calls"] - r["schema_failures"] - r["timeouts"] for r in records),
                                  sum(r["llm_calls"] - r["timeouts"] for r in records)),
        "decided": len(decided),
        "adoption_rate": _ratio(sum(1 for r in decided if r["adopted"]), len(decided)),
        "human_override_rate": _ratio(sum(1 for r in decided if r["overridden"]), len(decided)),
        "escalation_rate": _ratio(sum(1 for r in records if r["escalated"]), runs),
        "tasks_written": len(tasks),
        "task_completion_rate": _ratio(sum(1 for tid in known if task_status[tid] == "completed"), len(known)),
    }


def _workflow_cost(store, all_records: list[dict], start: datetime) -> dict:
    """Cost per completed workflow: the runs that belong to each workflow finished in the window."""
    from app.workflows import progress

    try:
        done = [w for w in progress.all_runs(store) if w.status == "completed" and w.finished_at
                and w.finished_at >= start]
    except Exception:
        done = []
    if not done:
        return {"completed_workflows": 0, "cost_per_completed_workflow_usd": None, "workflow_cost_usd": 0.0}
    by_encounter, by_context, by_run = defaultdict(float), defaultdict(float), {}
    for r in all_records:
        if r["encounter_id"]:
            by_encounter[r["encounter_id"]] += r["cost_usd"]
        if r["context_id"]:
            by_context[r["context_id"]] += r["cost_usd"]
        by_run[r["run_id"]] = r["cost_usd"]
    total = 0.0
    for w in done:
        if w.workflow_type == "journey" and w.encounter_id:
            total += by_encounter.get(w.encounter_id, 0.0)
        elif w.workflow_type == "capacity" and w.exception_id:
            total += by_context.get(w.exception_id, 0.0)
        elif w.run_id:
            total += by_run.get(w.run_id, 0.0)
    return {"completed_workflows": len(done), "workflow_cost_usd": round(total, 6),
            "cost_per_completed_workflow_usd": round(total / len(done), 6)}


def overview(store, days: int = 1, now: datetime | None = None) -> dict:
    """Everything the AI Ops page shows for a window: totals, the dimensions, the failures."""
    start = window(days, now)
    traces = traces_since(store, start)
    units = _units(store, {t.encounter_id for t in traces if t.encounter_id})
    records = [record(t, units) for t in traces]
    task_status = _task_statuses(store, {tid for r in records for tid in r["task_refs"]})
    totals = aggregate(records, task_status)
    totals.update(_workflow_cost(store, records, start))

    def by(key: str) -> list[dict]:
        groups: dict[str, list] = defaultdict(list)
        for r in records:
            groups[r[key]].append(r)
        return [{key: k, **aggregate(v, task_status)} for k, v in sorted(groups.items())]

    failures = sorted((r for r in records if r["failed"]), key=lambda r: r["started_at"], reverse=True)
    return {"window": {"days": days, "from": start.isoformat(timespec="seconds"),
                       "to": (now or datetime.now()).isoformat(timespec="seconds")},
            "totals": totals, "by_agent": by("agent_id"), "by_model": by("model"), "by_unit": by("unit"),
            "by_day": by("day"),
            "failures": [{k: r[k] for k in ("run_id", "agent_id", "started_at", "outcome", "failures", "model",
                                           "unit", "encounter_id", "prompt_version")} for r in failures[:200]],
            "failure_count": len(failures)}
