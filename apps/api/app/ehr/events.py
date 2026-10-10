"""The hospital's domain event bus (spec 6.2).

Hospital systems are event driven; their heartbeat is the ADT feed. Here the day
simulator plays the EHR and sends HL7 v2 messages, the HL7 adapter
(app/ehr/hl7.py) maps each message to a domain event, and modules subscribe to
domain events only, never to HL7 message types:

    from app.ehr.events import bus

    def on_admit(event: DomainEvent) -> None:
        encounter = FhirGateway(...).read("Encounter", event.encounter)  # content comes from the gateway

    bus.subscribe("patient.admitted", on_admit, consumer="control_tower.census")

An event carries references only (`refs`: Patient/..., Encounter/..., Location/...)
and a few routing attributes (encounter class, disposition, status), never
clinical content or identifiers. Every event has `event_id`, `correlation_id`,
`actor` and a timestamp (`occurred_at`, hospital time).

Delivery (`InProcessEventBus`):

- Publishing appends the event to `domain_events` in the publisher's unit of
  work (a transactional outbox): the simulator's FHIR writes and its events
  commit together or not at all.
- `drain()` delivers committed events to each subscriber in sequence order. The
  handler's own writes, the delivery records and the consumer's cursor commit
  together: a batch of events in one transaction, and if any handler in it
  raises, the batch is rolled back and redone one event per transaction. A
  handler may therefore run again for an event whose effects were rolled back,
  so side effects outside the database must be idempotent (e.g. a Temporal
  workflow started under a business key). The API's background loop drains
  every second; tests call it directly.
- Consumers deduplicate by `event_id` (`event_deliveries`): an event delivered
  twice, e.g. an HL7 message the interface engine resent, is handled once.
- A failing handler is retried on the next drains; after `MAX_ATTEMPTS` the
  event is parked for that consumer (a dead letter, shown on the integrations
  page) and the consumer moves on. Until then the consumer waits, so it never
  sees events out of order.

Swapping in Redis Pub/Sub (`RedisPubSubBus`) keeps the publish/subscribe
interface, the outbox and the consumer-side deduplication; only the transport
between them changes.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel, Field
from sqlalchemy import BigInteger, Column, DateTime, Identity, Index, Table, Text, func, insert, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.db.schema import metadata

log = logging.getLogger("app.events")

# Domain event types, with the HL7 v2 message each one comes from (mapping in app/ehr/hl7.py).
EVENT_TYPES: dict[str, str] = {
    "patient.admitted": "ADT^A01: arrival at the ED, inpatient admission or day-surgery check-in",
    "patient.transferred": "ADT^A02: a bed move",
    "patient.discharged": "ADT^A03: the end of an ED visit, stay or day-surgery visit",
    "encounter.updated": "ADT^A08: an encounter changed (triaged, seen, disposition decided, ALC)",
    "bed.status_changed": "ADT^A20: housekeeping finished, the bed is free",
    "order.placed": "ORM^O01: a lab, imaging or consult order",
    "result.available": "ORU^R01: a lab or imaging result, or a vital-sign panel",
    "appointment.scheduled": "SIU^S12: an operating-room case was booked",
    "appointment.updated": "SIU^S14: an operating-room case started or finished",
    "appointment.cancelled": "SIU^S15: an operating-room case was cancelled",
    "consent.revoked": "platform: a patient withdrew a consent (no HL7 message)",
    "consent.granted": "platform: a patient gave a consent that was missing or withdrawn (no HL7 message)",
    # WP4c (6.5): what people and the platform decide, so durable workflows can wait for it, and the steps
    # those workflows complete, so the boards follow them.
    "task.decided": "platform: a person approved, accepted or rejected a Task (approval, AI review, sign-off)",
    "document.signed": "platform: a person signed a draft document (final, or waiting for a co-signature)",
    "flag.raised": "platform: a safety flag was raised on an encounter (e.g. a NEWS2 score)",
    "exception.opened": "platform: the Control Tower's rule engine found a capacity exception",
    "exception.decided": "platform: an exception's recommendation was approved, rejected or deferred",
    "exception.cleared": "platform: an exception's condition cleared",
    "agent.low_confidence": "platform: an agent run's confidence fell below the agent's registry threshold",
    "workflow.step_completed": "platform: a durable workflow completed or skipped a step",
}

MAX_ATTEMPTS = 3
BATCH = 200
_EVENT_NS = uuid.UUID("5b1f4c2e-8f0a-4d51-9a57-1c0de0e7e3a1")

# ---------- tables (truncated by a demo reset: app/core/db/schema.py FIXED_DATA_TABLES) ----------

domain_events = Table(
    "domain_events", metadata,
    Column("seq", BigInteger, Identity(), primary_key=True),
    Column("event_id", Text, nullable=False),
    Column("type", Text, nullable=False),
    Column("occurred_at", DateTime, nullable=False),  # hospital clock
    Column("recorded_at", DateTime, nullable=False),  # wall clock
    Column("correlation_id", Text, nullable=False),
    Column("actor", Text, nullable=False),
    Column("source", Text, nullable=False),  # HL7 message type ("ADT^A01") or "platform"
    Column("message_id", Text),  # HL7 message control id
    Column("patient", Text),  # Patient id (a reference, not an identifier)
    Column("encounter", Text),
    Column("refs", JSONB, nullable=False),
    Column("attrs", JSONB, nullable=False),
)
Index("ix_domain_events_type_seq", domain_events.c.type, domain_events.c.seq)
Index("ix_domain_events_event_id", domain_events.c.event_id)
Index("ix_domain_events_encounter", domain_events.c.encounter)

event_consumers = Table(
    "event_consumers", metadata,
    Column("consumer", Text, primary_key=True),
    Column("cursor", BigInteger, nullable=False),  # seq of the last event this consumer is done with
    Column("duplicates", BigInteger, nullable=False),  # events skipped because their event_id was handled
    Column("updated_at", DateTime, nullable=False),
)

event_deliveries = Table(
    "event_deliveries", metadata,
    Column("consumer", Text, primary_key=True),
    Column("event_id", Text, primary_key=True),
    Column("seq", BigInteger, nullable=False),
    Column("status", Text, nullable=False),  # done, failed (will retry), parked (gave up)
    Column("attempts", BigInteger, nullable=False),
    Column("error", Text),
    Column("updated_at", DateTime, nullable=False),
)
Index("ix_event_deliveries_status", event_deliveries.c.status)


# ---------- the event ----------


class DomainEvent(BaseModel):
    event_id: str
    type: str
    occurred_at: datetime  # hospital time of the change
    correlation_id: str
    actor: str  # "system:<sending application>" or "user:<staff id>" (who triggered it)
    source: str  # the HL7 message type it was mapped from, or "platform"
    message_id: str | None = None
    patient: str | None = None  # Patient id
    encounter: str | None = None  # Encounter id
    refs: dict[str, str | list[str]] = Field(default_factory=dict)  # FHIR references only
    attrs: dict[str, str | int | bool | None] = Field(default_factory=dict)  # routing attributes, no content
    recorded_at: datetime | None = None
    seq: int | None = None

    @property
    def timestamp(self) -> datetime:
        return self.occurred_at


def event_id_for(key: str) -> str:
    """A stable event id: the same source message (e.g. a resent HL7 message) gets the same id."""
    return str(uuid.uuid5(_EVENT_NS, key))


def platform_event(event_type: str, *, at: datetime, actor: str, refs: dict, attrs: dict | None = None,
                   correlation_id: str | None = None, key: str | None = None) -> DomainEvent:
    """An event raised by the platform itself rather than mapped from an HL7 message (e.g. consent.revoked)."""
    if event_type not in EVENT_TYPES:
        raise ValueError(f"Unknown event type {event_type!r}")
    patient = refs.get("patient")
    encounter = refs.get("encounter")
    return DomainEvent(
        event_id=event_id_for(key) if key else str(uuid.uuid4()), type=event_type, occurred_at=at,
        correlation_id=correlation_id or uuid.uuid4().hex, actor=actor, source="platform",
        patient=patient.split("/", 1)[1] if isinstance(patient, str) else None,
        encounter=encounter.split("/", 1)[1] if isinstance(encounter, str) else None,
        refs=refs, attrs=attrs or {})


def _row(event: DomainEvent) -> dict:
    return {"event_id": event.event_id, "type": event.type, "occurred_at": event.occurred_at,
            "recorded_at": event.recorded_at or datetime.now(), "correlation_id": event.correlation_id,
            "actor": event.actor, "source": event.source, "message_id": event.message_id,
            "patient": event.patient, "encounter": event.encounter, "refs": event.refs, "attrs": event.attrs}


def _event(row) -> DomainEvent:
    return DomainEvent(seq=row.seq, event_id=row.event_id, type=row.type, occurred_at=row.occurred_at,
                       recorded_at=row.recorded_at, correlation_id=row.correlation_id, actor=row.actor,
                       source=row.source, message_id=row.message_id, patient=row.patient,
                       encounter=row.encounter, refs=row.refs, attrs=row.attrs)


# ---------- the bus ----------

Handler = Callable[[DomainEvent], None]


@dataclass
class Subscription:
    consumer: str  # stable name: the cursor and the deduplication records are kept under it
    types: frozenset[str]  # event types, or {"*"} for all
    handler: Handler

    def wants(self, event_type: str) -> bool:
        return "*" in self.types or event_type in self.types


@dataclass
class DrainResult:
    delivered: int = 0
    duplicates: int = 0
    failed: int = 0  # handler raised; retried on a later drain
    parked: int = 0  # gave up after MAX_ATTEMPTS
    by_consumer: dict[str, int] = field(default_factory=dict)


class EventBus(Protocol):
    def publish(self, event: DomainEvent) -> DomainEvent: ...

    def publish_many(self, events: Iterable[DomainEvent]) -> list[DomainEvent]: ...

    def subscribe(self, event_types: str | Iterable[str], handler: Handler, *, consumer: str | None = None) -> Subscription: ...

    def unsubscribe(self, consumer: str) -> None: ...

    def drain(self, *, limit: int | None = None) -> DrainResult: ...


def _types(event_types: str | Iterable[str]) -> frozenset[str]:
    types = frozenset([event_types] if isinstance(event_types, str) else event_types)
    unknown = types - set(EVENT_TYPES) - {"*"}
    if unknown:
        raise ValueError(f"Unknown event type(s) {sorted(unknown)}; see app.ehr.events.EVENT_TYPES")
    return types


def _lock_key(consumer: str) -> int:
    return int(uuid.uuid5(_EVENT_NS, "consumer:" + consumer).int % (2 ** 62))


def _reset_running(store) -> bool:
    """A demo reset holds REWRITE_LOCK exclusively while it truncates every table. A delivery transaction takes it
    shared (or stops for this drain), so the drain and the reset's TRUNCATE never deadlock (WP4c: the workflow
    bridge is the first subscriber in the API process)."""
    from sqlalchemy import text

    from app.core.store import REWRITE_LOCK

    if store.detached:
        return False
    return not store.conn().execute(text("SELECT pg_try_advisory_xact_lock_shared(:key)"),
                                    {"key": REWRITE_LOCK}).scalar()


class InProcessEventBus:
    """Outbox in PostgreSQL, handlers in this process (see the module docstring)."""

    def __init__(self) -> None:
        self._subs: dict[str, Subscription] = {}

    # ----- subscriptions -----

    def subscribe(self, event_types: str | Iterable[str], handler: Handler, *, consumer: str | None = None) -> Subscription:
        name = consumer or f"{handler.__module__}.{handler.__qualname__}"
        if name in self._subs:
            raise ValueError(f"Consumer {name!r} is already subscribed")
        sub = Subscription(name, _types(event_types), handler)
        self._subs[name] = sub
        return sub

    def unsubscribe(self, consumer: str) -> None:
        self._subs.pop(consumer, None)

    def subscriptions(self) -> list[Subscription]:
        return list(self._subs.values())

    # ----- publishing -----

    def publish(self, event: DomainEvent) -> DomainEvent:
        return self.publish_many([event])[0]

    def publish_many(self, events: Iterable[DomainEvent]) -> list[DomainEvent]:
        """Append events to the outbox in the current unit of work; they are delivered after it commits."""
        from app.core.store import get_store

        events = list(events)
        if not events:
            return []
        for event in events:
            if event.type not in EVENT_TYPES:
                raise ValueError(f"Unknown event type {event.type!r}")
        rows = [_row(e) for e in events]
        seqs = get_store().conn().execute(insert(domain_events).returning(domain_events.c.seq, sort_by_parameter_order=True), rows).scalars().all()
        return [e.model_copy(update={"seq": seq, "recorded_at": row["recorded_at"]})
                for e, seq, row in zip(events, seqs, rows, strict=True)]

    # ----- delivery -----

    def drain(self, *, limit: int | None = None) -> DrainResult:
        """Deliver committed events to every subscriber (at most `limit` per consumer)."""
        result = DrainResult()
        for sub in list(self._subs.values()):
            n = self._drain_consumer(sub, limit, result)
            if n:
                result.by_consumer[sub.consumer] = n
        return result

    def _pending(self, store, sub: Subscription, cursor: int, size: int) -> list[DomainEvent]:
        t = domain_events
        stmt = select(t).where(t.c.seq > cursor)
        if "*" not in sub.types:
            stmt = stmt.where(t.c.type.in_(sorted(sub.types)))
        return [_event(r) for r in store.conn().execute(stmt.order_by(t.c.seq).limit(size))]

    @staticmethod
    def _cursor(store, consumer: str) -> tuple[int, int]:
        row = store.conn().execute(select(event_consumers.c.cursor, event_consumers.c.duplicates)
                                   .where(event_consumers.c.consumer == consumer)).first()
        return (row.cursor, row.duplicates) if row else (0, 0)

    @staticmethod
    def _advance(store, consumer: str, seq: int, *, duplicate: bool = False) -> None:
        stmt = pg_insert(event_consumers).values(consumer=consumer, cursor=seq, duplicates=int(duplicate),
                                                 updated_at=datetime.now())
        store.conn().execute(stmt.on_conflict_do_update(
            index_elements=[event_consumers.c.consumer],
            set_={"cursor": func.greatest(event_consumers.c.cursor, stmt.excluded.cursor),
                  "duplicates": event_consumers.c.duplicates + stmt.excluded.duplicates,
                  "updated_at": stmt.excluded.updated_at}))

    @staticmethod
    def _record(store, consumer: str, event: DomainEvent, status: str, attempts: int, error: str | None) -> None:
        stmt = pg_insert(event_deliveries).values(consumer=consumer, event_id=event.event_id, seq=event.seq,
                                                  status=status, attempts=attempts, error=error,
                                                  updated_at=datetime.now())
        store.conn().execute(stmt.on_conflict_do_update(
            index_elements=[event_deliveries.c.consumer, event_deliveries.c.event_id],
            set_={"seq": stmt.excluded.seq, "status": stmt.excluded.status, "attempts": stmt.excluded.attempts,
                  "error": stmt.excluded.error, "updated_at": stmt.excluded.updated_at}))

    @staticmethod
    def _delivery(store, consumer: str, event_id: str):
        t = event_deliveries
        return store.conn().execute(select(t.c.status, t.c.attempts)
                                    .where(t.c.consumer == consumer, t.c.event_id == event_id)).first()

    def _drain_consumer(self, sub: Subscription, limit: int | None, result: DrainResult) -> int:
        from app.core.store import unit_of_work

        done = 0
        while limit is None or done < limit:
            with unit_of_work() as store:
                if _reset_running(store):
                    break
                cursor, _ = self._cursor(store, sub.consumer)
                size = BATCH if limit is None else min(BATCH, limit - done)
                batch = self._pending(store, sub, cursor, size)
            if not batch:
                break
            outcomes = self._deliver_batch(sub, batch)
            if outcomes is None:  # a handler failed (or a retry is pending): one event per transaction
                outcomes = []
                for event in batch:
                    outcome = self._deliver(sub, event)
                    if outcome == "stop":
                        break
                    outcomes.append(outcome)
            done += len(outcomes)
            result.delivered += outcomes.count("done")
            result.duplicates += outcomes.count("duplicate")
            result.parked += outcomes.count("parked")
            if len(outcomes) < len(batch):
                break
        return done

    def _deliver_batch(self, sub: Subscription, batch: list[DomainEvent]) -> list[str] | None:
        """The whole batch in one transaction (the fast path after a fast-forward). Returns None when it
        has to be redone event by event: a handler raised (the transaction is rolled back, nothing of the
        batch stays) or an event of the batch has a failed attempt on record."""
        from app.core.store import unit_of_work

        t = event_deliveries
        try:
            with unit_of_work() as store:
                if _reset_running(store) or not store.try_lock(_lock_key(sub.consumer)):
                    return []  # a demo reset runs, or another process is delivering to this consumer
                cursor, _ = self._cursor(store, sub.consumer)
                if batch[0].seq <= cursor:
                    return []  # handled by the other process; re-read on the next drain
                previous = dict(store.conn().execute(
                    select(t.c.event_id, t.c.status).where(t.c.consumer == sub.consumer,
                                                           t.c.event_id.in_({e.event_id for e in batch}))).all())
                if "failed" in previous.values():
                    return None
                outcomes, rows, now = [], [], datetime.now()
                for event in batch:
                    if event.event_id in previous:
                        outcomes.append("duplicate")
                        continue
                    sub.handler(event)
                    previous[event.event_id] = "done"
                    rows.append({"consumer": sub.consumer, "event_id": event.event_id, "seq": event.seq,
                                 "status": "done", "attempts": 1, "error": None, "updated_at": now})
                    outcomes.append("done")
                if rows:
                    store.conn().execute(pg_insert(t), rows)
                self._advance(store, sub.consumer, batch[-1].seq)
                if outcomes.count("duplicate"):
                    store.conn().execute(update(event_consumers).where(event_consumers.c.consumer == sub.consumer)
                                         .values(duplicates=event_consumers.c.duplicates + outcomes.count("duplicate")))
                return outcomes
        except Exception:  # rolled back; the event-by-event pass finds the failing event and records it
            log.warning("event batch failed for consumer=%s; delivering one by one", sub.consumer)
            return None

    def _deliver(self, sub: Subscription, event: DomainEvent) -> str:
        """One event to one consumer, in its own transaction. Returns done, duplicate, parked or stop."""
        from app.core.store import unit_of_work

        try:
            with unit_of_work() as store:
                if _reset_running(store) or not store.try_lock(_lock_key(sub.consumer)):
                    return "stop"  # a demo reset runs, or another process is delivering to this consumer
                cursor, _ = self._cursor(store, sub.consumer)
                if event.seq <= cursor:
                    return "stop"  # already handled by the other process; re-read on the next drain
                previous = self._delivery(store, sub.consumer, event.event_id)
                if previous is not None and previous.status in ("done", "parked"):
                    self._advance(store, sub.consumer, event.seq, duplicate=True)
                    return "duplicate"
                sub.handler(event)
                self._record(store, sub.consumer, event, "done", (previous.attempts if previous else 0) + 1, None)
                self._advance(store, sub.consumer, event.seq)
            return "done"
        except Exception as e:  # the handler's transaction is rolled back; record the attempt separately
            log.exception("event handler failed: consumer=%s type=%s event_id=%s", sub.consumer, event.type, event.event_id)
            error = f"{type(e).__name__}: {e}"[:500]
            with unit_of_work() as store:
                previous = self._delivery(store, sub.consumer, event.event_id)
                attempts = (previous.attempts if previous else 0) + 1
                if attempts < MAX_ATTEMPTS:
                    self._record(store, sub.consumer, event, "failed", attempts, error)
                    return "stop"
                self._record(store, sub.consumer, event, "parked", attempts, error)
                self._advance(store, sub.consumer, event.seq)
                self._dead_letter(store, sub.consumer, event, error, attempts)
            return "parked"

    @staticmethod
    def _dead_letter(store, consumer: str, event: DomainEvent, error: str, attempts: int) -> None:
        from app.integrations.contract import DeadLetter, dead_letters

        letter = DeadLetter(id=store.next_id("DLQ"), ts=datetime.now(), adapter=f"event-bus/{consumer}",
                            operation=event.type, correlation_id=event.correlation_id,
                            payload={"event_id": event.event_id, "seq": event.seq, "refs": event.refs},
                            error=error, attempts=attempts)
        dead_letters(store)[letter.id] = letter

    # ----- reading the log -----

    @staticmethod
    def events(store, *, after: int | None = None, before: int | None = None, types: Iterable[str] | None = None,
               encounter: str | None = None, limit: int = 100, newest_first: bool = True) -> list[DomainEvent]:
        t = domain_events
        stmt = select(t)
        if after is not None:
            stmt = stmt.where(t.c.seq > after)
        if before is not None:
            stmt = stmt.where(t.c.seq < before)
        if types:
            stmt = stmt.where(t.c.type.in_(list(types)))
        if encounter:
            stmt = stmt.where(t.c.encounter == encounter)
        stmt = stmt.order_by(t.c.seq.desc() if newest_first else t.c.seq).limit(limit)
        return [_event(r) for r in store.conn().execute(stmt)]

    def consumer_status(self, store) -> list[dict[str, Any]]:
        """Each subscriber's cursor, backlog and failed or parked deliveries."""
        out = []
        last = store.conn().execute(select(func.max(domain_events.c.seq))).scalar() or 0
        for sub in self._subs.values():
            cursor, duplicates = self._cursor(store, sub.consumer)
            t = domain_events
            backlog = select(func.count()).select_from(t).where(t.c.seq > cursor)
            if "*" not in sub.types:
                backlog = backlog.where(t.c.type.in_(sorted(sub.types)))
            d = event_deliveries
            counts = dict(store.conn().execute(select(d.c.status, func.count()).where(d.c.consumer == sub.consumer)
                                               .group_by(d.c.status)).all())
            out.append({"consumer": sub.consumer, "types": sorted(sub.types), "cursor": cursor, "last_seq": last,
                        "backlog": store.conn().execute(backlog).scalar_one(), "duplicates_skipped": duplicates,
                        "delivered": counts.get("done", 0), "failing": counts.get("failed", 0),
                        "parked": counts.get("parked", 0)})
        return out

    @staticmethod
    def counts(store) -> dict[str, int]:
        t = domain_events
        return dict(store.conn().execute(select(t.c.type, func.count()).group_by(t.c.type)).all())


class RedisPubSubBus:
    """Interface only (the demo runs in one process). A deployment with several API or worker
    processes would keep `publish_many` writing the outbox in the publisher's transaction and add
    a relay that PUBLISHes committed rows to one channel per event type; each consumer process
    SUBSCRIBEs to its types and runs the same per-consumer deduplication (`event_deliveries`), so a
    message Redis delivers twice is still handled once. Redis Pub/Sub does not keep messages for a
    consumer that is down, so a restarted consumer catches up from the outbox by its cursor."""

    def __init__(self, url: str) -> None:
        self.url = url

    def _unavailable(self, *args, **kwargs):
        raise NotImplementedError("RedisPubSubBus is an interface in the demo; use InProcessEventBus")

    publish = publish_many = subscribe = unsubscribe = drain = _unavailable


bus = InProcessEventBus()


def get_bus() -> InProcessEventBus:
    return bus
