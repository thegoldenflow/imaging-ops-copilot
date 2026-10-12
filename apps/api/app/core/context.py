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


@dataclass
class RequestContext:
    user: StaffUser | None = None
    source_ip: str | None = None


_context: ContextVar[RequestContext | None] = ContextVar("request_context", default=None)


def request_context() -> RequestContext | None:
    return _context.get()


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
        token = _context.set(RequestContext(source_ip=client[0] if client else None))
        try:
            await self.app(scope, receive, send)
        finally:
            _context.reset(token)
