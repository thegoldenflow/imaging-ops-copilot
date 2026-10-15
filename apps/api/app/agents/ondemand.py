"""AI on demand: the items that get AI output, their AI state, and the jobs that run it (BACKGROUND_AI).

With BACKGROUND_AI=on_demand (the demo's default) no model call is made for work nobody asked for. An item's AI
runs when a person opens it or presses Ask AI (in the request: `run_now`), and at once for new work their own
action created or for a batch they asked for (`request`: the API's job loop runs it, app/main.py). The rule checks
keep running in the background either way. BACKGROUND_AI=eager keeps the background writing the AI output of every
new item before anyone looks (a "real hospital" mode, and the evals).

Each module registers its kinds of item (`register`): order reviews, reconciliations and Control Tower exceptions,
requisitions and patient feedback. A kind knows how to run the AI for one item (waiting for a run of the same item
already under way, so a double click or two people opening it make one call), who may ask, and what the item is
called. `view` gives every item the same AI block for the web app:

    state      not_requested | queued | running | done | unavailable | skipped
    auto       opening the item runs it (False: only Ask AI, e.g. a test order without a rule flag)
    typical_s  how long it usually takes (measured here, else a default for the kind)
    started_at / step / steps while running; generated_at, duration_ms, run_id, cause once done; error

The running and queued registry is this process's (the API runs one worker, docs/DEPLOY.md); the item's own record
says whether its AI has run. Who caused each run is on its trace (app/agents/cause.py).
"""

from __future__ import annotations

import logging
import statistics
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.agents import cause as causes
from app.core import config

log = logging.getLogger("app.ondemand")

STATES = ("not_requested", "queued", "running", "done", "unavailable", "skipped")
CONCURRENCY = 2  # jobs the loop runs at once: a batch should not starve the people opening items
BATCH_MAX = 50
RETRY_UNSEEN = 20  # a job for an item its request has not committed yet waits up to this many half-seconds


class NotAllowed(PermissionError):
    pass


@dataclass(frozen=True)
class Kind:
    name: str  # order_review, medrec, exception, requisition, feedback
    noun: str  # "order review"
    running_text: str  # "AI is reviewing this order"
    agents: tuple[str, ...]  # the agents its run calls
    calls: int  # model calls per item when all goes well
    live_s: float  # typical seconds on a live model before anything was measured here
    run: Callable[[Any, str], Any]  # (store, item_id): runs the AI; waits for a run of the item under way elsewhere
    needs_ai: Callable[[Any, str], bool]  # (store, item_id): its AI has not run (a batch skips the rest)
    check: Callable[[Any, Any, str], None]  # (store, user, item_id): LookupError / NotAllowed
    describe: Callable[[Any, str], tuple[str, str | None]]  # (store, item_id) -> (label, link)
    steps: tuple[str, ...] = ()  # the stages a run goes through, shown while it runs
    retry: Callable[[Any, str], None] | None = None  # (store, item_id): make a failed run ask again (Ask AI/Retry)


KINDS: dict[str, Kind] = {}


def register(kind: Kind) -> Kind:
    KINDS[kind.name] = kind
    return kind


def kind(name: str) -> Kind:
    if name not in KINDS:
        raise LookupError(f"No AI item kind {name!r}")
    return KINDS[name]


def on_demand() -> bool:
    return config.settings.background_ai == "on_demand"


# ---------- what is running and queued in this process ----------


@dataclass
class Job:
    kind: str
    item_id: str
    cause: causes.AiCause | None
    queued_at: datetime = field(default_factory=datetime.now)
    attempts: int = 0
    not_before: float = 0.0


_lock = threading.Lock()
_running: dict[tuple[str, str], dict] = {}
_queue: deque[Job] = deque()
_durations: dict[tuple[str, str], deque] = {}  # (model mode, kind) -> recent run seconds


def _key(kind_name: str, item_id: str) -> tuple[str, str]:
    return (kind_name, item_id)


def live(kind_name: str, item_id: str) -> dict | None:
    """{state: running|queued, ...} while this process runs or holds a job for the item."""
    key = _key(kind_name, item_id)
    with _lock:
        if (r := _running.get(key)) is not None:
            return {"state": "running", "started_at": r["started_at"], "step": r.get("step"),
                    "cause": r["cause"].model_dump(exclude_none=True) if r["cause"] else None}
        for position, job in enumerate(_queue):
            if (job.kind, job.item_id) == key:
                return {"state": "queued", "queued_at": job.queued_at, "ahead": position}
    return None


def progress(kind_name: str, item_id: str, step: str) -> None:
    """The stage a running item has reached (a requisition: extraction, triage, protocol)."""
    with _lock:
        if (r := _running.get(_key(kind_name, item_id))) is not None:
            r["step"] = step


