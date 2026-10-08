"""Append-only audit log protected by a hash chain.

Each event stores the hash of the previous event, so editing or deleting any
earlier entry breaks every hash after it and `verify()` reports where. In the
database the table also refuses UPDATE and DELETE (trigger in the initial
migration).

Events are written in their own short transaction, under an advisory lock that
keeps the chain in order, so an access attempt stays on record even when the
request that made it fails and rolls back.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import insert, select, text

from app.core.db import schema
from app.core.models import AuditEvent

if TYPE_CHECKING:
    from app.core.store import Store

GENESIS_HASH = "0" * 64
_CHAIN_LOCK = 0x10C_A0D1  # pg_advisory_xact_lock key serializing appends


def _digest(prev_hash: str, payload: dict) -> str:
    body = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256((prev_hash + body).encode()).hexdigest()


def _event(prev_seq: int, prev_hash: str, fields: dict, ts: datetime | None) -> AuditEvent:
    payload = {"seq": prev_seq + 1, "ts": (ts or datetime.now()).isoformat(timespec="seconds"), **fields}
    return AuditEvent(**payload, prev_hash=prev_hash, hash=_digest(prev_hash, payload))


def verify_chain(events: list[AuditEvent]) -> tuple[bool, int | None]:
    """Return (intact, seq of the first broken event)."""
    prev_hash = GENESIS_HASH
    for event in events:
        payload = event.model_dump(exclude={"prev_hash", "hash"})
        payload["ts"] = event.ts.isoformat(timespec="seconds")
        if event.prev_hash != prev_hash or event.hash != _digest(prev_hash, payload):
            return False, event.seq
        prev_hash = event.hash
    return True, None


class AuditLog:
    def __init__(self, store: Store) -> None:
        self._store = store
        self._pending: list[AuditEvent] = []  # detached store only

    def record(
        self,
        *,
        user_id: str,
        user_name: str,
        role: str,
        action: str,
        resource_type: str,
        resource_id: str | None,
        outcome: str = "allowed",
        source_ip: str | None = None,
        reason: str = "",
        ts: datetime | None = None,  # only for importing historical events in order (demo seed)
    ) -> AuditEvent:
        fields = {"user_id": user_id, "user_name": user_name, "role": role, "action": action,
                  "resource_type": resource_type, "resource_id": resource_id, "outcome": outcome,
                  "source_ip": source_ip, "reason": reason}
        if self._store.detached:
            last = self._pending[-1] if self._pending else None
            event = _event(last.seq if last else 0, last.hash if last else GENESIS_HASH, fields, ts)
            self._pending.append(event)
            return event
        table = schema.audit_events
        # After a reset in this transaction the table is locked until commit, so write inline.
        writer = contextlib.nullcontext(self._store.conn()) if self._store.rewritten else self._store.autonomous()
        with writer as conn:
            conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _CHAIN_LOCK})
            last = conn.execute(select(table.c.seq, table.c.hash).order_by(table.c.seq.desc()).limit(1)).first()
            event = _event(last.seq if last else 0, last.hash if last else GENESIS_HASH, fields, ts)
            conn.execute(insert(table).values(**event.model_dump()))
        return event

    def events(self) -> list[AuditEvent]:
        if self._store.detached:
            return list(self._pending)
        table = schema.audit_events
        rows = self._store.conn().execute(select(table).order_by(table.c.seq)).mappings()
        return [AuditEvent(**row) for row in rows]

    def verify(self) -> tuple[bool, int | None]:
        return verify_chain(self.events())

    def encoded_rows(self) -> list[dict]:
        return [e.model_dump() for e in self._pending]
