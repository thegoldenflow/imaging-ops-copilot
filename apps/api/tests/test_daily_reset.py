"""The scheduled daily demo reset (DEMO_DAILY_RESET_HOUR, app/modules/admin/service.py) runs once a day and leaves
the hospital storyline at step 1 with the default patient, Margaret Ellis: it is the storyline's reset (one
regeneration, then her preset), so the live app's story needs no visitor's reset. A reset someone else did that day
counts: the generated data carries no marker of its own, so before casebook 12.15 a storyline reset after the hour
was followed within seconds by the scheduler's reset, which wiped the story's preset (live server, 10-19, twice in a
row). Generating the real data set takes 15-20 s; here the generation is counted and stood in for (the seeded data
stays, and the preset is planted on it as on a new data set), inside the test's rolled-back transaction. A failing
daily reset is tried once a day per process, not every 2 s: each try regenerates and leaks memory (casebook 7.8)."""

import dataclasses
from datetime import datetime, timedelta

import pytest

from app.core import store as store_module
from app.core.db.repo import clear_process_cache
from app.core.store import Store, reset_store
from app.ehr.seed import journey
from app.modules.admin import service as admin


@pytest.fixture
def daily_at_8(monkeypatch):
    monkeypatch.setattr(admin, "settings", dataclasses.replace(admin.settings, demo_daily_reset_hour=8))
    generated: list[str] = []
    monkeypatch.setattr(store_module, "build_store", lambda seed=None: generated.append("generated") or Store())
    monkeypatch.setattr(store_module, "persist", lambda src, into, rows=None: None)  # the seeded data stays
    monkeypatch.setattr(admin, "_attempted", None)  # a process that has not tried today
    return generated


def _step(store, now: datetime) -> bool | None:
    """The daily reset as the intake loop runs it (app/main.py `_step`, every 2 s): in its own transaction, which a
    reset that raises rolls back with all it wrote, the day's marker too. None: it raised."""
    store.flush()  # what the test set before belongs to an earlier, committed transaction
    savepoint = store.conn().begin_nested()
    try:
        done = admin.maybe_daily_reset(store, now=now)
    except Exception:
        savepoint.rollback()
        store.expunge()
        clear_process_cache()
        return None
    savepoint.commit()
    return done


def _failures(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == "app.admin"]


def _resets(store) -> list[tuple]:
    return [(e.user_id, e.user_name, e.role, e.resource_id, e.reason)
            for e in store.audit.query(action="reset", resource_type="demo_data")]


def test_the_daily_reset_leaves_the_storyline_at_step_1_with_margaret_ellis(fresh_state, daily_at_8, client, login):
    fresh_state.modules["daily_reset"] = (datetime.now().date() - timedelta(days=1)).isoformat()  # yesterday's
    assert admin.maybe_daily_reset(fresh_state, now=datetime.now().replace(hour=9)) is True
    assert daily_at_8 == ["generated"]  # one regeneration: the storyline's reset is the demo reset
    view = client.get("/api/storyline", headers=login("U-ADMIN")).json()
    assert view["prepared"] is True and view["current"] == 1
    assert view["steps"][0]["status"] == "active" and not any(s["done"] for s in view["steps"])
    assert (view["patient"]["name"], view["patient"]["story_language"]) == ("Margaret Ellis", "en")
    state = fresh_state.modules["storyline"]
    assert state["operator"] is None and state["ctx"]["operator"] is None  # nobody's: the simulator acts
    [(user_id, user_name, role, resource_id, reason)] = _resets(fresh_state)
    assert (user_id, user_name, role, resource_id) == ("system", "Scheduler", "system", "storyline")
    assert reason == "daily demo reset (demo reset + preset; the patient: English-speaking: Margaret Ellis)"


def test_the_daily_reset_runs_once_a_day(fresh_state, daily_at_8):
    today = datetime.now()
    fresh_state.modules["daily_reset"] = (today.date() - timedelta(days=1)).isoformat()
    assert admin.maybe_daily_reset(fresh_state, now=today.replace(hour=7)) is False  # before the hour
    assert admin.maybe_daily_reset(fresh_state, now=today.replace(hour=9)) is True
    assert admin.maybe_daily_reset(fresh_state, now=today.replace(hour=9, minute=1)) is False
    assert admin.maybe_daily_reset(fresh_state, now=today.replace(hour=15)) is False
    assert daily_at_8 == ["generated"]
    assert fresh_state.modules["daily_reset"] == today.date().isoformat()


