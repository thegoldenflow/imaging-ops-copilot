"""Append-only audit log protected by a hash chain.

Each event stores the hash of the previous event, so editing or deleting any
earlier entry breaks every hash after it and `verify()` reports where.
"""

import hashlib
import json
from datetime import datetime

from app.core.models import AuditEvent

GENESIS_HASH = "0" * 64


def _digest(prev_hash: str, payload: dict) -> str:
    body = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256((prev_hash + body).encode()).hexdigest()


class AuditLog:
    def __init__(self) -> None:
        self._events: list[AuditEvent] = []

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
        prev_hash = self._events[-1].hash if self._events else GENESIS_HASH
        payload = {
            "seq": len(self._events) + 1,
            "ts": (ts or datetime.now()).isoformat(timespec="seconds"),
            "user_id": user_id,
            "user_name": user_name,
            "role": role,
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "outcome": outcome,
            "source_ip": source_ip,
            "reason": reason,
        }
        event = AuditEvent(**payload, prev_hash=prev_hash, hash=_digest(prev_hash, payload))
        self._events.append(event)
        return event

    def events(self) -> list[AuditEvent]:
        return list(self._events)

    def verify(self) -> tuple[bool, int | None]:
        """Return (intact, seq of the first broken event)."""
        prev_hash = GENESIS_HASH
        for event in self._events:
            payload = event.model_dump(exclude={"prev_hash", "hash"})
            payload["ts"] = event.ts.isoformat(timespec="seconds")
            if event.prev_hash != prev_hash or event.hash != _digest(prev_hash, payload):
                return False, event.seq
            prev_hash = event.hash
        return True, None
