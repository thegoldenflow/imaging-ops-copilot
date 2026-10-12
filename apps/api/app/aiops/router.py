"""AI Ops API (spec 6.6): `/api/aiops/...` for the AI Ops page.

Every number the page shows comes from here: trace aggregates (app/aiops/observe.py), eval run files
(app/aiops/runs.py), the registries and the alert Tasks. Nothing is computed in the browser beyond formatting.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.aiops import alerts, observe, promptgate, release, runs, suites
from app.core.auth import require_roles
from app.core.models import Role, StaffUser
from app.core.store import get_store

router = APIRouter(prefix="/api/aiops", tags=["ai-ops"])
OPS = (Role.ADMIN, Role.OPERATIONS_MANAGER, Role.MEDICAL_DIRECTOR)
EVALS = (*OPS, Role.RADIOLOGIST)  # the radiologist kept the AI evaluations page; the Eval Center replaces it
SIX = ("schema_validity", "grounding_rate", "task_success", "human_override_rate", "latency_p50_ms",
       "latency_p95_ms", "cost_per_case_usd")


def _agents_table(view: dict) -> list[dict]:
    from app.agents import registry

    by_agent = {row["agent_id"]: row for row in view["by_agent"]}
    rows = []
    for agent_id, spec in sorted(registry.agents().items()):
        if not spec.is_agent:
            continue
        ops = by_agent.get(agent_id) or {}
        latest = runs.latest_for(agent_id, prompt_version=spec.prompt_version)
        entry = latest[1] if latest else {}
        rows.append({
            "agent_id": agent_id, "version": spec.version, "kind": spec.kind, "risk_tier": spec.risk_tier,
            "deployment_status": spec.deployment_status, "prompt_version": spec.prompt_version,
            "eval_status": spec.eval_status.status, "eval_source": spec.eval_status.source,
            "eval_note": spec.eval_status.note, "eval_run": spec.eval_status.run_id,
            "threshold": release.threshold(spec),
            "pass_rate": (entry.get("metrics") or {}).get("task_success"),
            "eval_cases": (entry.get("metrics") or {}).get("cases"),
            "override_rate": ops.get("human_override_rate"), "decided": ops.get("decided", 0),
            "latency_p95_ms": ops.get("latency_p95_ms"), "cost_usd": ops.get("cost_usd"),
            "cost_estimated": ops.get("cost_estimated", False), "requests": ops.get("requests", 0),
            "failed_runs": ops.get("failed_runs", 0)})
    return rows


@router.get("/overview")
def overview(days: int = Query(1, ge=1, le=90), user: StaffUser = Depends(require_roles(*OPS))):
    """The six cards (hospital-wide, the window), the agent table, the failures and the dimensions."""
    from app.llm.gateway import get_gateway

    view = observe.overview(get_store(), days=days)
    t = view["totals"]
    cards = [
        {"key": "requests", "label": "Agent runs", "value": t["requests"], "format": "int",
         "detail": {"model_calls": t["model_calls"], "tokens": t["tokens"]}},
        {"key": "cost_usd", "label": "Cost", "value": t["cost_usd"], "format": "usd",
         "estimated": t["cost_estimated"], "detail": {"cost_per_completed_workflow_usd":
                                                      t["cost_per_completed_workflow_usd"]}},
        {"key": "latency_p95_ms", "label": "Latency p95", "value": t["latency_p95_ms"], "format": "ms",
         "detail": {"latency_p50_ms": t["latency_p50_ms"]}},
        {"key": "failure_rate", "label": "Failure rate", "value": t["failure_rate"], "format": "pct",
         "detail": {"failed_runs": t["failed_runs"], "timeouts": t["timeouts"], "schema_failures": t["schema_failures"],
                    "tool_failures": t["tool_failures"]}},
        {"key": "human_override_rate", "label": "Human override", "value": t["human_override_rate"], "format": "pct",
         "detail": {"decided": t["decided"]}},
        {"key": "adoption_rate", "label": "Adoption", "value": t["adoption_rate"], "format": "pct",
         "detail": {"decided": t["decided"]}},
    ]
    return {**view, "cards": cards, "agents": _agents_table(view), "mode": get_gateway().mode,
            "alerts": {"open": [a for a in alerts.open_tasks() if a["status"] not in ("completed", "cancelled")]}}


@router.get("/traces/{run_id}")
def trace_detail(run_id: str, user: StaffUser = Depends(require_roles(*OPS))):
    """One run's trace (references and hashes only), with what the registries say about it."""
    from app.agents import models, registry
    from app.agents import prompts as prompt_files
    from app.agents import trace as traces

    t = traces.get(run_id)
    if t is None:
        raise HTTPException(status_code=404, detail=f"No trace {run_id}")
    spec = registry.agent(t.agent_id)
    header = None
    if t.prompt_version:
        try:
            header = prompt_files.header(t.prompt_version)
        except prompt_files.PromptFileError:
            header = None
    entry = models.by_model_id(t.model)
    return {"trace": t.model_dump(mode="json"), "record": observe.record(t, {}),
            "agent": {"agent_id": t.agent_id, "version": spec.version if spec else None,
                      "eval_status": spec.eval_status.status if spec else None} if spec else None,
            "prompt_header": header, "model_entry": entry.name if entry else None,
            "duration_ms": int((t.finished_at - t.started_at).total_seconds() * 1000) if t.finished_at else None}


