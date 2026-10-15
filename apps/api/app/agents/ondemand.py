"""AI on demand: the items that get AI output, their AI state, and the jobs that run it (BACKGROUND_AI).

With BACKGROUND_AI=on_demand (the demo's default) no model call is made for work nobody asked for. An item's AI
runs when a person opens it or presses Ask AI (in the request: `run_now`), and at once for new work their own
action created or for a batch they asked for (`request`, after the request commits: the API's job loop runs it,
app/main.py). The rule checks keep running in the background either way. BACKGROUND_AI=eager keeps the background
writing the AI output of every new item before anyone looks (a "real hospital" mode, and the evals).

Each module registers its kinds of item (`register`; `kind()` imports the module of a known kind not yet loaded,
e.g. in the Temporal worker): order reviews, reconciliations and Control Tower exceptions, requisitions and patient
feedback. A kind knows how to run the AI for one item, who may ask, what the item is called, and whether a failed
run may be asked for again. Its `run(store, item_id, retry)` takes the item's database lock, reads the item again
and calls the model only if the AI has not run (or, with `retry`, if it failed and may run again): a double click,
two people, the job loop and the eager background make one run. `view` gives every item the same AI block:

    state      not_requested | queued | running | done | unavailable | skipped
    auto       opening the item runs it (False: only Ask AI, e.g. a test order without a rule flag)
    retryable  a failed run may be asked for again (Retry)
    typical_s  how long it usually takes (measured here from runs that got an answer, else a default for the kind)
    started_at / step / steps while running; generated_at, duration_ms, run_id, cause once done; error

The running and queued registry is this process's (the API runs one worker, docs/DEPLOY.md); the item's own record
says whether its AI has run. Who caused each run is on its trace (app/agents/cause.py).

Known limit: a run calls the model inside its unit of work, holding a pooled connection and the item's lock (as
main's GET-time narration did); about twenty runs stalled at once on a slow model would use up the pool (10 + 10).
"""

from __future__ import annotations

import importlib
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
RETRY_UNSEEN = 20  # a job for an item that has not appeared yet waits up to this many half-seconds
WAIT_MARGIN_S = 30.0  # a second caller's wait beyond the model calls' budgets (the run's reads, writes and lock)


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
    # (store, item_id, retry): under the item's lock, read it again and run the AI if it has not run (with `retry`:
    # also if it failed and `retryable`); LookupError when the item does not exist
    run: Callable[[Any, str, bool], Any]
    needs_ai: Callable[[Any, str], bool]  # (store, item_id): its AI has not run (a batch skips the rest)
    check: Callable[[Any, Any, str], None]  # (store, user or None, item_id): LookupError / NotAllowed
    describe: Callable[[Any, str], tuple[str, str | None]]  # (store, item_id) -> (label, link)
    steps: tuple[str, ...] = ()  # the stages a run goes through, shown while it runs
    retryable: Callable[[Any, str], bool] | None = None  # (store, item_id): its failed run may be asked again


KINDS: dict[str, Kind] = {}
# Where each kind registers itself: a process that never imported the module (the standalone Temporal worker) still
# finds the kind
KIND_MODULES = {"order_review": "app.modules.med_safety.service", "medrec": "app.modules.med_safety.service",
                "exception": "app.modules.control_tower.service",
                "requisition": "app.modules.requisitions.service", "feedback": "app.modules.feedback.service"}


def register(kind: Kind) -> Kind:
    KINDS[kind.name] = kind
    return kind


def kind(name: str) -> Kind:
    if name not in KINDS and name in KIND_MODULES:
        importlib.import_module(KIND_MODULES[name])
    if name not in KINDS:
        raise LookupError(f"No AI item kind {name!r}")
    return KINDS[name]


def on_demand() -> bool:
    return config.settings.background_ai == "on_demand"


def can_retry(store, kind_name: str, item_id: str) -> bool:
    k = kind(kind_name)
    return k.retryable is not None and k.retryable(store, item_id)


# ---------- what is running and queued in this process ----------


@dataclass
class Job:
    kind: str
    item_id: str
    cause: causes.AiCause | None
    retry: bool = False  # asked again after a failed run (a storyline step's Ask AI)
    queued_at: datetime = field(default_factory=datetime.now)
    attempts: int = 0
    not_before: float = 0.0


_lock = threading.Lock()
_running: dict[tuple[str, str], dict] = {}
_queue: deque[Job] = deque()
# Jobs the loop has taken but whose run has not taken the item's slot yet (its transaction is starting): still
# queued for everyone else, so a person opening the item meanwhile runs it as what it was queued for
_taken: dict[tuple[str, str], Job] = {}
_durations: dict[tuple[str, str], deque] = {}  # (model mode, kind) -> recent run seconds


def _key(kind_name: str, item_id: str) -> tuple[str, str]:
    return (kind_name, item_id)


