"""The database layer under threads: the workflow worker runs its activities in a thread pool (8 threads; 2 inside
the API), each with its own unit of work, and Temporal cancels a timed-out activity by raising an exception inside
its thread."""

import ctypes
import logging
import threading
import time
import uuid

import psycopg.waiting
import pytest
from sqlalchemy import text

from app.core import store as store_module
from app.core.db import repo, schema
from app.core.store import get_engine, unit_of_work


@pytest.fixture
def probe_table(monkeypatch):
    """A table no thread has built yet, with a slow model import so that every thread is inside the first build
    at the same time (what happens when the worker starts and its activity threads open their first Store)."""
    name = f"race_probe_{uuid.uuid4().hex[:8]}"
    monkeypatch.setitem(schema.MODULE_ENTITIES, name, schema.EntitySpec(name, f"{schema.CORE}:Site"))
    real_import = schema._import

    def slow_import(ref):
        time.sleep(0.2)
        return real_import(ref)

    monkeypatch.setattr(schema, "_import", slow_import)
    yield name
    repo._CODECS.pop(name, None)
    schema._MAPPINGS.pop(name, None)
    if name in schema.metadata.tables:
        schema.metadata.remove(schema.metadata.tables[name])


def test_threads_building_the_same_codec_at_once_share_one_table(probe_table):
    threads = 8
    barrier = threading.Barrier(threads)
    codecs, errors = [], []

    def build():
        barrier.wait()
        try:
            codecs.append(repo._codec(probe_table))
        except Exception as exc:  # before the fix: "Table ... is already defined for this MetaData instance"
            errors.append(exc)

    workers = [threading.Thread(target=build) for _ in range(threads)]
    for w in workers:
        w.start()
    for w in workers:
        w.join(timeout=30)
    assert errors == []
    assert len(codecs) == threads and len({id(c) for c in codecs}) == 1
    assert codecs[0].m.table is schema.metadata.tables[probe_table]


class Cancelled(Exception):
    """Stands in for temporalio's CancelledError, which the SDK raises inside an activity's thread."""


def _raise_in_thread(thread: threading.Thread, exc_type: type[BaseException]) -> None:
    hit = ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(thread.ident), ctypes.py_object(exc_type))
    assert hit == 1


def _lock_waiters(conn, key: int) -> int:
    return conn.execute(text("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND NOT granted "
                             "AND objid::bigint = :key"), {"key": key}).scalar_one()


def test_a_query_interrupted_from_another_thread_gives_its_connection_back(monkeypatch, caplog):
    """An activity waits for the demo-reset lock (held while a reset runs) and Temporal cancels it at its
    start-to-close timeout: the exception lands while psycopg waits for the result. The unit of work must roll back
    without sending commands into the busy connection, stop the waiting query and leave the pool clean."""
    monkeypatch.setattr(store_module, "_ambient", None)  # a real unit of work, as in the worker
    # On Windows psycopg waits for results in a Python loop, where the exception lands mid-query (as in the worker's
    # log); elsewhere it waits in C, which may hold the exception back until the result is in. Wait the Windows way.
    monkeypatch.setattr(psycopg.waiting, "wait", psycopg.waiting.wait_select)
    engine = get_engine()
    key = 0x7E57_0C0D
    checked_out = engine.pool.checkedout()
    outcome: list[BaseException | None] = []

    def activity():
        try:
            with unit_of_work() as store:
                store.conn().execute(text("SELECT pg_advisory_xact_lock_shared(:key)"), {"key": key})
            outcome.append(None)
        except BaseException as exc:
            outcome.append(exc)

    with engine.connect() as holder:  # the reset, holding the lock exclusively
        holder.execute(text("SELECT pg_advisory_lock(:key)"), {"key": key})
        worker = threading.Thread(target=activity)
        try:
            worker.start()
            deadline = time.monotonic() + 10
            while _lock_waiters(holder, key) == 0:
                assert time.monotonic() < deadline, "the activity never started waiting for the lock"
                time.sleep(0.05)
            with caplog.at_level(logging.ERROR):
                _raise_in_thread(worker, Cancelled)
                worker.join(timeout=10)
                assert not worker.is_alive()
            # The cancellation itself comes out, not "another command is already in progress" from the rollback
            assert len(outcome) == 1 and isinstance(outcome[0], Cancelled), outcome
            assert not [r for r in caplog.records if r.name.startswith("sqlalchemy.pool")]
            assert engine.pool.checkedout() == checked_out + 1  # the holder's only
            # The query no longer waits on the server (otherwise it would take the lock when the reset ends)
            deadline = time.monotonic() + 5
            while _lock_waiters(holder, key):
                assert time.monotonic() < deadline, "the interrupted query still waits for the lock"
                time.sleep(0.05)
        finally:
            holder.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
            holder.commit()
            worker.join(timeout=10)

    with unit_of_work() as store:  # the pool still hands out working connections
        assert store.conn().execute(text("SELECT 1")).scalar_one() == 1
    assert engine.pool.checkedout() == checked_out


def test_a_store_that_fails_to_build_holds_no_connection(monkeypatch):
    monkeypatch.setattr(store_module, "_ambient", None)
    engine = get_engine()
    checked_out = engine.pool.checkedout()

    def broken(name):
        raise RuntimeError(f"no codec for {name}")

    monkeypatch.setattr(repo, "_codec", broken)
    with pytest.raises(RuntimeError, match="no codec"):
        with unit_of_work():
            pass
    assert engine.pool.checkedout() == checked_out


def test_a_test_whose_connection_is_left_mid_query_still_ends_cleanly(fresh_state, monkeypatch):
    """What a workflow activity cancelled in its thread leaves behind (tests/temporal_harness.py): the test's one
    connection still owes the result of a query. The check is the fixture's teardown (tests/conftest.py): it drops
    that connection, its query cancelled, where a ROLLBACK fails with "another command is already in progress"."""
    monkeypatch.setattr(psycopg.waiting, "wait", psycopg.waiting.wait_select)
    conn = fresh_state.conn()
    key = 0x7E57_7EA2
    outcome: list[BaseException | None] = []

    def activity():
        try:
            conn.execute(text("SELECT pg_advisory_xact_lock_shared(:key)"), {"key": key})
            outcome.append(None)
        except BaseException as exc:
            outcome.append(exc)

    with get_engine().connect() as holder:
        holder.execute(text("SELECT pg_advisory_lock(:key)"), {"key": key})
        worker = threading.Thread(target=activity)
        try:
            worker.start()
            deadline = time.monotonic() + 10
            while _lock_waiters(holder, key) == 0:
                assert time.monotonic() < deadline, "the query never started waiting for the lock"
                time.sleep(0.05)
            _raise_in_thread(worker, Cancelled)
            worker.join(timeout=10)
        finally:
            holder.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
            holder.commit()
    assert len(outcome) == 1 and isinstance(outcome[0], Cancelled), outcome
    assert store_module._busy(conn)
