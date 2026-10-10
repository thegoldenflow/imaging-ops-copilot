"""The exception stream of the Control Tower (spec 7.1): what the rule engine found, and what people decided.

One row per exception (table `flow_exceptions`). The rule engine's key
(`unit_occupancy:MEDA`) identifies the condition; while it lasts there is one
exception for it, whatever is decided. Lifecycle:

    open --approve--> approved          (Tasks for the actions' owner roles)
    open --reject---> rejected          (with the reason)
    open --defer----> deferred --(reminder time on the hospital clock)--> open again
    open / deferred --(the condition clears)--> cleared

A decided exception stays decided while its condition lasts; when the condition
clears and later returns, that is a new exception. Facts are stored as the
engine saw them when the exception opened or changed severity (the narrative
quotes them); the board shows the live numbers next to it. `sync` writes only
when something changed, so an idle board does not touch the database.

Opening and clearing publish `exception.opened` / `exception.cleared` on the event bus, and the action drawer's
decisions `exception.decided` (service.py): the CapacityExceptionWorkflow (6.5) runs on those. Its last step
writes `outcome` (decision, executed Tasks, the occupancy re-checked an hour later).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.core.store import Store
from app.ehr.events import bus, platform_event
from app.modules.control_tower.rules import SEVERITY_RANK, Detected

TABLE = "flow_exceptions"
Status = Literal["open", "deferred", "approved", "rejected", "cleared"]
LIVE = ("open", "deferred")


class FlowException(BaseModel):
    id: str  # EXC-00001
    key: str
    rule: str
    severity: Literal["low", "med", "high"]
    status: Status = "open"
    title: str
    summary: str
    unit_id: str
    subject_ref: str
    facts: dict
    evidence_refs: list[str] = Field(default_factory=list)
    menu: list[dict] = Field(default_factory=list)
    detected_at: datetime  # hospital clock
    changed_at: datetime  # hospital clock: opened, or the severity changed
    cleared_at: datetime | None = None
    narration_status: Literal["pending", "done"] = "pending"
    narration: dict | None = None  # the validated narrator output and how it was made (encrypted at rest)
    review_task_id: str | None = None  # the ai-review Task (explainException) a decision completes
    decision: dict | None = None  # {decision, by, name, role, at, note, actions[], remind_at}
    remind_at: datetime | None = None  # deferred until (hospital clock)
    reminders: int = 0
    outcome: dict | None = None  # recorded by the CapacityExceptionWorkflow (6.5): decision, Tasks, verification


def table(store: Store):
    return store.module(TABLE, dict)


def get(store: Store, exception_id: str) -> FlowException | None:
    return table(store).get(exception_id)


def fresh(store: Store, exception_id: str) -> FlowException | None:
    """The exception as committed now (not this transaction's earlier copy): read it after taking its lock."""
    table(store).forget(exception_id)
    return get(store, exception_id)


def save(store: Store, exc: FlowException) -> None:
    table(store)[exc.id] = exc


def all_exceptions(store: Store) -> list[FlowException]:
    return list(table(store).values())


def current(store: Store) -> dict[str, FlowException]:
    """Exceptions whose condition has not cleared, by key."""
    return {e.key: e for e in all_exceptions(store) if e.cleared_at is None}


def publish(exc: FlowException, event_type: str, at: datetime, *, actor: str = "system:control-tower",
            attrs: dict | None = None, key: str | None = None) -> None:
    """Tell the event bus (references and routing attributes only)."""
    refs = {"location": f"Location/{exc.unit_id}"}
    if exc.subject_ref.startswith(("Encounter/", "Appointment/")):
        refs[exc.subject_ref.split("/", 1)[0].lower()] = exc.subject_ref
    bus.publish(platform_event(event_type, at=at, actor=actor, refs=refs,
                               key=key or f"{event_type}|{exc.id}",
                               attrs={"exception_id": exc.id, "rule": exc.rule, "severity": exc.severity,
                                      **(attrs or {})}))


def sync(store: Store, detected: list[Detected], now: datetime) -> dict[str, list[str]]:
    """Bring the stream in line with what the engine sees now; returns the ids opened, changed, cleared, reopened."""
    live = current(store)
    seen = set()
    changes: dict[str, list[str]] = {"opened": [], "changed": [], "cleared": [], "reopened": []}
    for d in detected:
        seen.add(d.key)
        exc = live.get(d.key)
        if exc is None:
            exc = FlowException(id=store.next_id("EXC"), key=d.key, rule=d.rule, severity=d.severity, title=d.title,
                                summary=d.summary, unit_id=d.unit_id, subject_ref=d.subject_ref, facts=d.facts,
                                evidence_refs=d.evidence_refs, menu=[m.model_dump() for m in d.menu],
                                detected_at=now, changed_at=now)
            save(store, exc)
            publish(exc, "exception.opened", now)
            changes["opened"].append(exc.id)
            continue
        dirty = False
        if exc.severity != d.severity and exc.status in LIVE:
            exc = exc.model_copy(update=dict(
                severity=d.severity, title=d.title, summary=d.summary, facts=d.facts, evidence_refs=d.evidence_refs,
                menu=[m.model_dump() for m in d.menu], changed_at=now,
                narration_status="pending" if exc.status in LIVE else exc.narration_status))
            changes["changed"].append(exc.id)
            dirty = True
        elif exc.title != d.title and exc.status in LIVE:  # the headline follows the counts; the narrative waits
            exc = exc.model_copy(update=dict(title=d.title))
            dirty = True
        if exc.status == "deferred" and exc.remind_at is not None and exc.remind_at <= now:
            exc = exc.model_copy(update=dict(status="open", reminders=exc.reminders + 1))
            changes["reopened"].append(exc.id)
            dirty = True
        if dirty:
            save(store, exc)
    for key, exc in live.items():
        if key in seen:
            continue
        exc = exc.model_copy(update=dict(cleared_at=now, status="cleared" if exc.status in LIVE else exc.status))
        save(store, exc)
        publish(exc, "exception.cleared", now, attrs={"decided": exc.status not in ("cleared",)})
        changes["cleared"].append(exc.id)
    return changes


def stream(store: Store, *, limit: int = 60) -> list[FlowException]:
    """For the board: live exceptions first (most severe, then newest), then recently decided or cleared ones."""
    items = all_exceptions(store)
    live = sorted((e for e in items if e.cleared_at is None and e.status in LIVE),
                  key=lambda e: (-SEVERITY_RANK[e.severity], e.status == "deferred", -e.changed_at.timestamp()))
    rest = sorted((e for e in items if not (e.cleared_at is None and e.status in LIVE)),
                  key=lambda e: -(e.cleared_at or e.changed_at).timestamp())
    return (live + rest)[:limit]


def pending_narration(store: Store) -> list[FlowException]:
    return sorted((e for e in all_exceptions(store) if e.narration_status == "pending" and e.status in LIVE
                   and e.cleared_at is None), key=lambda e: (-SEVERITY_RANK[e.severity], e.detected_at))