def live(kind_name: str, item_id: str) -> dict | None:
    """{state: running|queued, ...} while this process runs or holds a job for the item."""
    key = _key(kind_name, item_id)
    with _lock:
        if (r := _running.get(key)) is not None:
            return {"state": "running", "started_at": r["started_at"], "step": r.get("step"),
                    "cause": causes.public(r["cause"])}
        if (job := _taken.get(key)) is not None:
            return {"state": "queued", "queued_at": job.queued_at, "ahead": 0}
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
    """Everything running or queued, for the activity panel and the list pages' batch progress. Each entry keeps its
    cause with the session (`session`, for filtering by browser); the API shows `public` causes only."""
    with _lock:
        running = [{"kind": k, "item_id": i, "started_at": r["started_at"], "step": r.get("step"),
                    "label": r.get("label"), "link": r.get("link"),
                    "cause": r["cause"].model_dump(exclude_none=True) if r["cause"] else None}
                   for (k, i), r in _running.items()]
        queued = [{"kind": j.kind, "item_id": j.item_id, "queued_at": j.queued_at,
                   "cause": j.cause.model_dump(exclude_none=True) if j.cause else None}
                  for j in [*_taken.values(), *_queue]]
    return {"running": running, "queued": queued}


def _record(kind_name: str, seconds: float) -> None:
    from app.llm.gateway import get_gateway

    with _lock:
        _durations.setdefault((get_gateway().mode, kind_name), deque(maxlen=12)).append(seconds)


def typical_s(kind_name: str) -> float:
    """The median of the recent runs of this kind on this model that got an answer, else the kind's default (mock:
    about 0.6 s a call plus the tools; live: measured on Gemini 3.5 Flash)."""
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


def wait_s(k: Kind) -> float:
    """How long a second caller waits for the run under way: each of the kind's model calls may make two attempts
    within its agent's budget (model_policy.timeout_s, else LLM_TIMEOUT_S), plus a margin. A first run that never
    returns then holds nobody (nor their pooled connection) for good."""
    from app.agents import registry

    default = config.settings.llm_timeout_s
    budgets = []
    for agent_id in k.agents:
        try:
            spec = registry.agent(agent_id)
        except Exception:  # an unreadable registry: the default budget
            spec = None
        policy = spec.model_policy if spec is not None else None
        budgets.append(policy.timeout_s if policy is not None and policy.timeout_s else default)
    return k.calls * 2 * max(budgets, default=default) + WAIT_MARGIN_S


def run_now(store, kind_name: str, item_id: str, *, trigger: causes.Trigger = "opened",
            cause: causes.AiCause | None = None, note: str | None = None, retry: bool = False) -> None:
    """Run one item's AI in this thread (a person's request, or a job). One run per item at a time in this process:
    a second caller waits for the first and then reads the item again under its lock (which the first holds until
    its transaction commits), so it sees the result and makes no call. It waits at most `wait_s`; a run still going
    then is left to finish, and the caller shows the item as it is (running). `retry` (Ask AI after a failure)
    belongs to whoever wins the slot, and the item's own run decides, under its lock, whether the failure may run
    again: a caller that waited for a retry reads its result; one that waited for a run that was not a retry (an
    opening, a job) keeps its retry and runs it next. An item already queued (new work, a batch, a storyline step;
    also one the job loop has taken but not started) runs now, as what it was queued for."""
    k = kind(kind_name)
    key = _key(kind_name, item_id)
    if cause is None and (queued := _take_queued(key)) is not None:
        cause, retry = queued.cause or causes.AiCause(trigger="background"), retry or queued.retry
    label, link = _describe(k, store, item_id)  # a database read: not under the registry's lock
    limit = deadline = None
    while True:
        with _lock:
            running = _running.get(key)
            if running is None:
                event = threading.Event()
                _running[key] = entry = {"started_at": datetime.now(), "event": event, "retry": retry,
                                         "cause": cause or causes.current(), "label": label, "link": link}
                _taken.pop(key, None)  # a job the loop took for it is this run now (or reads its result)
                break
        if deadline is None:  # only a caller that waits needs its bound (it reads the agents' budgets)
            limit = wait_s(k)
            deadline = time.monotonic() + limit
        if not running["event"].wait(timeout=max(0.0, deadline - time.monotonic())):
            log.warning("AI %s %s: still running after %.0f s; this caller shows it as it is", kind_name, item_id,
                        limit)
            return
        if running["retry"]:
            retry = False  # the run under way was the retry: this caller reads its result
    started = time.monotonic()
    try:
        with causes.using(cause) if cause is not None else _nothing():
            with causes.about(kind_name, item_id, trigger=None if cause is not None else trigger, label=label,
                              link=link, note=note) as c, causes.collecting() as calls:
                entry["cause"] = c
                k.run(store, item_id, retry)
        if any(outcome in ("ok", "retried_ok") for outcome in calls):  # not a refused or failed run's few ms
            _record(kind_name, time.monotonic() - started)
    finally:
        with _lock:
            _running.pop(key, None)
        event.set()


def _take_queued(key: tuple[str, str]) -> Job | None:
    """The job queued for the item: off the queue, or one the loop has taken but not started (the loop's run then
    finds the item done)."""
    with _lock:
        for job in list(_queue):
            if (job.kind, job.item_id) == key:
                _queue.remove(job)
                return job
        return _taken.get(key)


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


