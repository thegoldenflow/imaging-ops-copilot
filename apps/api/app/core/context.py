"""Who is acting in the current request, for code that has no request at hand.

`RequestContextMiddleware` gives each HTTP request a `RequestContext`; the
`current_user` dependency fills in the signed-in user. Deeper layers, such as
the LLM gateway writing an `ai_call` audit event, read it with `request_context()`.
Outside a request (background steps, scripts) there is none and they act as the system.

The context object is shared by reference: FastAPI runs dependencies and the
endpoint in separate worker threads with copies of the context variables, so a
value set by a dependency would not reach the endpoint, but a change to the shared
object does.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING

from starlette.types import ASGIApp, Receive, Scope, Send

if TYPE_CHECKING:
    from app.core.models import StaffUser


SESSION_HEADER = "x-demo-session"  # the browser's demo session (apps/web/src/lib/api.ts), for "What the AI did"


@dataclass
class RequestContext:
    user: StaffUser | None = None
    source_ip: str | None = None
    session: str | None = None  # the browser's demo session id (X-Demo-Session), not an identity


_context: ContextVar[RequestContext | None] = ContextVar("request_context", default=None)
# The request's context even while it acts as the system: an agent that writes as itself still ran because this
# person opened something (app/agents/cause.py records that on the run's trace).
_origin: ContextVar[RequestContext | None] = ContextVar("request_origin", default=None)


def request_context() -> RequestContext | None:
    return _context.get()


def request_origin() -> RequestContext | None:
    """The HTTP request this code runs for, also inside `acting_as_system()`; None outside a request."""
    return _origin.get()


@contextmanager
def acting_as_system() -> Iterator[None]:
    """Inside a request, act as the system: for work that belongs to no user even when a request happens to
    start it (e.g. the Control Tower's agent writing a narrative on its own authority)."""
    token = _context.set(None)
    try:
        yield
    finally:
        _context.reset(token)


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        client = scope.get("client")
        session = next((v.decode("latin-1")[:64] for k, v in scope.get("headers") or [] if k == SESSION_HEADER.encode()),
                       None)
        ctx = RequestContext(source_ip=client[0] if client else None, session=session or None)
        token, origin = _context.set(ctx), _origin.set(ctx)
        try:
            await self.app(scope, receive, send)
        finally:
            _context.reset(token)
            _origin.reset(origin)
