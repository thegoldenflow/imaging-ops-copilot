"""The demo reset and REWRITE_LOCK: the data set is generated in memory before the lock is taken, so workflow
activities and background steps (which take the lock shared) wait only while the tables are rewritten. Generating
the real data set takes 15-20 s; here an empty detached store stands in for it, and every reset runs in a transaction
that is rolled back, so the test database keeps its data."""

import threading
import time

from sqlalchemy import text

from app.core import store as store_module
from app.core.store import REWRITE_LOCK, EngineSource, Store, get_engine, get_store, reset_store, use_store


def _lock_waiters(conn) -> int:
    """Transactions waiting for REWRITE_LOCK in this database (pg_locks lists every database of the server)."""
    return conn.execute(text("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND NOT granted "
                             "AND objid::bigint = :key AND database = (SELECT oid FROM pg_database "
                             "WHERE datname = current_database())"), {"key": REWRITE_LOCK}).scalar_one()


def _until(check, what: str) -> None:
    deadline = time.monotonic() + 10
    while not check():
        assert time.monotonic() < deadline, what
        time.sleep(0.02)


def _reset_rolled_back() -> None:
    """A reset in its own transaction (as a request or the daily reset runs it), rolled back afterwards."""
    store = Store(EngineSource(get_engine()))
    try:
        with use_store(store):
            reset_store()
    finally:
        store.rollback()


def test_an_activity_waits_for_a_reset_only_while_it_writes(monkeypatch):
    monkeypatch.setattr(store_module, "_ambient", None)
    engine = get_engine()
    real_persist = store_module.persist
    timeline: list[str] = []
    late: list[threading.Thread] = []

    def activity(name: str) -> None:  # what activities._work() does first
        with engine.connect() as conn:
            conn.execute(text("SELECT pg_advisory_xact_lock_shared(:key)"), {"key": REWRITE_LOCK})
            timeline.append(f"{name} has the lock")
            conn.rollback()

    def generate(seed=None):
        early = threading.Thread(target=activity, args=("activity started while generating",))
        early.start()
        early.join(timeout=10)
        assert not early.is_alive(), "an activity waited while the reset generated its data"
        timeline.append("generated")
        return Store()

    def write(src, into, rows=None):
        waiter = threading.Thread(target=activity, args=("activity started while writing",))
        waiter.start()
        late.append(waiter)
        with engine.connect() as probe:
            _until(lambda: _lock_waiters(probe) == 1, "the activity never waited for the lock")
        real_persist(src, into, rows)
        timeline.append("written")

    monkeypatch.setattr(store_module, "build_store", generate)
    monkeypatch.setattr(store_module, "persist", write)
    store = Store(EngineSource(engine))
    try:
        with use_store(store):
            reset_store()
        time.sleep(0.2)
        assert late[0].is_alive()  # the lock is the reset transaction's, held until it ends
        timeline.append("the reset's transaction ends")
    finally:
        store.rollback()
    late[0].join(timeout=10)
    assert timeline == ["activity started while generating has the lock", "generated", "written",
                        "the reset's transaction ends", "activity started while writing has the lock"]


def test_two_resets_generate_at_once_and_write_one_after_the_other(monkeypatch):
    monkeypatch.setattr(store_module, "_ambient", None)
    engine = get_engine()
    real_persist = store_module.persist
    generating = threading.Barrier(2, timeout=10)  # broken if one reset generated under the other's lock
    events: list[str] = []
    errors: list[BaseException] = []

    def generate(seed=None):
        generating.wait()
        return Store()

    def write(src, into, rows=None):
        me = threading.current_thread().name
        first = not events
        events.append(f"{me} writes")
        if first:  # keep the lock until the other reset waits for it
            with engine.connect() as probe:
                _until(lambda: _lock_waiters(probe) == 1, "the second reset never waited for the lock")
        real_persist(src, into, rows)
        events.append(f"{me} has written")

    def run() -> None:
        try:
            _reset_rolled_back()
        except BaseException as exc:
            errors.append(exc)

    monkeypatch.setattr(store_module, "build_store", generate)
    monkeypatch.setattr(store_module, "persist", write)
    resets = [threading.Thread(target=run, name=name) for name in ("A", "B")]
    for t in resets:
        t.start()
    for t in resets:
        t.join(timeout=30)
    assert errors == [] and not any(t.is_alive() for t in resets)
    first, second = events[0].split()[0], events[2].split()[0]
    assert {first, second} == {"A", "B"}
    assert events == [f"{first} writes", f"{first} has written", f"{second} writes", f"{second} has written"]


def test_a_detached_store_keeps_its_caches_to_itself(fresh_state):
    """The generator runs next to units of work that use the process's caches: the PHIPA seed reads phipa_cache
    (phipa.detect) while it plants the investigated alert, and the inspection seed drops inspection_index."""
    db = get_store()
    db.modules["phipa_cache"] = (3, ["derived from the database"])
    db.modules["inspection_index"] = {"chunks": ["from the database"]}

    generated = Store()
    assert generated.modules.get("phipa_cache") is None
    generated.modules["phipa_cache"] = (3, ["generated"])
    generated.modules.pop("inspection_index")
    assert generated.modules.module("noshow", lambda: "trained on the generated data") == "trained on the generated data"

    assert db.modules.get("phipa_cache") == (3, ["derived from the database"])
    assert db.modules.get("inspection_index") == {"chunks": ["from the database"]}
    assert db.modules.get("noshow") is None
    assert generated.modules.get("phipa_cache") == (3, ["generated"])
