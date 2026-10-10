"""Failures injected into workflow steps for the demo and the tests (spec 6.5: "a deliberate failure").

`inject("medRecAdmission", 2)` makes the next two attempts of that step's activity fail after their side effects
committed (the hardest case for idempotency: the MedRec draft exists, the activity still reports a failure, and
Temporal retries it). The counter lives in the database (blob `workflow_faults`), so the API sets it and the
worker process consumes it; each consumption commits on its own.
"""

from __future__ import annotations

from app.core.store import Store, get_store, unit_of_work

BLOB = "workflow_faults"
MAX_TIMES = 3


class InjectedFailure(RuntimeError):
    """A failure put there on purpose (retryable)."""


def _faults(store: Store) -> dict:
    return store.module(BLOB, dict)


def inject(step: str, times: int, store: Store | None = None) -> dict:
    if not 1 <= times <= MAX_TIMES:
        raise ValueError(f"Inject 1 to {MAX_TIMES} failures")
    faults = _faults(store or get_store())
    faults[step] = times
    return dict(faults)


def clear(store: Store | None = None) -> None:
    _faults(store or get_store()).clear()


def pending(store: Store | None = None) -> dict:
    return dict(_faults(store or get_store()))


def maybe_fail(step: str) -> None:
    """Called after an activity's work committed: fail once if a failure is pending for the step."""
    with unit_of_work() as store:
        faults = _faults(store)
        left = int(faults.get(step) or 0)
        if left <= 0:
            return
        if left > 1:
            faults[step] = left - 1
        else:
            faults.pop(step, None)
    raise InjectedFailure(f"Injected failure in {step} (demo); {left - 1} more to come")
