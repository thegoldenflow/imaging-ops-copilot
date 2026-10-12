"""The contract every adapter to a non-FHIR external system follows (spec 6.2).

HL7 v2 interface engine, PACS, telephony, SMS, email, fax, OHIP, insurers and
outside image archives are mocks in the demo (app/integrations/mocks.py); the
real ones are interfaces only (app/integrations/interfaces.py). All of them go
through `Adapter.call`, which provides:

- a correlation id on every message, outbound and inbound (`receive`), written to
  the log with the adapter, operation, attempt and outcome (never the payload);
- a timeout per attempt and retries with exponential backoff for retryable errors
  (`Policy`); a rejected message (the other side says it is invalid) is not retried;
- a circuit breaker per adapter: after `breaker_failures` failed calls in a row it
  opens and calls fail at once without reaching the system; after `breaker_reset_s`
  one trial call is let through (half open) and its outcome closes or reopens it;
- a dead-letter table: a message that still fails is parked in `dead_letters`
  (payload encrypted, it may hold PHI) for a person to look at. Callers that keep
  their own durable retry (prior-image retrieval, critical-result calls) make one
  attempt per call, park nothing, and call `dead_letter()` when they give up.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import BaseModel

log = logging.getLogger("app.integrations")


@dataclass(frozen=True)
class Policy:
    timeout_s: float = 5.0  # per attempt
    attempts: int = 3  # tries in total
    backoff_s: float = 0.2  # wait before the next try: backoff_s, 4 x backoff_s, ...
    breaker_failures: int = 5  # failed calls in a row that open the circuit
    breaker_reset_s: float = 30.0  # an open circuit lets a trial call through after this

    def backoff(self, attempt: int) -> float:
        return self.backoff_s * 4 ** (attempt - 1)


class AdapterError(Exception):
    """The external system failed (unavailable, error response); worth another try."""

    retryable = True


class AdapterTimeout(AdapterError):
    pass


class AdapterRejected(AdapterError):
    """The external system refused the message itself; retrying would not help."""

    retryable = False


class CircuitBreaker:
    def __init__(self, failures: int, reset_s: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.failures_to_open, self.reset_s, self._clock = failures, reset_s, clock
        self.failures = 0
        self.opened_at: float | None = None

    @property
    def state(self) -> str:
        if self.opened_at is None:
            return "closed"
        return "half_open" if self._clock() - self.opened_at >= self.reset_s else "open"

    def allow(self) -> bool:
        return self.state != "open"

    def success(self) -> None:
        self.failures, self.opened_at = 0, None

    def failure(self) -> None:
        self.failures += 1
        if self.state == "half_open" or self.failures >= self.failures_to_open:
            self.opened_at = self._clock()

    def reset(self) -> None:
        self.success()


class DeadLetter(BaseModel):
    id: str
    ts: datetime
    adapter: str
    operation: str
    correlation_id: str
    payload: dict[str, Any]
    error: str
    attempts: int
    status: str = "parked"  # parked, redriven, discarded


@dataclass
class AdapterResult:
    ok: bool
    adapter: str
    operation: str
    correlation_id: str
    attempts: int
    response: dict[str, Any] | None = None
    error: str | None = None
    dead_letter_id: str | None = None


def new_correlation_id() -> str:
    return uuid.uuid4().hex


def dead_letters(store) -> dict[str, DeadLetter]:
    return store.module("dead_letters", dict)


@dataclass
class Adapter:
    """Base class: subclasses implement `_send` (one attempt, honouring `timeout_s`)."""

    name: str
    policy: Policy = field(default_factory=Policy)
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep

    def __post_init__(self) -> None:
        self.breaker = CircuitBreaker(self.policy.breaker_failures, self.policy.breaker_reset_s, self.clock)

    def _send(self, operation: str, payload: dict, *, correlation_id: str, timeout_s: float) -> dict:
        raise NotImplementedError

    def _log(self, direction: str, operation: str, correlation_id: str, attempt: int, outcome: str) -> None:
        log.info("adapter=%s direction=%s operation=%s correlation_id=%s attempt=%d outcome=%s",
                 self.name, direction, operation, correlation_id, attempt, outcome)

    def call(self, operation: str, payload: dict, *, correlation_id: str | None = None,
             attempts: int | None = None, dead_letter: bool = True) -> AdapterResult:
        """Send one message under the contract. Never raises for a failure of the other system:
        the result says whether it got through (and where it was parked if not)."""
        correlation_id = correlation_id or new_correlation_id()
        tries = attempts or self.policy.attempts
        error, attempt = "not sent", 0
        while attempt < tries:
            if not self.breaker.allow():
                error = "circuit open: calls are paused after repeated failures"
                self._log("out", operation, correlation_id, attempt, "circuit_open")
                break
            attempt += 1
            try:
                response = self._send(operation, payload, correlation_id=correlation_id, timeout_s=self.policy.timeout_s)
            except AdapterError as e:
                self.breaker.failure()
                error = str(e) or type(e).__name__
                self._log("out", operation, correlation_id, attempt, type(e).__name__)
                if not e.retryable:
                    break
                if attempt < tries:
                    self.sleep(self.policy.backoff(attempt))
                continue
            self.breaker.success()
            self._log("out", operation, correlation_id, attempt, "ok")
            return AdapterResult(True, self.name, operation, correlation_id, attempt, response=response)
        letter = self.dead_letter(operation, payload, correlation_id, error, attempt) if dead_letter else None
        return AdapterResult(False, self.name, operation, correlation_id, attempt, error=error,
                             dead_letter_id=letter.id if letter else None)

    def dead_letter(self, operation: str, payload: dict, correlation_id: str, error: str, attempts: int) -> DeadLetter | None:
        """Park a message that could not be delivered (in the current unit of work)."""
        from app.core.store import get_store

        try:
            store = get_store()
        except RuntimeError:  # no unit of work (a script): log only
            self._log("out", operation, correlation_id, attempts, "dead_letter_not_stored")
            return None
        letter = DeadLetter(id=store.next_id("DLQ"), ts=datetime.now(), adapter=self.name, operation=operation,
                            correlation_id=correlation_id, payload=payload, error=error[:500], attempts=attempts)
        dead_letters(store)[letter.id] = letter
        self._log("out", operation, correlation_id, attempts, "dead_lettered")
        return letter

    def receive(self, operation: str, correlation_id: str | None = None) -> str:
        """Log an inbound message (e.g. a patient's SMS reply) under its correlation id."""
        correlation_id = correlation_id or new_correlation_id()
        self._log("in", operation, correlation_id, 0, "received")
        return correlation_id

    def health(self) -> dict:
        return {"adapter": self.name, "circuit": self.breaker.state, "failures_in_a_row": self.breaker.failures,
                "policy": {"timeout_s": self.policy.timeout_s, "attempts": self.policy.attempts,
                           "backoff_s": self.policy.backoff_s, "breaker_failures": self.policy.breaker_failures,
                           "breaker_reset_s": self.policy.breaker_reset_s}}
