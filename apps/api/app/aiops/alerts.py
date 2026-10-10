"""AI Ops alerts (spec 6.6): `config/alerts.yaml` thresholds over today's trace aggregates open admin Tasks.

`check(store)` compares each alert's metric (app/aiops/observe.py, today) per agent, model, unit or for the whole
hospital with its threshold. A breach opens one admin Task (code `ai-ops-alert`, performer admin, no patient) per
alert, scope and day, through the Tool Gateway as the registry module `ai_ops` (tool createAlertTask: audited,
traced, idempotent). The API's background loop checks every minute; the AI Ops page can check on request. An admin
resolves the Task on the AI Ops page (updateTask completed, with the admin's name, plus a `write` audit event).
"""

from __future__ import annotations

import os
from datetime import datetime
from functools import cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from app.aiops import observe
from app.core.context import acting_as_system

CONFIG = Path(__file__).resolve().parents[2] / "config"
ALERTS_FILE = Path(os.environ.get("ALERTS_CONFIG", CONFIG / "alerts.yaml"))
MODULE = "ai_ops"
TASK_CODE_VALUE = "ai-ops-alert"
_LOCK = 0x0A10_A1E7  # one check at a time (pg advisory lock)


class Alert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,40}$")
    title: str
    metric: str
    per: Literal["agent", "model", "unit", "hospital"]
    op: Literal[">", ">="] = ">"
    threshold: float
    min_requests: int = 0
    priority: Literal["routine", "urgent", "asap", "stat"] = "urgent"


@cache
def _load(path: Path = ALERTS_FILE) -> tuple[Alert, ...]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return tuple(Alert.model_validate(a) for a in data.get("alerts") or [])


def alerts() -> tuple[Alert, ...]:
    return _load()


def reload() -> None:
    _load.cache_clear()


def _rows(view: dict, per: str) -> list[tuple[str, dict]]:
    if per == "hospital":
        return [("hospital", view["totals"])]
    key = {"agent": "agent_id", "model": "model", "unit": "unit"}[per]
    return [(row[key], row) for row in view[f"by_{per}"]]


def breaches(view: dict) -> list[dict]:
    """Every alert whose metric passes its threshold in this overview (today)."""
    out = []
    for alert in alerts():
        for scope, row in _rows(view, alert.per):
            value = row.get(alert.metric)
            if value is None or row.get("requests", 0) < alert.min_requests:
                continue
            hit = value > alert.threshold if alert.op == ">" else value >= alert.threshold
            if hit:
                out.append({"alert_id": alert.id, "title": alert.title, "scope": scope, "per": alert.per,
                            "metric": alert.metric, "value": value, "threshold": alert.threshold, "op": alert.op,
                            "priority": alert.priority, "estimated": bool(row.get("cost_estimated"))
                            and alert.metric == "cost_usd"})
    return out


def _fhir():
    from app.ehr.gateway import Actor, FhirGateway

    return FhirGateway(Actor.system("ai-ops"), MODULE)


def open_tasks(store=None) -> list[dict]:
    """The alert Tasks (newest first), as plain dicts with their inputs."""
    found = _fhir().search("Task", code=TASK_CODE_VALUE)
    out = []
    for task in found:
        inputs = {(i.get("type") or {}).get("text"): i.get("valueString") for i in task.get("input") or []}
        out.append({"id": task["id"], "status": task.get("status"), "priority": task.get("priority"),
                    "description": task.get("description"), "authored": task.get("authoredOn"),
                    "alert_id": inputs.get("alert"), "scope": inputs.get("scope"), "day": inputs.get("day"),
                    "metric": inputs.get("metric"), "value": inputs.get("value"),
                    "threshold": inputs.get("threshold"), "notes": [n.get("text") for n in task.get("note") or []]})
    return sorted(out, key=lambda t: t["authored"] or "", reverse=True)


def _describe(b: dict) -> str:
    value = f"{b['value']:.4f}" if isinstance(b["value"], float) else str(b["value"])
    unit = " USD" if b["metric"] == "cost_usd" else ""
    est = " (list-price estimate: mock calls bill nothing)" if b["estimated"] else ""
    return (f"AI Ops alert: {b['title']}. {b['per'].capitalize()} {b['scope']}: {b['metric']} {value}{unit}{est} "
            f"{b['op']} threshold {b['threshold']}{unit} (config/alerts.yaml). Review the agent's traces on the AI "
            f"Ops page.")[:1000]