def snapshot() -> dict:
    """Everything running or queued, for the activity panel and the list pages' batch progress."""
    with _lock:
        running = [{"kind": k, "item_id": i, "started_at": r["started_at"], "step": r.get("step"),
                    "label": r.get("label"), "link": r.get("link"),
                    "cause": r["cause"].model_dump(exclude_none=True) if r["cause"] else None}
                   for (k, i), r in _running.items()]
        queued = [{"kind": j.kind, "item_id": j.item_id, "queued_at": j.queued_at,
                   "cause": j.cause.model_dump(exclude_none=True) if j.cause else None} for j in _queue]
    return {"running": running, "queued": queued}


def _record(kind_name: str, seconds: float) -> None:
    from app.llm.gateway import get_gateway

    with _lock:
        _durations.setdefault((get_gateway().mode, kind_name), deque(maxlen=12)).append(seconds)


def typical_s(kind_name: str) -> float:
    """The median of the recent runs of this kind on this model, else the kind's default (mock: about 0.6 s a
    call plus the tools; live: measured on Gemini 3.5 Flash)."""
    from app.llm.gateway import get_gateway

    mode = get_gateway().mode
    with _lock:
        seen = list(_durations.get((mode, kind_name)) or [])
    if seen:
        return round(statistics.median(seen), 1)
    k = KINDS.get(kind_name)
    if k is None:
        return 5.0
    return round(k.calls * 0.8, 1) if mode == "mock" else k.live_s


def run_now(store, kind_name: str, item_id: str, *, trigger: causes.Trigger = "opened",
            cause: causes.AiCause | None = None, note: str | None = None) -> None:
    """Run one item's AI in this thread (a person's request, or a job). One run per item at a time in this process:
    a second caller waits for the first to finish (the kind's own lock covers other processes)."""
    k = kind(kind_name)
    key = _key(kind_name, item_id)
    if cause is None and (queued := _take_queued(key)) is not None:
        # already asked for (a person's new work, a batch, a storyline step): run it now, as what it was asked for
        cause = queued.cause or causes.AiCause(trigger="background")
    if trigger == "asked" and cause is None and k.retry is not None:
        k.retry(store, item_id)  # a person asks again after the AI was unavailable
    label, link = _describe(k, store, item_id)  # a database read: not under the registry's lock
    with _lock:
        running = _running.get(key)
        if running is None:
            event = threading.Event()
            _running[key] = running_entry = {"started_at": datetime.now(), "event": event,
                                             "cause": cause or causes.current(), "label": label, "link": link}
    if running is not None:  # the same item is being run by another request or job: wait for it
        running["event"].wait(timeout=180)
        return
    started = time.monotonic()
    try:
        with causes.using(cause) if cause is not None else _nothing():
            with causes.about(kind_name, item_id, trigger=None if cause is not None else trigger,
                              label=running_entry["label"], link=running_entry["link"], note=note) as c:
                running_entry["cause"] = c
                k.run(store, item_id)
        _record(kind_name, time.monotonic() - started)
    finally:
        with _lock:
            _running.pop(key, None)
        event.set()


def _take_queued(key: tuple[str, str]) -> Job | None:
    with _lock:
        for job in list(_queue):
            if (job.kind, job.item_id) == key:
                _queue.remove(job)
                return job
    return None


class _nothing:
    def __enter__(self):
        return None

    def __exit__(self, *exc):
        return False


def _describe(k: Kind, store, item_id: str) -> tuple[str, str | None]:
    try:
        return k.describe(store, item_id)
    except Exception:  # a label is never worth failing the run
        return f"{k.noun.capitalize()} {item_id}", None


def request(kind_name: str, item_id: str, cause: causes.AiCause | None = None) -> bool:
    """Queue one item for the job loop (new work a person's action created, a batch). False when it is already
    queued or running."""
    kind(kind_name)
    key = _key(kind_name, item_id)
    with _lock:
        if key in _running or any((j.kind, j.item_id) == key for j in _queue):
            return False
        _queue.append(Job(kind_name, item_id, cause or causes.current()))
    return True


def next_job() -> Job | None:
    """The first job whose item can be run now (the job loop)."""
    now = time.monotonic()
    with _lock:
        for job in list(_queue):
            if job.not_before <= now:
                _queue.remove(job)
                return job
    return None


def run_job(store, job: Job) -> None:
    """Run a queued job (in its own unit of work). An item its request has not committed yet waits a little."""
    try:
        run_now(store, job.kind, job.item_id, cause=job.cause)
    except LookupError:
        if job.attempts >= RETRY_UNSEEN:
            log.warning("AI job %s %s: the item never appeared", job.kind, job.item_id)
            return
        job.attempts += 1
        job.not_before = time.monotonic() + 0.5
        with _lock:
            _queue.append(job)
    except NotAllowed as e:  # e.g. the exception cleared before its turn
        log.info("AI job %s %s skipped: %s", job.kind, job.item_id, e)


