"""Fast-forward as a background job, for the Control Tower's control bar (WP5).

`simulator.fast_forward` applies a whole simulated night in one request (about
1,600 events, ~11 s on the local machine). The job does the same in steps of 30
hospital minutes, each in its own transaction followed by an event drain, so the
request returns at once, the control bar can show progress, and the boards move
while it runs (every step commits FHIR writes and their domain events). It stops
the running clock first, so the background tick does not race it. One job at a
time per process (the API runs one worker); its state is kept in memory.
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timedelta

from sqlalchemy import text

from app.core.store import REWRITE_LOCK, Store, unit_of_work
from app.ehr import simulator
from app.ehr.events import bus
from app.fhir.dt import parse

log = logging.getLogger("app.simulator")

STEP = timedelta(minutes=30)
_lock = threading.Lock()
_job: dict | None = None


def current() -> dict | None:
    """The last job (running, done or failed), for the control bar."""
    with _lock:
        return dict(_job) if _job else None


def start(store: Store, hour: int = 8, *, operator: str, run_async: bool = True) -> dict:
    """Start fast-forwarding to `hour`:00 tomorrow; raises SimulatorBusy when a job is already running."""
    global _job
    with _lock:
        if _job and _job["status"] == "running":
            raise simulator.SimulatorBusy("A fast-forward is already running")
        simulator.pause(store)  # the background tick must not move the clock meanwhile
        now = parse(simulator.clock(store)["now"])
        target = min(simulator.next_morning(now, hour), parse(store.modules["hospital_plan"]["horizon"]))
        _job = {"id": uuid.uuid4().hex[:12], "status": "running", "start": now.isoformat(),
                "target": target.isoformat(), "now": now.isoformat(), "progress": 0.0, "events": 0,
                "operator": operator, "started_at": datetime.now().isoformat(timespec="seconds"), "error": None}
        job = dict(_job)
    if run_async:
        # the caller's transaction (which paused the clock) commits when its request ends; the job waits for it
        threading.Thread(target=_run, args=(job["id"],), daemon=True, name="fast-forward").start()
    return job


def _update(job_id: str, **values) -> None:
    with _lock:
        if _job and _job["id"] == job_id:
            _job.update(values)


def step(store: Store, target: datetime) -> tuple[datetime, int]:
    """One step towards the target in the caller's transaction: (the clock afterwards, domain events published)."""
    if not store.detached and not store.conn().execute(text("SELECT pg_try_advisory_xact_lock_shared(:key)"),
                                                       {"key": REWRITE_LOCK}).scalar():
        raise simulator.SimulatorBusy("The demo data is being reset")
    now = parse(simulator.clock(store)["now"])
    result = simulator.advance(store, min(now + STEP, target))
    return result.end, sum(result.events.values())


def _run(job_id: str) -> None:
    job = current()
    if job is None:
        return
    start_t, target = parse(job["start"]), parse(job["target"])
    events = 0
    try:
        while True:
            try:
                with unit_of_work() as store:
                    now, n = step(store, target)
            except simulator.SimulatorBusy:  # the pausing request has not committed yet, or a tick holds the lock
                threading.Event().wait(0.2)
                continue
            events += n
            bus.drain()
            span = (target - start_t).total_seconds() or 1
            _update(job_id, now=now.isoformat(), events=events,
                    progress=round(min(1.0, (now - start_t).total_seconds() / span), 3))
            if now >= target:
                break
        _update(job_id, status="done", finished_at=datetime.now().isoformat(timespec="seconds"))
    except Exception as e:  # keep the API alive; the control bar shows the failure
        log.exception("fast-forward failed")
        _update(job_id, status="failed", error=str(e)[:300])


def run_sync(store: Store, target: datetime) -> int:
    """The job's steps in the caller's transaction (tests, scripts): events published."""
    events, before = 0, None
    while True:
        now, n = step(store, target)
        events += n
        if now >= target or now == before:  # there, or the plan's horizon
            return events
        before = now
