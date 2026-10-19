"""The scheduled daily demo reset (DEMO_DAILY_RESET_HOUR, app/modules/admin/service.py) runs once a day, and a reset
someone else did that day counts: the generated data carries no marker of its own, so before casebook 12.15 a
storyline reset after the hour was followed within seconds by the scheduler's reset, which wiped the story's preset
(live server, 10-19, twice in a row). Generating the real data set takes 15-20 s; an empty detached store stands in
for it, inside the test's rolled-back transaction."""

import dataclasses
from datetime import datetime

import pytest

from app.core import store as store_module
from app.core.store import Store, reset_store
from app.modules.admin import service as admin


@pytest.fixture
def daily_at_8(monkeypatch):
    monkeypatch.setattr(admin, "settings", dataclasses.replace(admin.settings, demo_daily_reset_hour=8))
    monkeypatch.setattr(store_module, "build_store", lambda seed=None: Store())
    calls: list[str] = []
    monkeypatch.setattr(admin, "reset_store", lambda: calls.append("reset") or reset_store())
    return calls


def test_a_reset_after_the_hour_counts_as_todays_daily_reset(fresh_state, daily_at_8):
    reset_store()  # the storyline's reset, or the header's Reset demo
    assert fresh_state.modules.get("daily_reset") == datetime.now().date().isoformat()
    assert admin.maybe_daily_reset(fresh_state, now=datetime.now().replace(hour=9)) is False
    assert daily_at_8 == []  # the scheduler left the data (and what was planted on it) alone


def test_the_scheduler_still_resets_once_on_a_day_without_one(fresh_state, daily_at_8):
    fresh_state.modules.pop("daily_reset")
    nine = datetime.now().replace(hour=9)
    assert admin.maybe_daily_reset(fresh_state, now=nine) is True
    assert admin.maybe_daily_reset(fresh_state, now=nine) is False
    assert daily_at_8 == ["reset"]
    assert admin.maybe_daily_reset(fresh_state, now=datetime.now().replace(hour=7)) is False  # before the hour
