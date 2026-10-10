"""EventBus <-> Temporal bridge (spec 6.5).

Domain events in, workflow starts and signals out:

| Domain event              | Condition                         | Command                                                |
| ------------------------- | --------------------------------- | ------------------------------------------------------ |
| patient.admitted          | inpatient stay (class IMP)        | start InpatientJourneyWorkflow `journey-<encounter>`   |
| patient.discharged        | inpatient stay                    | signal the journey: discharged                         |
| flag.raised               | code news2                        | signal the journey: news2                              |
| document.signed           | a workflow waits for the document | signal that workflow: signed (final or a co-signature) |
| task.decided              | a workflow waits for the Task     | signal that workflow: decided (approve / reject)       |
| exception.opened          |                                   | start CapacityExceptionWorkflow `capacity-<exception>` |
| exception.decided         |                                   | signal it: decided (approved / rejected / deferred)    |
| exception.cleared         | before anyone decided             | signal it: cleared                                     |
| agent.low_confidence      |                                   | start LowConfidenceReviewWorkflow `review-<run>`       |

The subscriber does not call Temporal: in the drain's transaction it inserts the commands into the outbox
`workflow_commands`, keyed by event and target, so an event delivered twice adds nothing (insert-first
idempotency). The dispatcher (`dispatch_step`, a background step of the API) sends them in order: a start with the
workflow id as business key (already started = done), a signal carrying the event id (the workflow ignores a
repeat). When Temporal is unreachable the commands wait and go out once it is back; nothing is lost and nothing
blocks the event bus. Workflows publish their completed steps back onto the bus (progress.py).

Without TEMPORAL_ADDRESS the bridge does not subscribe and the dispatcher does nothing.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.config import settings
from app.core.store import Store, get_store
from app.ehr.events import DomainEvent, bus
from app.workflows import client
from app.workflows import model as M
from app.workflows import progress
from app.workflows.tables import workflow_commands, workflow_waits

log = logging.getLogger("app.workflows")
CONSUMER = "workflows.bridge"
EVENT_TYPES = ("patient.admitted", "patient.discharged", "flag.raised", "document.signed", "task.decided",
               "exception.opened", "exception.decided", "exception.cleared", "agent.low_confidence")
BATCH = 50
BACKOFF_S = (1, 2, 5, 10, 30, 60)


def timers() -> dict:
    return asdict(M.Timers(signoff_timeout_s=settings.workflow_signoff_timeout_s,
                           escalation_timeout_s=settings.workflow_escalation_timeout_s,
                           followup_delay_s=settings.workflow_followup_delay_s,
                           verify_after_s=settings.workflow_verify_after_s))


# ---------- the subscriber ----------


def install() -> bool:
    """Subscribe to the bus when Temporal is configured (the API calls it at startup)."""
    if client.link() is None or any(s.consumer == CONSUMER for s in bus.subscriptions()):
        return False
    bus.subscribe(EVENT_TYPES, handle, consumer=CONSUMER)
    return True


def uninstall() -> None:
    bus.unsubscribe(CONSUMER)


def handle(event: DomainEvent) -> None:
    for command in commands_for(event):
        enqueue(command)


def _ref_id(event: DomainEvent, key: str) -> str | None:
    value = event.refs.get(key)
    return value.split("/", 1)[1] if isinstance(value, str) and "/" in value else None


def _signal(event: DomainEvent, workflow_id: str, payload: dict) -> dict:
    return {"id": f"{event.event_id}:signal:{workflow_id}", "kind": "signal", "workflow_id": workflow_id,
            "workflow_type": M.type_of(workflow_id), "signal": M.SIGNAL_EVENT,
            "payload": {"event_id": event.event_id, "at": event.occurred_at.isoformat(), **payload}}


def _start(event: DomainEvent, workflow_type: str, workflow_id: str, arg: dict) -> dict:
    return {"id": f"{event.event_id}:start:{workflow_id}", "kind": "start", "workflow_id": workflow_id,
            "workflow_type": workflow_type, "signal": None,
            "payload": {**arg, "timers": timers(), "started_by": event.event_id}}


def commands_for(event: DomainEvent) -> list[dict]:
    """The starts and signals one domain event means (see the table above)."""
    a, t = event.attrs, event.type
    if t == "patient.admitted" and a.get("encounter_class") == "IMP" and event.encounter:
        return [_start(event, M.JOURNEY, M.journey_id(event.encounter),
                       {"encounter_id": event.encounter, "patient_id": event.patient})]
    if t == "patient.discharged" and a.get("encounter_class") == "IMP" and event.encounter:
        return [_signal(event, M.journey_id(event.encounter),
                        {"kind": "discharged", "ref": f"Encounter/{event.encounter}"})]
    if t == "flag.raised" and a.get("code") == "news2" and event.encounter:
        return [_signal(event, M.journey_id(event.encounter), {"kind": "news2", "ref": event.refs.get("flag")})]
    if t in ("document.signed", "task.decided"):
        ref = event.refs.get("document" if t == "document.signed" else "task")
        wait = waiting_for(ref) if isinstance(ref, str) else None
        if wait is None:
            return []
        extra = {"kind": "signed", "final": bool(a.get("final"))} if t == "document.signed" else \
            {"kind": "decided", "decision": a.get("decision")}
        return [_signal(event, wait["workflow_id"], {**extra, "ref": ref, "role": a.get("role"), "step": wait["step"]})]
    exc = a.get("exception_id")
    if t == "exception.opened" and exc:
        return [_start(event, M.CAPACITY, M.capacity_id(exc), {"exception_id": exc})]
    if t == "exception.decided" and exc:
        return [_signal(event, M.capacity_id(exc), {"kind": "decided", "ref": f"FlowException/{exc}",
                                                    "decision": a.get("decision"), "role": a.get("role")})]
    if t == "exception.cleared" and exc and not a.get("decided"):
        return [_signal(event, M.capacity_id(exc), {"kind": "cleared", "ref": f"FlowException/{exc}"})]
    if t == "agent.low_confidence" and a.get("run_id") and a.get("review_id"):
        return [_start(event, M.REVIEW, M.review_id(str(a["run_id"])),
                       {"review_id": a["review_id"], "run_id": a["run_id"], "agent_id": a.get("agent_id")})]
    return []


def enqueue(command: dict, store: Store | None = None) -> bool:
    """Insert-first: True when the command is new."""
    now = datetime.now()
    stmt = pg_insert(workflow_commands).values(
        id=command["id"], kind=command["kind"], workflow_type=command.get("workflow_type"),
        workflow_id=command["workflow_id"], signal=command.get("signal"), payload=command["payload"],
        status="pending", attempts=0, next_attempt_at=now, created_at=now)
    result = (store or get_store()).conn().execute(
        stmt.on_conflict_do_nothing(index_elements=[workflow_commands.c.id]).returning(workflow_commands.c.seq))
    return result.first() is not None


def control(workflow_id: str, step: str, action: str, by: str, role: str, event_id: str) -> None:
    """A retry or skip from the workflow view (the router checks and audits it first)."""
    enqueue({"id": f"{event_id}:control:{workflow_id}", "kind": "signal", "workflow_id": workflow_id,
             "workflow_type": M.type_of(workflow_id), "signal": M.SIGNAL_CONTROL,
             "payload": {"event_id": event_id, "action": action, "step": step, "by": by, "role": role,
                         "at": datetime.now().isoformat(timespec="seconds")}})


def start_journey(encounter_id: str, patient_id: str | None, by: str, event_id: str, timer_values: dict | None = None) -> str:
    """Start a journey for an inpatient admitted before the bridge was on (from the workflow view)."""
    workflow_id = M.journey_id(encounter_id)
    enqueue({"id": f"{event_id}:start:{workflow_id}", "kind": "start", "workflow_id": workflow_id,
             "workflow_type": M.JOURNEY, "signal": None,
             "payload": {"encounter_id": encounter_id, "patient_id": patient_id,
                         "timers": {**timers(), **(timer_values or {})}, "started_by": by}})
    return workflow_id


# ---------- what workflows wait for ----------


def register_wait(ref: str, workflow_id: str, step: str, store: Store | None = None) -> None:
    stmt = pg_insert(workflow_waits).values(ref=ref, workflow_id=workflow_id, step=step, created_at=datetime.now())
    (store or get_store()).conn().execute(stmt.on_conflict_do_nothing(index_elements=[workflow_waits.c.ref]))


def waiting_for(ref: str, store: Store | None = None) -> dict | None:
    row = (store or get_store()).conn().execute(select(workflow_waits).where(workflow_waits.c.ref == ref)).first()
    return dict(row._mapping) if row else None


# ---------- the dispatcher ----------


def dispatch_step(store: Store) -> int:
    """Background step: send due commands to Temporal in order. Returns how many were settled."""
    link = client.link()
    if link is None:
        return 0
    t = workflow_commands
    now = datetime.now()
    rows = store.conn().execute(
        select(t).where(t.c.status == "pending", t.c.next_attempt_at <= now).order_by(t.c.seq).limit(BATCH)
        .with_for_update(skip_locked=True)).all()
    settled, blocked = 0, set()
    for row in rows:
        if row.workflow_id in blocked:  # an earlier command for it is still waiting: keep the order
            continue
        try:
            status = _send(link, store, row)
        except client.TemporalUnavailable as e:
            _retry(store, row, str(e))
            break  # Temporal is down: the rest wait too
        except Exception as e:  # this command only (bad payload, unexpected answer): retry later, keep order
            log.warning("workflow command %s failed: %s", row.id, str(e)[:200])
            _retry(store, row, f"{type(e).__name__}: {e}")
            blocked.add(row.workflow_id)
            continue
        if status == "wait":
            _retry(store, row, "the workflow is not running yet")
            blocked.add(row.workflow_id)
            continue
        store.conn().execute(update(t).where(t.c.seq == row.seq).values(
            status=status, attempts=row.attempts + 1, sent_at=datetime.now(), last_error=None))
        settled += 1
    return settled


def _send(link: client.TemporalLink, store: Store, row) -> str:
    if row.kind == "start":
        return "sent" if link.start(row.workflow_type, row.workflow_id, row.payload) == "started" else "duplicate"
    if link.signal(row.workflow_id, row.signal, row.payload) == "sent":
        return "sent"
    # No such workflow. Its start, if any, came earlier in the outbox: still pending, it waits; otherwise the
    # workflow was never started (admitted before the bridge was on) or has finished, and the signal is dropped.
    t = workflow_commands
    starting = store.conn().execute(select(t.c.seq).where(t.c.workflow_id == row.workflow_id, t.c.kind == "start",
                                                          t.c.status == "pending", t.c.seq < row.seq)).first()
    return "wait" if starting is not None else "dropped"


def _retry(store: Store, row, error: str) -> None:
    wait = BACKOFF_S[min(row.attempts, len(BACKOFF_S) - 1)]
    store.conn().execute(update(workflow_commands).where(workflow_commands.c.seq == row.seq).values(
        attempts=row.attempts + 1, next_attempt_at=datetime.now() + timedelta(seconds=wait), last_error=error[:500]))


def backlog(store: Store) -> dict:
    """Commands by status (the workflow view's status line)."""
    from sqlalchemy import func

    t = workflow_commands
    return dict(store.conn().execute(select(t.c.status, func.count()).group_by(t.c.status)).all())


# ---------- for the modules ----------


def running_capacity_workflow(store: Store, exception_id: str) -> str | None:
    """The exception's workflow id when Temporal is on and the workflow has started (it then executes the
    approved actions); None otherwise (the Control Tower executes them in the request)."""
    if client.link() is None:
        return None
    run = progress.get(M.capacity_id(exception_id), store)
    return run.id if run is not None and run.status == "running" else None


def on_reset() -> None:
    """A demo reset regenerated the data the running workflows refer to: end them (best effort)."""
    link = client.link()
    if link is not None:
        link.terminate_running("demo data reset")
