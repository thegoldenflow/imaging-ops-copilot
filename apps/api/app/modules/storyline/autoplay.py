"""Demo mode's "Do this step for me" and "Jump to step N" (spec 8.2, WP9): the storyline's automated walk, run by the
API in the background.

The walk (walk.py) does each step through the HTTP API as the people who play it: the same requests the web app
makes, the same checks and records. Here it runs inside the API process. Its requests go through the app in process
(Starlette's test transport: no port, no network; each request is its own transaction, as from a browser), the
hospital's background loops do the work between them, and the clock moves with POST /api/storyline/advance. One job
at a time per process (the API runs one worker), kept in memory like the fast-forward's (app/ehr/simjobs.py);
GET /api/storyline shows it (`autoplay`) and marks the steps it did (`automated`).

Who did what stays plain:
- the job is the presenter's demo action, audited under their name (`simulator_event`, action `autoplay`) with the
  steps and the people it acts as;
- it signs in as each step's person with tokens registered as the automation's (app/core/context.py), so every audit
  event of those requests and every signature it adds say "Demo automation on behalf of <name> (<role>), started by
  <presenter>", and so do the notes and reasons the walk writes.

`do_step` (the current step) and `jump` (every step before the target) validate and register the job; with the
background workers on, a thread runs it. Without them (tests, scripts) the caller runs it: `run(job_id, client, pump,
advance)`, e.g. with the in-process harness (tests/storyline_harness.py).
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Callable

from app.core.config import settings
from app.core.context import register_automation, unregister_automation
from app.core.models import StaffUser
from app.core.store import Store, unit_of_work
from app.modules.storyline import script as S

log = logging.getLogger("app.storyline")

# The walk's wait for one step's background work in the running API (scripts/storyline_report.py's default), and the
# fast-forward to a beat (the discharge and the 48 hours after it take a minute or two).
STEP_TIMEOUT_S = 180.0
CLIENT_ADDRESS = ("demo-automation", 0)  # the source address its requests are audited with

_lock = threading.Lock()
_job: dict | None = None
_marks: dict[str, dict[str, dict]] = {}  # storyline reset (prepared_at) -> step key -> who had it done, when


class Busy(Exception):
    pass


def current(prepared_at: str | None = None) -> dict | None:
    """The last job (running, done or failed); with `prepared_at`, only one of that storyline reset."""
    with _lock:
        if _job is None or (prepared_at is not None and _job["prepared_at"] != prepared_at):
            return None
        return {k: (list(v) if isinstance(v, list) else v) for k, v in _job.items() if k != "people"}


def marks(prepared_at: str | None) -> dict[str, dict]:
    """The steps the automation did since that reset: key -> {by, as, at, job}."""
    with _lock:
        return dict(_marks.get(prepared_at or "", {}))


def on_reset() -> None:
    """The storyline was reset: a running job stops after its step (its data is gone), the marks with the data."""
    with _lock:
        if _job is not None and _job["status"] == "running":
            _job["cancelled"] = True
        _marks.clear()


def _update(job_id: str, **values) -> None:
    with _lock:
        if _job is not None and _job["id"] == job_id:
            _job.update(values)


def _label(role: str) -> str:
    return str(role).replace("_", " ")


# ---------- starting a job ----------


def do_step(store: Store, user: StaffUser, key: str, *, source_ip: str | None = None) -> dict:
    """The current step, done by the demo's automation."""
    from app.modules.storyline import service

    step = _step(key)
    view = service.status(store, user)
    if view["current"] is None:
        raise ValueError("The story is complete")
    if step.n != view["current"]:
        raise ValueError(f"Step {step.n} is {'done' if step.n < view['current'] else 'not reached yet'}: the current "
                         f"step is {view['current']} (jump to a later step instead)")
    return _start(store, user, [key], view, mode="step", target=None, source_ip=source_ip)


def jump(store: Store, user: StaffUser, key: str, *, source_ip: str | None = None) -> dict:
    """Every step from the current one up to the one before `key`, done by the demo's automation: `key` is then the
    current step."""
    from app.modules.storyline import service

    target = _step(key)
    view = service.status(store, user)
    if view["current"] is None:
        raise ValueError("The story is complete")
    if target.n <= view["current"]:
        raise ValueError(f"Step {target.n} is {'already the current step' if target.n == view['current'] else 'done'}")
    keys = [s.key for s in S.STEPS if view["current"] <= s.n < target.n]
    return _start(store, user, keys, view, mode="jump", target=target.n, source_ip=source_ip)


def _step(key: str) -> S.Step:
    step = S.BY_KEY.get(key)
    if step is None:
        raise LookupError(f"No storyline step {key!r}")
    return step


