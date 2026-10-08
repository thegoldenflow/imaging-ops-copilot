"""The hospital's simulated clock (docs/data-model.md): separate from the wall clock,
it starts at 07:00 on the hospital day and only moves when the day simulator runs."""

from __future__ import annotations

from datetime import datetime

from app.core.store import Store


def hospital_now(store: Store) -> datetime:
    clock = store.modules.get("hospital_clock") or {}
    return datetime.fromisoformat(clock["now"]) if clock.get("now") else datetime.now()
