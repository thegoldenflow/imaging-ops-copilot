"""Idle pause: whether a person has used the app recently (IDLE_PAUSE_MINUTES).

With a real model key, the background loops (app/main.py) spend money with nobody watching: the day simulator
writes orders and admissions, and order review, medication reconciliation and the Control Tower narrator call the
model for each (about $4 an hour on Gemini, 2026-10-12). So the background work that leads to model calls pauses
once nobody has used the app for IDLE_PAUSE_MINUTES, and resumes on the next request of a signed-in person.

Activity is an authenticated request (`current_user`, app/core/auth.py). The web app sends `X-Idle-Seconds`, the
time since the person last touched the page (pointer, keys, scrolling, the tab coming back into view), so a tab
left open on a polling page does not keep the server awake: the request counts as activity at the moment of that
last input. A request without the header (an API client, a script) counts as activity now.

The state is per process (the API runs one worker: docs/DEPLOY.md). A process starts idle: after a restart nothing
runs up model calls until someone signs in. Times are wall-clock datetimes; tests pass `now`.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta

from app.core import config

log = logging.getLogger("app.activity")

IDLE_HEADER = "X-Idle-Seconds"

_lock = threading.Lock()
_state: dict = {"last_seen": None, "awake_since": None, "paused_logged": False, "last_pause": None}


def window() -> timedelta | None:
    """How long without activity before the pause; None: never pause."""
    minutes = config.settings.idle_pause_minutes
    return timedelta(minutes=minutes) if minutes > 0 else None


def _idle(now: datetime) -> bool:
    span = window()
    if span is None:
        return False
    last = _state["last_seen"]
    return last is None or now - last > span


def touch(now: datetime | None = None, *, idle_seconds: float | None = None) -> None:
    """A person used the app (`idle_seconds` ago, per the web app's header). Wakes the background work when it
    was paused, and only ever moves the last activity forward."""
    now = now or datetime.now()
    seen = now - timedelta(seconds=max(0.0, idle_seconds)) if idle_seconds is not None else now
    with _lock:
        last = _state["last_seen"]
        if last is not None and seen <= last:
            return
        was_idle = _idle(now)
        _state["last_seen"] = seen
        if was_idle and not _idle(now):
            paused_at = last + window() if last is not None else None
            _state["awake_since"] = now
            _state["paused_logged"] = False
            _state["last_pause"] = {"from": paused_at, "to": now}
            log.info("background work resumed (idle since %s)", paused_at.isoformat(timespec="seconds")
                     if paused_at else "start-up")


def touch_from_header(value: str | None, now: datetime | None = None) -> None:
    try:
        idle_seconds = float(value) if value else None
    except ValueError:
        idle_seconds = None
    touch(now, idle_seconds=idle_seconds)


def idle(now: datetime | None = None) -> bool:
    """True while the background work that leads to model calls is paused. Logs the moment it pauses."""
    now = now or datetime.now()
    with _lock:
        paused = _idle(now)
        if paused and not _state["paused_logged"]:
            _state["paused_logged"] = True
            if _state["last_seen"] is None:
                log.info("background work that calls the model waits for the first signed-in request")
            else:
                log.info("background work that calls the model paused: no activity for %g minutes",
                         config.settings.idle_pause_minutes)
        return paused


def awake_since() -> datetime | None:
    """When the background work last resumed (the simulator clock does not count the idle time before it)."""
    with _lock:
        return _state["awake_since"]


def status(now: datetime | None = None) -> dict:
    """For the clock display, AI Ops and /api/health."""
    now = now or datetime.now()
    with _lock:
        span, last = window(), _state["last_seen"]
        paused = _idle(now)
        return {
            "pause_minutes": config.settings.idle_pause_minutes,
            "paused": paused,
            "paused_since": (last + span if last is not None and span is not None else None) if paused else None,
            "last_activity": last,
            "last_pause": _state["last_pause"],
        }


def reset() -> None:
    """Tests: back to a freshly started process."""
    with _lock:
        _state.update(last_seen=None, awake_since=None, paused_logged=False, last_pause=None)