def request(kind_name: str, item_id: str, cause: causes.AiCause | None = None, *, retry: bool = False) -> bool:
    """Queue one item for the job loop. False when it is already queued or running. Work a transaction creates or
    changes is queued with `request_after_commit`, so the job never reads the item as it was before."""
    kind(kind_name)
    key = _key(kind_name, item_id)
    with _lock:
        if key in _running or key in _taken or any((j.kind, j.item_id) == key for j in _queue):
            return False
        _queue.append(Job(kind_name, item_id, cause or causes.current(), retry=retry))
    return True


def request_after_commit(store, kind_name: str, item_id: str, cause: causes.AiCause | None = None, *,
                         retry: bool = False) -> None:
    """Queue the item once this unit of work commits (new work a person's action created, a storyline step)."""
    cause = cause or causes.current()
    kind(kind_name)
    store.after_commit(lambda: request(kind_name, item_id, cause, retry=retry))


def next_job() -> Job | None:
    """The first job whose item can be run now (the job loop). Until its run takes the item's slot it still counts
    as queued (`_taken`); `release` it when done with it."""
    now = time.monotonic()
    with _lock:
        for job in list(_queue):
            if job.not_before <= now:
                _queue.remove(job)
                _taken[_key(job.kind, job.item_id)] = job
                return job
    return None


def release(job: Job) -> None:
    """The job loop is done with a job it took (run, skipped, put back, or failed before its run)."""
    key = _key(job.kind, job.item_id)
    with _lock:
        if _taken.get(key) is job:
            del _taken[key]


def requeue(job: Job) -> None:
    """Put a job back (the data was being rewritten when its turn came)."""
    job.not_before = time.monotonic() + 1.0
    release(job)
    with _lock:
        _queue.append(job)


def run_job(store, job: Job) -> None:
    """Run a queued job in its own unit of work: the kind's checks first (an exception that cleared since is
    skipped), then the run. An item not found yet is tried again a little later."""
    k = kind(job.kind)
    try:
        k.check(store, None, job.item_id)
        run_now(store, job.kind, job.item_id, cause=job.cause, retry=job.retry)
    except LookupError:
        if job.attempts >= RETRY_UNSEEN:
            log.warning("AI job %s %s: the item never appeared", job.kind, job.item_id)
            return
        job.attempts += 1
        job.not_before = time.monotonic() + 0.5
        release(job)
        with _lock:
            _queue.append(job)
    except NotAllowed as e:  # e.g. the exception cleared before its turn
        log.info("AI job %s %s skipped: %s", job.kind, job.item_id, e)
    finally:
        release(job)


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
        _taken.clear()
        if everything:
            _durations.clear()
            _remembered.clear()


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
         cause: dict | None = None, with_trace: bool = True, retryable: bool = False) -> dict:
    """The AI block of one item: its record's state (not_requested, queued, done, unavailable, skipped), what this
    process is doing with it now, and when and how fast its output was made."""
    if state not in STATES:
        raise ValueError(state)
    k = KINDS.get(kind_name)
    out: dict = {"kind": kind_name, "item_id": item_id, "state": state, "auto": auto,
                 "retryable": retryable and state == "unavailable",
                 "typical_s": typical_s(kind_name), "steps": list(k.steps) if k else [],
                 "running_text": k.running_text if k else "AI is working", "error": error}
    if state in ("not_requested", "queued", "unavailable") and (now := live(kind_name, item_id)) is not None:
        out.update(now)
    # a list (polled every few seconds) leaves out when and how fast: that is one read of the run's trace per item
    facts = trace_facts(run_id) if with_trace and state in ("done", "unavailable") else {}
    out["generated_at"] = generated_at or facts.get("generated_at")
    out["duration_ms"] = duration_ms if duration_ms is not None else facts.get("duration_ms")
    out["run_id"] = run_id
    out["cause"] = causes.public(cause or facts.get("cause"))
    return out


def live_state(kind_name: str, item_id: str, stored: str) -> str:
    """A list row's state: what this process is doing with the item, else its record's."""
    now = live(kind_name, item_id) if stored in ("not_requested", "queued", "unavailable") else None
    return now["state"] if now is not None else stored


def batch(store, user, kind_name: str, item_ids: list[str]) -> dict:
    """A person asks for the AI of several items (a list page's "Ask AI for these N"). Items they may not see are
    left out and listed in `denied` (the caller audits them); items whose AI has run are skipped; the rest are queued
    in the order given."""
    k = kind(kind_name)
    wanted = list(dict.fromkeys(item_ids))[:BATCH_MAX]
    allowed, denied = [], []
    for item_id in wanted:
        try:
            k.check(store, user, item_id)
        except NotAllowed:
            denied.append(item_id)
            continue
        except LookupError:
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
    return {"kind": kind_name, "queued": queued, "skipped": len(wanted) - len(allowed), "denied": denied,
            "calls": queued * k.calls, "expected_s": round(each * queued / CONCURRENCY, 1) if queued else 0.0,
            "typical_s": each}
