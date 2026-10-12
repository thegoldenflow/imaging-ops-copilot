"""Append-only audit log protected by a hash chain.

Each event stores the hash of the previous event, so editing or deleting any
earlier entry breaks every hash after it and `verify()` reports where. In the
database the table also refuses UPDATE and DELETE (trigger in the initial
migration).

Events are written in their own short transaction, under an advisory lock that
keeps the chain in order, so an access attempt stays on record even when the
request that made it fails and rolls back.

Hospital platform (spec 6.3): every event also has an `event_type` (one of
EVENT_TYPES, derived from the action unless given) and, where they apply, the
patient's MRN hash, the encounter, the purpose module and the prompt version
(ai_call). These fields were added after the first events were written; a field
that is null is left out of the digest, so the chain over old and new events
verifies unchanged.
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

# Spec 6.3 event types; `login` is kept as its own type (sign-ins were audited before 6.3).
EVENT_TYPES = ("read", "write", "ai_call", "sign", "break_glass", "export", "consent_change", "simulator_event",
               "login", "tool_call", "approve")  # tool_call, approve: the agent runtime (6.4)
# Fields added in WP4: left out of the digest when null (see the module docstring).
OPTIONAL_FIELDS = ("event_type", "patient_mrn_hash", "encounter_id", "module", "prompt_version")
_TYPE_OF_ACTION = {"read": "read", "knowledge_query": "read", "export": "export", "disclose": "export",
                   "sign": "sign", "login": "login", "ai_call": "ai_call", "break_glass": "break_glass",
                   "consent_change": "consent_change", "simulator_event": "simulator_event",
                   "tool_call": "tool_call", "privileged_call": "tool_call"}
# `approve` events are written with an explicit event_type by the agent runtime's approvals
# (app/agents/approvals.py); other modules' "approve" actions (e.g. a protocol) stay writes.


def event_type_for(action: str) -> str:
    """The 6.3 event type of an action: reads, exports, signatures, ...; every other change is a write."""
    return _TYPE_OF_ACTION.get(action, "write")


def _digest(prev_hash: str, payload: dict) -> str:
    payload = {k: v for k, v in payload.items() if not (k in OPTIONAL_FIELDS and v is None)}
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


def mrn_hash(mrn: str | None) -> str | None:
    """The keyed blind index of an MRN (as the FHIR store's search column): searchable, not reversible."""
    if not mrn:
        return None
    from app.core.db.crypto import cipher

    return cipher().blind_index(mrn)


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
        event_type: str | None = None,  # default: derived from the action
        patient_mrn_hash: str | None = None,
        encounter_id: str | None = None,
        module: str | None = None,
        prompt_version: str | None = None,
    ) -> AuditEvent:
        event_type = event_type or event_type_for(action)
        if event_type not in EVENT_TYPES:
            raise ValueError(f"Unknown audit event type {event_type!r}")
        fields = {"user_id": user_id, "user_name": user_name, "role": str(role), "action": action,
                  "resource_type": resource_type, "resource_id": resource_id, "outcome": outcome,
                  "source_ip": source_ip, "reason": reason, "event_type": event_type,
                  "patient_mrn_hash": patient_mrn_hash, "encounter_id": encounter_id, "module": module,
                  "prompt_version": prompt_version}
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

    def query(self, *, user_id: str | None = None, event_type: str | None = None, module: str | None = None,
              outcome: str | None = None, patient_mrn_hash: str | None = None, resource_type: str | None = None,
              resource_id: str | None = None, action: str | None = None, since: datetime | None = None, until: datetime | None = None, limit: int | None = None,
              newest_first: bool = False) -> list[AuditEvent]:
        """Events matching every given filter (the admin audit search, the break-glass review)."""
        filters = {"user_id": user_id, "event_type": event_type, "module": module, "outcome": outcome,
                   "patient_mrn_hash": patient_mrn_hash, "resource_type": resource_type, "resource_id": resource_id,
                   "action": action}
        filters = {k: v for k, v in filters.items() if v is not None}
        if self._store.detached:
            out = [e for e in self._pending if all(getattr(e, k) == v for k, v in filters.items())
                   and (since is None or e.ts >= since) and (until is None or e.ts <= until)]
            out = out[::-1] if newest_first else out
            return out[:limit] if limit else out
        table = schema.audit_events
        stmt = select(table).where(*(table.c[k] == v for k, v in filters.items()))
        if since is not None:
            stmt = stmt.where(table.c.ts >= since)
        if until is not None:
            stmt = stmt.where(table.c.ts <= until)
        stmt = stmt.order_by(table.c.seq.desc() if newest_first else table.c.seq)
        if limit:
            stmt = stmt.limit(limit)
        return [AuditEvent(**row) for row in self._store.conn().execute(stmt).mappings()]

    def verify(self) -> tuple[bool, int | None]:
        return verify_chain(self.events())

    def encoded_rows(self) -> list[dict]:
        return [e.model_dump() for e in self._pending]