_remembered: dict[str, tuple[float, causes.AiCause]] = {}
REMEMBER_S = 3600


def remember(key: str, cause: causes.AiCause | None) -> None:
    """A person's action that the background will follow up with AI (signing a discharge summary: its patient
    instructions): the follow-up's runs are theirs (`recall`). Process memory, an hour at most."""
    if cause is None:
        return
    now = time.monotonic()
    with _lock:
        for k in [k for k, (at, _) in _remembered.items() if now - at > REMEMBER_S]:
            _remembered.pop(k)
        _remembered[key] = (now, cause)


def recall(key: str) -> causes.AiCause | None:
    with _lock:
        hit = _remembered.get(key)
    return hit[1] if hit is not None and time.monotonic() - hit[0] <= REMEMBER_S else None


def requeue(job: Job) -> None:
    """Put a job back (the data was being rewritten when its turn came)."""
    job.not_before = time.monotonic() + 1.0
    with _lock:
        _queue.append(job)


def drain(store) -> int:
    """Tests and scripts: run every queued job now, in this unit of work."""
    done = 0
    while (job := next_job()) is not None or _queue:
        if job is None:
            time.sleep(0.05)
            continue
        run_job(store, job)
        done += 1
    return done


def reset(*, everything: bool = False) -> None:
    """A demo reset: queued jobs refer to the old data. `everything` (tests): also the measured durations and the
    remembered causes."""
    with _lock:
        _queue.clear()
        if everything:
            _durations.clear()
            _remembered.clear()


# ---------- the item's AI block ----------


def trace_facts(run_id: str | None) -> dict:
    """generated_at, duration_ms and cause of the run that wrote an item's AI output."""
    if not run_id:
        return {}
    from app.agents import trace as traces

    try:
        t = traces.get(run_id)
    except RuntimeError:
        return {}
    if t is None:
        return {}
    end = t.finished_at or t.started_at
    return {"generated_at": end, "duration_ms": int((end - t.started_at).total_seconds() * 1000),
            "cause": t.cause, "mode": t.mode, "cost_usd": t.cost_usd}


def view(kind_name: str, item_id: str, state: str, *, auto: bool = True, run_id: str | None = None,
         generated_at: datetime | None = None, duration_ms: int | None = None, error: str | None = None,
         cause: dict | None = None, with_trace: bool = True) -> dict:
    """The AI block of one item: its record's state (not_requested, queued, done, unavailable, skipped), what this
    process is doing with it now, and when and how fast its output was made."""
    if state not in STATES:
        raise ValueError(state)
    k = KINDS.get(kind_name)
    out: dict = {"kind": kind_name, "item_id": item_id, "state": state, "auto": auto,
                 "typical_s": typical_s(kind_name), "steps": list(k.steps) if k else [],
                 "running_text": k.running_text if k else "AI is working", "error": error}
    if state in ("not_requested", "queued") and (now := live(kind_name, item_id)) is not None:
        out.update(now)
    # a list (polled every few seconds) leaves out when and how fast: that is one read of the run's trace per item
    facts = trace_facts(run_id) if with_trace and state in ("done", "unavailable") else {}
    out["generated_at"] = generated_at or facts.get("generated_at")
    out["duration_ms"] = duration_ms if duration_ms is not None else facts.get("duration_ms")
    out["run_id"] = run_id
    out["cause"] = cause or facts.get("cause")
    return out


def batch(store, user, kind_name: str, item_ids: list[str]) -> dict:
    """A person asks for the AI of several items (a list page's "Ask AI for these N"). Items they may not see, or
    whose AI has run, are left out; the rest are queued in the order given."""
    k = kind(kind_name)
    wanted = list(dict.fromkeys(item_ids))[:BATCH_MAX]
    allowed = []
    for item_id in wanted:
        try:
            k.check(store, user, item_id)
        except (LookupError, NotAllowed):
            continue
        if k.needs_ai(store, item_id):
            allowed.append(item_id)
    base = causes.from_request("batch") or causes.AiCause(trigger="batch")
    queued = 0
    for n, item_id in enumerate(allowed, 1):
        label, link = _describe(k, store, item_id)
        c = base.model_copy(update={"kind": kind_name, "item_id": item_id, "label": label, "link": link,
                                    "note": f"{n} of {len(allowed)}"})
        queued += request(kind_name, item_id, c)
    each = typical_s(kind_name)
    return {"kind": kind_name, "queued": queued, "skipped": len(wanted) - len(allowed), "calls": queued * k.calls,
            "expected_s": round(each * queued / CONCURRENCY, 1) if queued else 0.0, "typical_s": each}
