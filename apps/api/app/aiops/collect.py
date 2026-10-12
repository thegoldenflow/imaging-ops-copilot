"""What an eval run's model calls and agent runs were, case by case (used by app/aiops/evalrun.py).

The runner installs a `CollectingGateway` (the normal LLM gateway, mock or live) for the duration of a suite. Every
model call it logs and every trace it joins or writes is recorded with the case in progress. Harnesses mark the
case they start with `mark(case_id)` (a no-op outside the runner), or the runner's adapters do it for them, so the
existing per-module harnesses need no other change.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field

from app.llm.gateway import LlmGateway

_case: ContextVar[str | None] = ContextVar("eval_case", default=None)
_active: list["Collector"] = []


def mark(case_id: str | None) -> None:
    """The case a harness starts now (calls until the next mark belong to it)."""
    if _active:
        _case.set(case_id)
        if case_id is not None and _active[-1].on_case is not None:
            _active[-1].on_case(case_id)


def current() -> str | None:
    return _case.get()


@dataclass
class CallRecord:
    agent_id: str
    case_id: str | None
    model: str
    mode: str
    outcome: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    latency_ms: int
    error: str | None


@dataclass
class Collector:
    calls: list[CallRecord] = field(default_factory=list)
    runs: dict[str, str | None] = field(default_factory=dict)  # run id -> case id
    on_case: object = None  # called with each case id as it starts (a workflow activity's heartbeat)

    def __enter__(self) -> "Collector":
        _active.append(self)
        self._token = _case.set(None)
        return self

    def __exit__(self, *exc) -> None:
        _case.reset(self._token)
        _active.remove(self)


def active() -> Collector | None:
    return _active[-1] if _active else None


class CollectingGateway(LlmGateway):
    """The LLM gateway with the eval run's bookkeeping (the calls go out exactly as in the app)."""

    def _log(self, **kw):
        call = super()._log(**kw)
        if (c := active()) is not None:
            c.calls.append(CallRecord(agent_id=call.task, case_id=current(), model=call.model, mode=call.mode,
                                      outcome=call.outcome, tokens_in=call.input_tokens,
                                      tokens_out=call.output_tokens, cost_usd=call.cost_usd,
                                      latency_ms=call.latency_ms, error=call.error))
        return call

    def _trace(self, spec, call, input_refs):
        run_id = super()._trace(spec, call, input_refs)
        if run_id and (c := active()) is not None:
            c.runs.setdefault(run_id, current())
        return run_id
