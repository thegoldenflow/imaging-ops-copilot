"""Daily AI spend cap (LLM_DAILY_BUDGET_USD; spec: a public deployment has an AI call limit).

The gateway (app/llm/gateway.py) asks `refusal()` before each model call and, once the day's spend has reached
the cap, refuses the call: the caller gets the usual "AI temporarily unavailable" outcome and falls back to its
rules or a person, so the non-AI features keep working. The refusal is logged as an LlmCall with outcome
`refused` and an error starting with REFUSAL_PREFIX, and AI Ops shows the day's spend against the cap.

The day's spend is a ledger row in `app_meta` (key `llm_spend:<YYYY-MM-DD>`, the server's local date), not a sum
over `llm_calls`: a demo reset (the button, or DEMO_DAILY_RESET_HOUR) truncates `llm_calls`, which would hand a
visitor a fresh budget, while `app_meta` survives resets. Each priced call adds its cost with one atomic upsert in
a transaction of its own, so the money spent counts even when the caller's transaction rolls back (an eval run)
and every process (the API, the Temporal worker, an eval script) adds to the same row.

The check is cheap: the running total is cached in the process. It is exact after this process's own calls (the
upsert returns the new total) and re-read at most every REFRESH_S seconds for the other processes' calls. Calls
already in flight when the cap is reached still finish, so a day can end a few cents over the cap.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime, timedelta

from sqlalchemy import text

from app.core import config

log = logging.getLogger("app.llm.budget")

KEY_PREFIX = "llm_spend:"
REFUSAL_PREFIX = "Daily AI budget reached"
USER_MESSAGE = "AI is temporarily unavailable: today's AI budget is used up"
REFRESH_S = 30.0
KEEP_DAYS = 31  # ledger rows older than this are pruned

_today = date.today  # tests replace it with a day of their own
_lock = threading.Lock()
_cache: dict = {"day": None, "total": 0.0, "read_at": float("-inf"), "warned": None}


def limit() -> float | None:
    return config.settings.llm_daily_budget_usd


def _key(day: date) -> str:
    return f"{KEY_PREFIX}{day.isoformat()}"


def _engine():
    from app.core.store import get_engine

    return get_engine()


def _read(day: date) -> float | None:
    """The day's ledger row (and, once a day, prune old rows); None when the database cannot be read."""
    try:
        with _engine().begin() as conn:
            value = conn.execute(text("SELECT value FROM app_meta WHERE key = :k"), {"k": _key(day)}).scalar()
            conn.execute(text("DELETE FROM app_meta WHERE key LIKE :p AND key < :oldest"),
                         {"p": f"{KEY_PREFIX}%", "oldest": _key(day - timedelta(days=KEEP_DAYS))})
        return float(value) if value else 0.0
    except Exception:
        log.warning("could not read the AI spend ledger; using this process's total", exc_info=True)
        return None


def spent_today() -> float:
    """Today's model spend in USD, all processes (cached)."""
    day = _today()
    with _lock:
        if _cache["day"] == day and time.monotonic() - _cache["read_at"] < REFRESH_S:
            return _cache["total"]
    stored = _read(day)
    with _lock:
        if _cache["day"] != day:
            _cache.update(day=day, total=0.0)
        if stored is not None:
            _cache["total"] = max(_cache["total"], stored)  # the ledger only grows; ours may be the newer
        _cache["read_at"] = time.monotonic()
        return _cache["total"]


def record(cost_usd: float) -> None:
    """Add one call's cost to today's ledger (its own transaction) and to the cached total."""
    if cost_usd <= 0:
        return
    day = _today()
    total = None
    try:
        with _engine().begin() as conn:
            total = float(conn.execute(text(
                "INSERT INTO app_meta (key, value) VALUES (:k, :c) ON CONFLICT (key) DO UPDATE "
                "SET value = (app_meta.value::float8 + EXCLUDED.value::float8)::text RETURNING value"),
                {"k": _key(day), "c": repr(float(cost_usd))}).scalar())
    except Exception:
        log.warning("could not write the AI spend ledger; counting in this process only", exc_info=True)
    with _lock:
        if _cache["day"] != day:
            _cache.update(day=day, total=0.0, read_at=float("-inf"))
        _cache["total"] = max(_cache["total"], total) if total is not None else _cache["total"] + cost_usd


def refusal() -> str | None:
    """Why a model call may not go ahead now (for the call log), or None."""
    cap = limit()
    if cap is None:
        return None
    spent = spent_today()
    if spent < cap:
        return None
    day = _today()
    with _lock:
        first = _cache["warned"] != day
        _cache["warned"] = day
    if first:
        log.warning("daily AI budget reached: $%.4f of $%.2f spent on %s; model calls are refused until midnight",
                    spent, cap, day.isoformat())
    return f"{REFUSAL_PREFIX}: ${spent:.4f} of ${cap:.2f} spent on {day.isoformat()}"


def status(store=None) -> dict:
    """For AI Ops and /api/health: the cap, today's spend and how many calls it refused today."""
    cap, spent, day = limit(), spent_today(), _today()
    refused = None
    if store is not None and not store.detached:
        refused = store.conn().execute(text(
            "SELECT count(*) FROM llm_calls WHERE outcome = 'refused' AND error LIKE :p AND ts >= :start"),
            {"p": f"{REFUSAL_PREFIX}%", "start": datetime.combine(day, datetime.min.time())}).scalar()
    return {
        "limit_usd": cap, "spent_usd": round(spent, 4),
        "remaining_usd": None if cap is None else round(max(0.0, cap - spent), 4),
        "reached": cap is not None and spent >= cap, "day": day.isoformat(),
        "resets_at": datetime.combine(day + timedelta(days=1), datetime.min.time()), "refused_today": refused,
    }


def reset_cache() -> None:
    """Tests: forget the cached total."""
    with _lock:
        _cache.update(day=None, total=0.0, read_at=float("-inf"), warned=None)