# ---------- Eval Center ----------


@router.get("/runs")
def run_list(user: StaffUser = Depends(require_roles(*EVALS))):
    out = []
    for r in runs.summaries():
        statuses = [e.get("status") for e in r["agents"].values()]
        out.append({"run_id": r["run_id"], "started_at": r["started_at"], "mode": r["mode"], "variant": r["variant"],
                    "agents": len(r["agents"]), "met": statuses.count("met"), "below": statuses.count("below"),
                    "errors": statuses.count("error"), "no_eval_set": statuses.count("no_eval_set")})
    return {"runs": out, "folder": "evals/runs", "command": "cd apps/api && uv run python -m app.aiops.evalrun"}


@router.get("/runs/{run_id}")
def run_detail(run_id: str, user: StaffUser = Depends(require_roles(*EVALS))):
    try:
        record = runs.load(run_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    agents = {a: {k: v for k, v in e.items() if k not in ("case_results", "traceback")}
              for a, e in record["agents"].items()}
    return {**{k: v for k, v in record.items() if k != "agents"}, "agents": agents}


@router.get("/runs/{run_id}/agents/{agent_id}")
def run_agent(run_id: str, agent_id: str, user: StaffUser = Depends(require_roles(*EVALS))):
    try:
        record = runs.load(run_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    entry = record["agents"].get(agent_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"{agent_id} is not in {run_id}")
    cases = [{k: v for k, v in c.items() if k != "traces"} | {"has_trace": bool(c.get("traces"))}
             for c in entry.get("case_results") or []]
    return {"run_id": run_id, "started_at": record["started_at"], "mode": record["mode"],
            "variant": record.get("variant"), "agent_id": agent_id,
            **{k: v for k, v in entry.items() if k not in ("case_results", "traceback")}, "case_results": cases}


@router.get("/runs/{run_id}/agents/{agent_id}/cases/{case_id}")
def run_case(run_id: str, agent_id: str, case_id: str, user: StaffUser = Depends(require_roles(*EVALS))):
    """One eval case's result with the traces captured during the run (the run's writes were rolled back)."""
    try:
        record = runs.load(run_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    entry = record["agents"].get(agent_id) or {}
    case = next((c for c in entry.get("case_results") or [] if c["id"] == case_id), None)
    if case is None:
        raise HTTPException(status_code=404, detail=f"No case {case_id} for {agent_id} in {run_id}")
    return {"run_id": run_id, "agent_id": agent_id, "mode": record["mode"], "prompt_version": entry.get("prompt_version"),
            "model": entry.get("model"), **case}


@router.get("/evals")
def eval_center(user: StaffUser = Depends(require_roles(*EVALS))):
    """Per agent: its suite, case counts by source (the common view), the latest run and the effective status."""
    from app.agents import registry

    rows = []
    for agent_id, spec in sorted(registry.agents().items()):
        if not spec.is_agent:
            continue
        suite = suites.suite_for(spec)
        sources: dict[str, int] = {}
        if suite is not None:
            try:
                for c in suite.cases():
                    if (c.get("agent_id") or agent_id) == agent_id:
                        sources[c.get("source") or "authored"] = sources.get(c.get("source") or "authored", 0) + 1
            except Exception:  # noqa: BLE001  (a module report without rows)
                pass
        latest = runs.latest_for(agent_id, prompt_version=spec.prompt_version)
        run, entry = latest if latest else ({}, {})
        rows.append({"agent_id": agent_id, "version": spec.version, "prompt_version": spec.prompt_version,
                     "suite": suite.id if suite else None, "suite_title": suite.title if suite else None,
                     "suite_kind": suite.kind if suite else None, "source": suite.source if suite else None,
                     "cases_by_source": sources, "threshold": release.threshold(spec),
                     "eval_status": spec.eval_status.status, "eval_note": spec.eval_status.note,
                     "latest_run": run.get("run_id"), "latest_mode": entry.get("mode") or run.get("mode"),
                     "metrics": {k: (entry.get("metrics") or {}).get(k) for k in SIX} if entry else None,
                     "cost_estimated": (entry.get("metrics") or {}).get("cost_estimated"),
                     "status": entry.get("status"), "gates": entry.get("gates"), "extra": entry.get("extra")})
    platform = {name: suites.platform_report(name) for name in suites.PLATFORM_REPORTS}
    return {"agents": rows, "platform": platform,
            "command": "cd apps/api && uv run python -m app.aiops.evalrun [AGENT]"}


@router.get("/evals/{agent_id}/cases")
def eval_cases(agent_id: str, user: StaffUser = Depends(require_roles(*EVALS))):
    """The agent's cases in the common format (id, source, input_refs, expected)."""
    from app.agents import registry

    spec = registry.agent(agent_id)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"{agent_id} is not registered")
    suite = suites.suite_for(spec)
    if suite is None:
        return {"agent_id": agent_id, "suite": None, "cases": []}
    cases = [c for c in suite.cases() if (c.get("agent_id") or agent_id) == agent_id]
    return {"agent_id": agent_id, "suite": suite.id, "source": suite.source, "cases": cases}


@router.get("/compare")
def compare(agent: str, a: str, b: str, user: StaffUser = Depends(require_roles(*EVALS))):
    """Two runs of one agent side by side (two prompt versions or two models): the six metrics and every case
    whose result differs."""
    out = {}
    for key, run_id in (("a", a), ("b", b)):
        try:
            record = runs.load(run_id)
        except LookupError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        entry = record["agents"].get(agent)
        if entry is None or "metrics" not in entry:
            raise HTTPException(status_code=404, detail=f"{agent} has no results in {run_id}")
        out[key] = (record, entry)
    (ra, ea), (rb, eb) = out["a"], out["b"]

    def side(record, entry):
        return {"run_id": record["run_id"], "started_at": record["started_at"], "mode": entry.get("mode"),
                "variant": record.get("variant"), "prompt_version": entry.get("prompt_version"),
                "model": entry.get("model"), "model_key": entry.get("model_key"),
                "metrics": {k: entry["metrics"].get(k) for k in SIX}, "cost_estimated":
                entry["metrics"].get("cost_estimated"), "cases": entry["metrics"].get("cases"),
                "cases_passed": entry["metrics"].get("cases_passed"), "status": entry.get("status"),
                "threshold": entry.get("threshold"), "gates": entry.get("gates")}

    cases_a = {c["id"]: c for c in ea.get("case_results") or []}
    cases_b = {c["id"]: c for c in eb.get("case_results") or []}
    diffs = []
    for cid in sorted(set(cases_a) | set(cases_b)):
        ca, cb = cases_a.get(cid), cases_b.get(cid)
        changed = (ca or {}).get("passed") != (cb or {}).get("passed") or (ca or {}).get("got") != (cb or {}).get("got")
        if changed:
            diffs.append({"id": cid, "a": {k: (ca or {}).get(k) for k in ("passed", "got", "expected")} if ca else None,
                          "b": {k: (cb or {}).get(k) for k in ("passed", "got", "expected")} if cb else None,
                          "change": ("fixed" if cb and cb["passed"] and ca and not ca["passed"] else
                                     "regressed" if ca and ca["passed"] and cb and not cb["passed"] else "changed")})
    return {"agent_id": agent, "a": side(ra, ea), "b": side(rb, eb), "differences": diffs,
            "same_cases": len(set(cases_a) & set(cases_b)), "metrics_order": list(SIX)}


@router.get("/compare/options/{agent_id}")
def compare_options(agent_id: str, user: StaffUser = Depends(require_roles(*EVALS))):
    """The runs of one agent to choose from (newest first), each labelled with its prompt version and model."""
    return {"agent_id": agent_id, "runs": [
        {"run_id": run["run_id"], "started_at": run["started_at"], "mode": entry.get("mode"),
         "prompt_version": entry.get("prompt_version"), "model": entry.get("model"),
         "variant": entry.get("variant"), "task_success": (entry.get("metrics") or {}).get("task_success")}
        for run, entry in runs.runs_for(agent_id) if entry.get("metrics")]}


# ---------- registries ----------


@router.get("/registry")
def registry_view(user: StaffUser = Depends(require_roles(*EVALS))):
    """The model registry (ids resolved from the environment) and every prompt version with its header and gate."""
    from app.agents import models, registry
    from app.agents import prompts as prompt_files

    model_rows = []
    for name, entry in models.models().items():
        try:
            model_id = entry.model_id
        except models.ModelRegistryError:
            model_id = None
        users = sorted(a for a, s in registry.agents().items()
                       if s.is_agent and any((m := models.resolve(s, mode)) is not None and m.name == name
                                             for mode in ("anthropic", "gemini")))
        model_rows.append({**entry.model_dump(), "model_id": model_id, "agents": users})
    findings = {f.version: f for f in promptgate.check()}
    prompt_rows = []
    for agent_id, spec in sorted(registry.agents().items()):
        for version in prompt_files.versions(agent_id):
            try:
                header = prompt_files.header(version)
            except prompt_files.PromptFileError as e:
                header = {"error": str(e)}
            f = findings.get(version)
            prompt_rows.append({"version": version, "agent_id": agent_id, "active": version == spec.prompt_version,
                                "header": header, "gate_ok": f.ok if f else None, "gate_problem": f.problem if f else None,
                                "ungated": f.ungated if f else None,
                                "file": f"apps/api/prompts/{agent_id}/{version.partition('@')[2]}.md"})
    return {"models": model_rows, "prompts": prompt_rows,
            "gate": {"failing": sum(1 for p in prompt_rows if p["gate_ok"] is False),
                     "command": "cd apps/api && uv run python -m app.aiops.promptgate"}}


# ---------- alerts ----------


class ResolveBody(BaseModel):
    note: str | None = Field(default=None, max_length=500)


@router.get("/alerts")
def alert_list(user: StaffUser = Depends(require_roles(*OPS))):
    return {"config": [a.model_dump() for a in alerts.alerts()], "tasks": alerts.open_tasks(),
            "breaches": alerts.breaches(observe.overview(get_store(), days=1))}


@router.post("/alerts/check")
def alert_check(user: StaffUser = Depends(require_roles(*OPS))):
    return alerts.check(get_store())


@router.post("/alerts/{task_id}/resolve")
def alert_resolve(task_id: str, body: ResolveBody, user: StaffUser = Depends(require_roles(Role.ADMIN))):
    try:
        return alerts.resolve(task_id, user, body.note)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