def check(store, now: datetime | None = None) -> dict:
    """Compare today's aggregates with the thresholds; open a Task for each new breach."""
    from app.agents.runtime import runtime

    now = now or datetime.now()
    if not store.try_lock(_LOCK):
        return {"checked": False, "reason": "another check is running", "breaches": [], "opened": []}
    view = observe.overview(store, days=1, now=now)
    found = breaches(view)
    day = now.date().isoformat()
    existing = {(t["alert_id"], t["scope"], t["day"]) for t in open_tasks(store)}
    opened = []
    for b in found:
        if (b["alert_id"], str(b["scope"]), day) in existing:
            b["task"] = "open already"
            continue
        args = {"alert_id": b["alert_id"], "scope": str(b["scope"])[:80], "day": day,
                "description": _describe(b), "priority": b["priority"]}
        with acting_as_system(), runtime.start(MODULE, user=None,
                                               context_id=f"alert:{b['alert_id']}:{b['scope']}:{day}") as run:
            result = run.tool("createAlertTask", args)
            run.finish(outcome="completed" if result.ok else result.status)
        if result.ok:
            b["task"] = result.output["task_id"]
            opened.append(result.output["task_id"])
            # the measured value goes on the Task too (not in the idempotency key: it changes all day)
            _note_values(result.output["task_id"], b)
    return {"checked": True, "at": now.isoformat(timespec="seconds"), "breaches": found, "opened": opened}


def _note_values(task_id: str, b: dict) -> None:
    fhir = _fhir()
    found = fhir.read("Task", task_id)
    if found is None:
        return
    task = found.to_fhir()
    task["input"] = [*(task.get("input") or []),
                     *({"type": {"text": k}, "valueString": str(v)} for k, v in
                       (("metric", b["metric"]), ("value", b["value"]), ("threshold", b["threshold"])))]
    fhir.update(task)


def resolve(task_id: str, user, note: str | None = None) -> dict:
    """An admin closes an alert Task (through the Tool Gateway as the module, with the admin named)."""
    from app.agents.runtime import runtime
    from app.core.store import get_store

    task = next((t for t in open_tasks() if t["id"] == task_id), None)
    if task is None:
        raise LookupError(f"Task/{task_id} is not an AI Ops alert")
    text = f"Resolved by {user.name} ({user.role})" + (f": {note}" if note else "")
    with acting_as_system(), runtime.start(MODULE, user=None, context_id=f"alert-resolve:{task_id}") as run:
        result = run.tool("updateTask", {"task_id": task_id, "status": "completed", "note": text[:1000]})
        run.finish(outcome="completed" if result.ok else result.status)
    if not result.ok:
        raise PermissionError(f"updateTask {result.status}: {result.reason}")
    get_store().audit.record(user_id=user.id, user_name=user.name, role=str(user.role), action="resolve_alert",
                             event_type="write", resource_type="Task", resource_id=task_id, outcome="allowed",
                             source_ip=None, reason=(note or "")[:200], module=MODULE)
    return {"task_id": task_id, "status": "completed"}


# ---------- the tool handler (createAlertTask) ----------


def create_alert_task(ctx, alert_id: str, scope: str, day: str, description: str, priority: str = "urgent") -> dict:
    from app.ehr.codes import TASK_CODE, concept
    from app.fhir.dt import fhir_datetime

    now = fhir_datetime(datetime.now())
    task = {"resourceType": "Task", "status": "requested", "intent": "order", "priority": priority,
            "code": concept(TASK_CODE, TASK_CODE_VALUE, "AI Ops alert"), "description": description,
            "authoredOn": now, "lastModified": now,
            "performerType": [{"text": "admin"}],
            "input": [{"type": {"text": "alert"}, "valueString": alert_id},
                      {"type": {"text": "scope"}, "valueString": scope},
                      {"type": {"text": "day"}, "valueString": day}]}
    stored = ctx.fhir.create(ctx.stamp(task)).to_fhir()
    return {"task_id": stored["id"], "status": stored["status"], "_refs": [f"Task/{stored['id']}"]}
