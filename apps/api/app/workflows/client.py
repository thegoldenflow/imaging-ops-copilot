"""This process's connection to Temporal (spec 6.5), for the API's synchronous code.

The Temporal client is asyncio; the API's background steps and request handlers are not. One client lives on its
own event loop in a daemon thread and the methods below wait for it with a timeout. When Temporal cannot be
reached the link says so (`TemporalUnavailable`) and stays quiet for a few seconds instead of blocking every
call; the outbox (bridge.py) keeps what could not be sent.

`link()` is None without TEMPORAL_ADDRESS: Temporal is off and nothing here runs.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Awaitable, Callable
from typing import Any

from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode

from app.core.config import settings

log = logging.getLogger("app.workflows")
QUIET_S = 10  # after a failed connection, how long calls fail fast
STATUS_TTL_S = 3
RECENT_POLL_S = 30


class TemporalUnavailable(RuntimeError):
    """Temporal could not be reached (the outbox keeps the command for later)."""


class TemporalLink:
    def __init__(self, address: str, namespace: str, task_queue: str) -> None:
        self.address, self.namespace, self.task_queue = address, namespace, task_queue
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: Client | None = None
        self._quiet_until = 0.0
        self._lock = threading.Lock()
        self._status: tuple[float, dict] | None = None

    # ---------- plumbing ----------

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is None:
                loop = asyncio.new_event_loop()
                threading.Thread(target=loop.run_forever, name="temporal-link", daemon=True).start()
                self._loop = loop
            return self._loop

    async def _connected(self) -> Client:
        if self._client is None:
            self._client = await Client.connect(self.address, namespace=self.namespace)
        return self._client

    def call(self, fn: Callable[[Client], Awaitable[Any]], *, timeout: float = 10) -> Any:
        if time.monotonic() < self._quiet_until:
            raise TemporalUnavailable(f"Temporal at {self.address} is unreachable; trying again shortly")

        async def run():
            return await fn(await self._connected())

        future = asyncio.run_coroutine_threadsafe(run(), self._ensure_loop())
        try:
            return future.result(timeout)
        except RPCError as e:
            if e.status in (RPCStatusCode.UNAVAILABLE, RPCStatusCode.DEADLINE_EXCEEDED):
                self._down(e)
                raise TemporalUnavailable(str(e)) from e
            raise
        except (TimeoutError, ConnectionError, OSError) as e:
            future.cancel()
            self._down(e)
            raise TemporalUnavailable(str(e) or type(e).__name__) from e
        except RuntimeError as e:  # Client.connect raises RuntimeError("Failed client connect: ...")
            if "connect" in str(e).lower():
                self._down(e)
                raise TemporalUnavailable(str(e)) from e
            raise

    def _down(self, err: BaseException) -> None:
        self._quiet_until = time.monotonic() + QUIET_S
        self._client = None
        log.warning("Temporal unreachable at %s: %s", self.address, str(err)[:200])

    def submit(self, fn: Callable[[Client], Awaitable[Any]]) -> None:
        """Fire and forget (e.g. terminating old workflows after a demo reset)."""
        async def run():
            try:
                await fn(await self._connected())
            except Exception:  # best effort
                log.exception("Temporal call failed")

        asyncio.run_coroutine_threadsafe(run(), self._ensure_loop())

    # ---------- operations ----------

    def start(self, workflow_type: str, workflow_id: str, arg: dict) -> str:
        """`started`, or `duplicate` when a workflow with this business key is running (idempotent start). A closed
        one does not block a new run: the generator's encounter ids repeat after a demo reset, and the events
        themselves are deduplicated by event id before they get here (the outbox), so a resent message never
        starts a second run."""
        async def fn(client: Client) -> str:
            try:
                await client.start_workflow(workflow_type, arg, id=workflow_id, task_queue=self.task_queue,
                                            id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE)
                return "started"
            except WorkflowAlreadyStartedError:
                return "duplicate"
        return self.call(fn)

    def signal(self, workflow_id: str, name: str, payload: dict) -> str:
        """`sent`, or `not_found` when no such workflow runs (never started, or finished)."""
        async def fn(client: Client) -> str:
            try:
                await client.get_workflow_handle(workflow_id).signal(name, payload)
                return "sent"
            except RPCError as e:
                if e.status == RPCStatusCode.NOT_FOUND:
                    return "not_found"
                raise
        return self.call(fn)

    def describe(self, workflow_id: str) -> dict | None:
        async def fn(client: Client) -> dict | None:
            try:
                d = await client.get_workflow_handle(workflow_id).describe()
            except RPCError as e:
                if e.status == RPCStatusCode.NOT_FOUND:
                    return None
                raise
            pending = [{"activity": p.activity_type.name, "attempt": p.attempt,
                        "last_failure": p.last_failure.message if p.HasField("last_failure") else None}
                       for p in d.raw_description.pending_activities]
            return {"status": d.status.name if d.status else None, "run_id": d.run_id,
                    "history_length": d.raw_description.workflow_execution_info.history_length,
                    "start_time": d.start_time.isoformat() if d.start_time else None,
                    "close_time": d.close_time.isoformat() if d.close_time else None, "pending_activities": pending}
        return self.call(fn)

    def terminate_running(self, reason: str) -> None:
        """Every running workflow of the namespace (a demo reset regenerates the data they refer to)."""
        async def fn(client: Client) -> None:
            async for wf in client.list_workflows("ExecutionStatus='Running'"):
                if wf.status == WorkflowExecutionStatus.RUNNING:
                    try:
                        await client.get_workflow_handle(wf.id, run_id=wf.run_id).terminate(reason=reason)
                    except RPCError:
                        pass
        self.submit(fn)

    def workers(self) -> int:
        """Workers polling the task queue now (seen in the last 30 seconds; Temporal remembers pollers for minutes,
        so a worker that was just killed would otherwise still count)."""
        async def fn(client: Client) -> int:
            from temporalio.api.enums.v1 import TaskQueueType
            from temporalio.api.taskqueue.v1 import TaskQueue
            from temporalio.api.workflowservice.v1 import DescribeTaskQueueRequest

            resp = await client.workflow_service.describe_task_queue(DescribeTaskQueueRequest(
                namespace=self.namespace, task_queue=TaskQueue(name=self.task_queue),
                task_queue_type=TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW))
            cutoff = time.time() - RECENT_POLL_S
            return len({p.identity for p in resp.pollers
                        if not p.HasField("last_access_time") or p.last_access_time.seconds >= cutoff})
        return self.call(fn, timeout=5)

    def status(self) -> dict:
        """For the workflow view's banner (cached a few seconds)."""
        now = time.monotonic()
        if self._status and now - self._status[0] < STATUS_TTL_S:
            return self._status[1]
        out = {"configured": True, "address": self.address, "namespace": self.namespace,
               "task_queue": self.task_queue}
        try:
            out.update(online=True, workers=self.workers(), error=None)
        except Exception as e:  # unreachable, or anything else: say so
            out.update(online=False, workers=0, error=str(e)[:200])
        self._status = (now, out)
        return out


_link: list[TemporalLink | None] = []
_default: TemporalLink | None = None


def link() -> TemporalLink | None:
    """The process's link, or None when TEMPORAL_ADDRESS is unset (tests may install another with `use`)."""
    global _default
    if _link:
        return _link[-1]
    if not settings.temporal_address:
        return None
    if _default is None:
        _default = TemporalLink(settings.temporal_address, settings.temporal_namespace, settings.temporal_task_queue)
    return _default


class use:
    """Tests: `with use(fake): ...` replaces the link (None: Temporal off)."""

    def __init__(self, value) -> None:
        self.value = value

    def __enter__(self):
        _link.append(self.value)
        return self.value

    def __exit__(self, *exc) -> None:
        _link.pop()


def offline_status() -> dict:
    return {"configured": False, "online": False, "workers": 0, "address": None, "namespace": settings.temporal_namespace,
            "task_queue": settings.temporal_task_queue,
            "error": "TEMPORAL_ADDRESS is not set: durable workflows are off; every module works without them"}
