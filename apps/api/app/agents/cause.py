"""Why an AI run happened: who caused it, by doing what, for which item ("What the AI did in this demo").

Every agent trace (app/agents/trace.py) carries the cause of its run. A run started while a person's request is
being served is caused by that person (the browser's demo session from `X-Demo-Session`, the signed-in user), also
when the agent then writes as itself (`acting_as_system`). The code that runs an item's AI says more with
`about(...)`: the trigger (they opened the item, pressed Ask AI, asked for a batch, pressed a storyline step, or
their action created new work) and the item. Work a person caused but the background carries out (a requisition
the portal received, a batch) runs `using(cause)` with the cause recorded when they asked. A run with no cause is
the background's own (BACKGROUND_AI=eager, the simulator's evals).

The cause holds ids and labels the person already saw, never patient details: the activity panel shows it to
whoever uses the same browser, whatever role they switch to. The session id is how a browser finds its own actions,
so nothing shows it back (`public`).
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from contextvars import ContextVar
from typing import Literal

from pydantic import BaseModel

from app.core.context import request_origin

Trigger = Literal["opened", "asked", "action", "batch", "storyline", "background"]

TRIGGER_TEXT = {
    "opened": "opened the item",
    "asked": "pressed Ask AI",
    "action": "their action",
    "batch": "asked for a batch",
    "storyline": "a storyline step",
    "background": "background (eager mode)",
}


class AiCause(BaseModel):
    trigger: Trigger = "action"
    session: str | None = None  # the browser's demo session
    user_id: str | None = None
    user_name: str | None = None
    role: str | None = None
    kind: str | None = None  # the item's kind (app/agents/ondemand.py), e.g. order_review
    item_id: str | None = None
    label: str | None = None  # what the panel calls the item, e.g. "Naproxen 500 mg (ORV-00012)"
    link: str | None = None  # where the item opens in the web app
    action_id: str | None = None  # groups the runs of one AI action (a requisition's three calls)
    note: str | None = None  # e.g. "Storyline step 5", "1 of 12"


_cause: ContextVar[AiCause | None] = ContextVar("ai_cause", default=None)


def from_request(trigger: Trigger = "action") -> AiCause | None:
    """The cause a person's request gives (None outside a request)."""
    origin = request_origin()
    if origin is None:
        return None
    user = origin.user
    return AiCause(trigger=trigger, session=origin.session, user_id=user.id if user else None,
                   user_name=user.name if user else None, role=str(user.role) if user else None)


def current() -> AiCause | None:
    """The cause of a run starting now: the one set with `about`/`using`, else the request's."""
    return _cause.get() or from_request()


@contextlib.contextmanager
def using(cause: AiCause | None) -> Iterator[AiCause | None]:
    """Run with this cause (a job the background runs for a person)."""
    token = _cause.set(cause)
    try:
        yield cause
    finally:
        _cause.reset(token)


@contextlib.contextmanager
def about(kind: str, item_id: str, *, trigger: Trigger | None = None, label: str | None = None,
          link: str | None = None, note: str | None = None) -> Iterator[AiCause | None]:
    """The runs inside are one AI action on this item; the person (or the cause already set) caused it."""
    base = _cause.get() or from_request(trigger or "action")
    if base is None:  # the background's own run: still name the item
        base, trigger = AiCause(trigger="background"), None
    cause = base.model_copy(update={k: v for k, v in {
        "trigger": trigger, "kind": kind, "item_id": item_id, "label": label, "link": link, "note": note,
        "action_id": uuid.uuid4().hex[:12]}.items() if v is not None})
    with using(cause):
        yield cause


def stamp() -> dict | None:
    """What a new trace records (None: the background's own run)."""
    cause = current()
    return cause.model_dump(exclude_none=True) if cause is not None else None


def public(cause: AiCause | dict | None) -> dict | None:
    """A cause as the web app may show it: without the browser's session id."""
    if cause is None:
        return None
    data = cause.model_dump(exclude_none=True) if isinstance(cause, AiCause) else dict(cause)
    data.pop("session", None)
    return data


# The model calls made while a run is under way (their outcomes), so its time is learned only from real answers
_calls: ContextVar[list | None] = ContextVar("ai_calls", default=None)


@contextlib.contextmanager
def collecting() -> Iterator[list]:
    seen: list = []
    token = _calls.set(seen)
    try:
        yield seen
    finally:
        _calls.reset(token)


def note_call(outcome: str) -> None:
    """The LLM gateway reports each logged call (ok, retried_ok, refused, unavailable, needs_human)."""
    seen = _calls.get()
    if seen is not None:
        seen.append(outcome)
