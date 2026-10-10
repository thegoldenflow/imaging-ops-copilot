"""Temporal's time-skipping test server for the workflow tests (spec 6.5).

Each test gets its own server (a binary the SDK downloads once; about a second to start), so workflow ids, which
are business keys, never collide between tests; it runs on an event loop thread shared by the session, and the
test starts a worker in this process. Its activities run in one worker thread and use the test's store (the ambient
store of tests/conftest.py), so everything they write is rolled back with the test. One connection serves both
threads, so every database access goes through `DB` (activities through a patched unit of work, the test body
explicitly with `with DB:`). Tests that need the server skip when it cannot be started.

Test workers keep no workflow cache (`max_cached_workflows=0`): every workflow task replays the whole history, so
every test also checks that the workflow code is deterministic at every step. (With a cache, Temporal sends a
workflow's next task to the worker that ran the last one, on a sticky queue; a real server hands it to another
worker after 10 seconds when that worker is gone, but the time-skipping test server never does, so a restart
test would hang. scripts/workflow_demo.py kills a real worker process against the dev server instead.)

`pump()` does what the API's background loops do: deliver domain events to the bridge and send its commands to
Temporal (through an `EnvLink` on the test server).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import threading
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from temporalio.client import Client
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from app.core import store as core_store
from app.ehr.events import bus
from app.workflows import activities, bridge, client, faults, progress, worker

TASK_QUEUE = "hospital-workflows-test"
DB = threading.RLock()
HISTORIES = Path(__file__).parent / "fixtures" / "workflows"  # captured histories for the replay tests


class _Loop:
    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, name="temporal-test", daemon=True).start()

    def run(self, coro, timeout: float = 120):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)


_state: dict = {}


def environment() -> tuple[_Loop, WorkflowEnvironment]:
    """A fresh time-skipping test server (skips the test when it cannot start, e.g. offline)."""
    if "error" in _state:
        pytest.skip(_state["error"])
    loop = _state.setdefault("loop", _Loop())
    try:
        env = loop.run(WorkflowEnvironment.start_time_skipping(), timeout=300)
    except Exception as e:  # noqa: BLE001  (the binary could not be downloaded or started)
        _state["error"] = f"Temporal test server unavailable: {e}"
        pytest.skip(_state["error"])
    return loop, env


class EnvLink(client.TemporalLink):
    """The bridge's link, on the test server's client and loop."""

    def __init__(self, env_client: Client, loop: _Loop) -> None:
        super().__init__("time-skipping-test-server", env_client.namespace, TASK_QUEUE)
        self._client, self._loop = env_client, loop.loop

    def workers(self) -> int:
        return 1


@contextlib.contextmanager
def _locked_unit_of_work():
    with DB, core_store.unit_of_work() as store:
        yield store


class Harness:
    def __init__(self, loop: _Loop, env: WorkflowEnvironment) -> None:
        self.loop, self.env = loop, env
        self.client = env.client
        self.link = EnvLink(env.client, loop)
        self.worker: Worker | None = None
        self._task = None

    # ----- the worker -----

    def start_worker(self) -> None:
        async def start():
            self.worker = worker.build(self.client, task_queue=TASK_QUEUE, threads=1, max_cached_workflows=0)
            self._task = asyncio.ensure_future(self.worker.run())
            await asyncio.sleep(0.3)
            if self._task.done():  # it failed to start: say why
                self._task.result()
        self.loop.run(start())

    def stop_worker(self) -> None:
        """Shut the worker down (its in-flight activities are cancelled), as when its process is killed."""
        if self.worker is None:
            return
        w, task = self.worker, self._task
        self.worker = self._task = None

        async def stop():
            await w.shutdown()
            with contextlib.suppress(Exception):
                await task
        self.loop.run(stop(), timeout=60)

    # ----- the API's background work -----

    def pump(self) -> None:
        with DB:
            bus.drain()
            bridge.dispatch_step(core_store.get_store())

    def skip(self, seconds: float) -> None:
        """Move the test server's clock (timers fire)."""
        self.loop.run(self.env.sleep(timedelta(seconds=seconds)), timeout=120)

    def wait(self, predicate, *, timeout: float = 60, what: str = "the workflow") -> object:
        """Pump and poll (under the lock) until the predicate returns something truthy."""
        deadline = time.monotonic() + timeout
        while True:
            self.pump()
            with DB:
                value = predicate()
            if value:
                return value
            if time.monotonic() > deadline:
                with DB:
                    runs = {r.id: (r.status, r.current_step, [(s["key"], s["status"], s.get("detail"))
                                                             for s in r.steps if s["status"] != "pending"])
                            for r in progress.all_runs()}
                tails = {}
                for workflow_id in runs:
                    with contextlib.suppress(Exception):
                        events = self.history(workflow_id).events[-12:]
                        tails[workflow_id] = [(e.event_id, e.WhichOneof("attributes"), e.workflow_task_scheduled_event_attributes.task_queue.kind if e.HasField("workflow_task_scheduled_event_attributes") else None) for e in events]
                raise AssertionError(f"timed out waiting for {what}; workflows: {runs}; last events: {tails}")
            time.sleep(0.2)

    def step(self, workflow_id: str, key: str) -> dict | None:
        run = progress.get(workflow_id)
        return next((s for s in run.steps if s["key"] == key), None) if run else None

    def wait_step(self, workflow_id: str, key: str, statuses: tuple[str, ...], timeout: float = 60) -> dict:
        return self.wait(lambda: (s := self.step(workflow_id, key)) and s["status"] in statuses and s,
                         timeout=timeout, what=f"{workflow_id} {key} in {statuses}")

    def history(self, workflow_id: str):
        return self.loop.run(self.client.get_workflow_handle(workflow_id).fetch_history())

    def capture(self, workflow_id: str, name: str) -> None:
        """With CAPTURE_WORKFLOW_HISTORIES=1, keep the workflow's history for tests/test_workflow_replay.py."""
        if os.environ.get("CAPTURE_WORKFLOW_HISTORIES") != "1":
            return
        HISTORIES.mkdir(parents=True, exist_ok=True)
        (HISTORIES / f"{name}.json").write_text(self.history(workflow_id).to_json(), encoding="utf-8")

    def result(self, workflow_id: str, timeout: float = 120):
        return self.loop.run(self.client.get_workflow_handle(workflow_id).result(), timeout=timeout)


@contextlib.contextmanager
def harness(monkeypatch):
    loop, env = environment()
    monkeypatch.setattr(activities, "unit_of_work", _locked_unit_of_work)
    monkeypatch.setattr(faults, "unit_of_work", _locked_unit_of_work)
    original_conn = core_store.Store.conn

    def guarded_conn(self):  # a worker thread uses the shared connection only under the lock (catches one that forgot)
        if not self.detached and threading.current_thread().name.startswith("activity") and not DB._is_owned():
            raise AssertionError(f"database used without the harness lock in thread "
                                 f"{threading.current_thread().name}")
        return original_conn(self)

    monkeypatch.setattr(core_store.Store, "conn", guarded_conn)
    h = Harness(loop, env)
    with client.use(h.link):
        bridge.install()
        h.start_worker()
        try:
            yield h
        finally:
            h.stop_worker()
            bridge.uninstall()
            _shutdown(h)


def _shutdown(h: Harness) -> None:
    with contextlib.suppress(Exception):
        h.loop.run(h.env.shutdown(), timeout=60)
