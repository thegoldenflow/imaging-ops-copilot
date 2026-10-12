"""The workflow worker (spec 6.5): `uv run python -m app.workflows.worker` (or the compose service `worker`).

It polls the task queue (TEMPORAL_TASK_QUEUE, namespace TEMPORAL_NAMESPACE) for the three workflows and their
activities. Activities are synchronous and run in a thread pool, each in its own database transaction against
DATABASE_URL (the API's database). Kill it at any point and start it again: Temporal replays each workflow from
its history, so completed steps are not run again, and an activity that was cut off is retried.

`TEMPORAL_WORKER_IN_API=1` runs the same worker inside the API process instead (development, Playwright).
"""

from __future__ import annotations

import asyncio
import logging
import signal
from concurrent.futures import ThreadPoolExecutor

from temporalio.client import Client
from temporalio.worker import Worker

from app.core.config import settings
from app.workflows.activities import ACTIVITIES
from app.workflows.capacity import CapacityExceptionWorkflow
from app.workflows.journey import InpatientJourneyWorkflow
from app.workflows.review import LowConfidenceReviewWorkflow

log = logging.getLogger("app.workflows")
WORKFLOWS = [InpatientJourneyWorkflow, CapacityExceptionWorkflow, LowConfidenceReviewWorkflow]


def build(client: Client, *, task_queue: str | None = None, threads: int = 8, **kwargs) -> Worker:
    return Worker(client, task_queue=task_queue or settings.temporal_task_queue, workflows=WORKFLOWS,
                  activities=ACTIVITIES, activity_executor=ThreadPoolExecutor(max_workers=threads,
                                                                              thread_name_prefix="activity"),
                  max_concurrent_activities=threads, **kwargs)


IN_API_THREADS = 2  # inside the API the activities share its connection pool (each also takes one for audit events)


async def run(stop: asyncio.Event | None = None, *, threads: int = 8) -> None:
    if not settings.temporal_address:
        raise SystemExit("TEMPORAL_ADDRESS is not set: there is no Temporal server to work for")
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    worker = build(client, threads=threads)
    log.info("workflow worker polling %s on %s (namespace %s)", settings.temporal_task_queue,
             settings.temporal_address, settings.temporal_namespace)
    if stop is None:
        await worker.run()
        return
    async with worker:
        await stop.wait()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format='{"level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}')
    stop = asyncio.Event()

    async def runner() -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except NotImplementedError:  # Windows: Ctrl+C raises KeyboardInterrupt instead
                pass
        await run(stop)

    try:
        asyncio.run(runner())
    except KeyboardInterrupt:
        log.info("workflow worker stopped")


if __name__ == "__main__":
    main()