def test_a_failing_daily_reset_is_tried_once_a_day_not_every_2_s(fresh_state, daily_at_8, monkeypatch, caplog):
    """The preset raises after the regeneration. Before, the rolled-back step left no marker and the next call, 2 s
    later, regenerated again: on the live server ~400-650 MiB of leak each (casebook 7.8, 7.9) under a 1.5 GiB cap."""
    def plant_fails(*args, **kwargs):
        raise RuntimeError("the preset failed")

    monkeypatch.setattr(journey, "plant", plant_fails)
    today = datetime.now()
    yesterday = (today.date() - timedelta(days=1)).isoformat()
    fresh_state.modules["daily_reset"] = yesterday
    assert _step(fresh_state, today.replace(hour=9, minute=0, second=0)) is None
    assert fresh_state.modules["daily_reset"] == yesterday  # rolled back: today has no reset
    assert _step(fresh_state, today.replace(hour=9, minute=0, second=2)) is False  # the loop's next call
    assert _step(fresh_state, today.replace(hour=15)) is False
    assert daily_at_8 == ["generated"]
    assert _failures(caplog) == [f"daily demo reset of {today.date().isoformat()} failed (RuntimeError); "
                                 "not tried again today in this process"]
    monkeypatch.setattr(admin, "_attempted", None)  # a restart (a deploy, or the memory cap's) tries once more
    assert _step(fresh_state, today.replace(hour=16)) is None
    assert _step(fresh_state, today.replace(hour=16, second=2)) is False
    assert daily_at_8 == ["generated"] * 2
    tomorrow = today + timedelta(days=1)
    assert _step(fresh_state, tomorrow.replace(hour=7)) is False  # before the hour
    assert _step(fresh_state, tomorrow.replace(hour=9)) is None  # a new day: tried again, once
    assert _step(fresh_state, tomorrow.replace(hour=9, second=2)) is False
    assert daily_at_8 == ["generated"] * 3 and len(_failures(caplog)) == 3


def test_a_daily_reset_that_failed_runs_the_next_day(fresh_state, daily_at_8, monkeypatch, caplog, client, login):
    def regenerate(seed=None):  # the regeneration fails today only
        daily_at_8.append("generated")
        if len(daily_at_8) == 1:
            raise RuntimeError("the regeneration failed")
        return Store()

    monkeypatch.setattr(store_module, "build_store", regenerate)
    today = datetime.now()
    fresh_state.modules["daily_reset"] = (today.date() - timedelta(days=1)).isoformat()
    assert _step(fresh_state, today.replace(hour=9)) is None
    assert _step(fresh_state, today.replace(hour=10)) is False
    assert _failures(caplog) == [f"daily demo reset of {today.date().isoformat()} failed (RuntimeError); "
                                 "not tried again today in this process"]
    tomorrow = (today + timedelta(days=1)).replace(hour=9)
    assert _step(fresh_state, tomorrow) is True
    assert _step(fresh_state, tomorrow.replace(second=2)) is False
    assert daily_at_8 == ["generated"] * 2
    assert fresh_state.modules["daily_reset"] == tomorrow.date().isoformat()
    view = client.get("/api/storyline", headers=login("U-ADMIN")).json()
    assert view["prepared"] is True and view["current"] == 1 and view["patient"]["name"] == "Margaret Ellis"


def test_a_reset_after_the_hour_counts_as_todays_daily_reset(fresh_state, daily_at_8):
    reset_store()  # what the storyline's reset and the header's Reset demo do first
    assert fresh_state.modules.get("daily_reset") == datetime.now().date().isoformat()
    assert admin.maybe_daily_reset(fresh_state, now=datetime.now().replace(hour=9)) is False
    assert daily_at_8 == ["generated"]  # the scheduler left the data (and what was planted on it) alone


def test_the_headers_reset_demo_is_still_a_plain_reset(fresh_state, daily_at_8, client, login):
    assert client.post("/api/demo/reset", headers=login("U-ADMIN")).json()["ok"] is True
    assert client.get("/api/storyline", headers=login("U-ADMIN")).json()["prepared"] is False
    assert [r[:4] for r in _resets(fresh_state)] == [("U-ADMIN", "Casey Brooks", "admin", None)]
    assert admin.maybe_daily_reset(fresh_state, now=datetime.now().replace(hour=9)) is False  # today's reset
    assert daily_at_8 == ["generated"]


def test_the_storyline_reset_still_plants_the_chosen_patient(fresh_state, daily_at_8, client, login):
    r = client.post("/api/storyline/reset", headers=login("U-ADMIN"), json={"language": "zh"})
    assert r.status_code == 200 and r.json()["language"] == "zh"
    view = client.get("/api/storyline", headers=login("U-ADMIN")).json()
    assert view["prepared"] is True and view["current"] == 1 and view["patient"]["story_language"] == "zh"
    assert fresh_state.modules["storyline"]["operator"] == "U-ADMIN"
    [(user_id, *_, reason)] = _resets(fresh_state)
    assert user_id == "U-ADMIN" and reason.startswith("storyline reset (demo reset + preset; the patient: Mandarin")
    assert admin.maybe_daily_reset(fresh_state, now=datetime.now().replace(hour=9)) is False  # today's reset
    assert daily_at_8 == ["generated"]