def _start(store: Store, user: StaffUser, keys: list[str], view: dict, *, mode: str, target: int | None,
           source_ip: str | None) -> dict:
    global _job
    from app.modules.storyline import service

    st = service.state(store)
    if st is None or not view["prepared"]:
        raise ValueError("Reset the storyline first")
    people = {uid: (u.name, _label(u.role)) for uid in {*S.CAST.values(), user.id}
              if (u := store.staff.get(uid)) is not None}
    steps = [S.BY_KEY[k] for k in keys]
    acting = ", ".join(dict.fromkeys(people[S.CAST[s.cast]][0] for s in steps if S.CAST[s.cast] in people))
    span = f"step {steps[0].n}" if len(steps) == 1 else f"steps {steps[0].n}-{steps[-1].n}"
    with _lock:
        if _job is not None and _job["status"] == "running":
            raise Busy(f"The demo's automation is still doing step {_job.get('current_n') or _job['steps'][0]}")
        _job = {"id": uuid.uuid4().hex[:12], "status": "running", "mode": mode, "keys": keys,
                "steps": [s.n for s in steps], "target": target, "done": [], "current": keys[0],
                "current_n": steps[0].n, "current_title": steps[0].title,
                "current_cast": people.get(S.CAST[steps[0].cast], ("?",))[0], "results": [],
                "started_by": {"id": user.id, "name": user.name, "role": str(user.role)},
                "started_at": datetime.now().isoformat(timespec="seconds"), "finished_at": None, "error": None,
                "prepared_at": st["prepared_at"], "cancelled": False, "people": people}
        job = {k: v for k, v in _job.items() if k != "people"}
    store.audit.record(user_id=user.id, user_name=user.name, role=str(user.role), action="autoplay",
                       resource_type="storyline_step", resource_id=keys[0] if len(keys) == 1 else f"{keys[0]}..{keys[-1]}",
                       source_ip=source_ip, event_type="simulator_event", module="storyline",
                       reason=f"storyline {span} done by the demo's automation"
                              f"{f' (jump to step {target})' if target else ''}, acting as {acting}")
    if settings.background_workers:
        threading.Thread(target=_run_in_api, args=(job["id"],), daemon=True, name="storyline-autoplay").start()
    return job


# ---------- running it ----------


def _run_in_api(job_id: str) -> None:
    from fastapi.testclient import TestClient

    from app.main import app
    from app.modules.storyline.walk import api_advance, summary_draft_s

    job = current()
    if job is None:
        return
    client = TestClient(app, client=CLIENT_ADDRESS)  # the app in process: no lifespan, no port
    run(job_id, client=client, pump=lambda: time.sleep(1.0),
        advance=api_advance(client, user=job["started_by"]["id"]), timeout_s=STEP_TIMEOUT_S,
        summary_timeout_s=STEP_TIMEOUT_S + summary_draft_s())


def run(job_id: str, *, client: Any, pump: Callable[[], None], advance: Callable[[Any, str], None],
        timeout_s: float = STEP_TIMEOUT_S, summary_timeout_s: float | None = None) -> dict | None:
    """Walk the job's steps in order as the cast, signed in with the automation's tokens; stops at the first step that
    fails (the job says why). Returns the job as it ended."""
    from app.modules.storyline.walk import Walk

    with _lock:
        job = dict(_job) if _job is not None and _job["id"] == job_id else None
    if job is None:
        return None
    presenter = job["started_by"]["name"]
    tokens: dict[str, str] = {}
    try:
        with unit_of_work() as store:  # committed before the first request uses them
            for uid in job["people"]:
                tokens[uid] = secrets.token_urlsafe(24)
                store.add_session(tokens[uid], uid)
        for token in tokens.values():
            register_automation(token, presenter)
        walk = Walk(client, pump=pump, advance=advance, log=lambda m: log.info("autoplay %s", m),
                    timeout_s=timeout_s, summary_timeout_s=summary_timeout_s, automation=presenter,
                    people=job["people"])
        walk.tokens.update(tokens)
        done, results = [], []
        for key in job["keys"]:
            if (now := current()) is not None and now.get("cancelled"):
                raise RuntimeError("the storyline was reset")
            step = S.BY_KEY[key]
            _update(job_id, current=key, current_n=step.n, current_title=step.title,
                    current_cast=job["people"].get(S.CAST[step.cast], ("?",))[0])
            try:
                walk.run([key])
            finally:
                r = walk.runs[-1] if walk.runs and walk.runs[-1].key == key else None
                if r is not None:
                    results.append({"n": r.n, "key": r.key, "ok": r.ok, "seconds": r.seconds, "result": r.result})
                    _update(job_id, results=list(results))
            done.append(key)
            with _lock:
                if _job is not None and _job["id"] == job_id and not _job.get("cancelled"):
                    _marks.setdefault(job["prepared_at"], {})[key] = {
                        "by": presenter, "as": job["people"].get(S.CAST[step.cast], ("?",))[0],
                        "at": datetime.now().isoformat(timespec="seconds"), "job": job_id}
            _update(job_id, done=list(done))
        _update(job_id, status="done", current=None, current_n=None, current_title=None, current_cast=None,
                finished_at=datetime.now().isoformat(timespec="seconds"))
    except Exception as e:  # keep the API alive; the guide shows where it stopped and why
        log.exception("storyline autoplay failed")
        _update(job_id, status="failed", error=f"{type(e).__name__}: {e}"[:400],
                finished_at=datetime.now().isoformat(timespec="seconds"))
    finally:
        unregister_automation(tokens.values())
    return current()
