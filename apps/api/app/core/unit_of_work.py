"""One database transaction per HTTP request.

The middleware opens a Store for the request and makes it current for
get_store(). When the response starts, it commits (status below 500) or rolls
back (server error) before any byte goes out, so a failed commit becomes a 500
instead of a success the client cannot trust. Audit events are written
separately (app/core/audit.py) and stay on record either way.

In tests a single ambient store is shared by the test body and every request;
the middleware saves it (flush, change counter) after each request and drops loaded objects, so the
next read comes from the database as it would in a new request.
"""

import logging

import anyio
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.store import EngineSource, Store, ambient_store, get_engine, use_store

log = logging.getLogger(__name__)


class UnitOfWorkMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        shared = ambient_store()
        store = shared or Store(EngineSource(get_engine()))
        finished = failed = False

        def finish(status: int) -> None:
            if shared is not None:
                store.save()
                store.expunge()
            elif status < 500:
                store.commit()
            else:
                store.rollback()

        async def send_wrapper(message: Message) -> None:
            nonlocal finished, failed
            if failed:
                return  # the error response has been sent; drop the original body
            if message["type"] == "http.response.start" and not finished:
                finished = True
                try:
                    await anyio.to_thread.run_sync(finish, message["status"])
                except Exception:
                    log.exception("commit failed")
                    failed = True
                    await send({"type": "http.response.start", "status": 500,
                                "headers": [(b"content-type", b"application/json")]})
                    await send({"type": "http.response.body", "body": b'{"detail":"Could not save the change"}'})
                    return
            await send(message)

        with use_store(store):
            try:
                await self.app(scope, receive, send_wrapper)
            finally:
                if not finished:
                    if shared is not None:
                        store.expunge()
                    await anyio.to_thread.run_sync(store.close)
