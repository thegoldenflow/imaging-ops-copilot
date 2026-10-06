"""FastAPI application entry point."""

import asyncio
import contextlib
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.store import get_store
from app.integrations.mocks import dispatch_due
from app.modules.admin.router import router as admin_router
from app.modules.backlog.router import router as backlog_router
from app.modules.contrast.router import router as contrast_router
from app.modules.critical import service as critical
from app.modules.critical.router import router as critical_router
from app.modules.evals.router import router as evals_router
from app.modules.frontdesk.router import router as frontdesk_router
from app.modules.mri_safety.router import router as mri_router
from app.modules.peer_review import service as peer_review
from app.modules.peer_review.router import router as peer_review_router
from app.modules.prep.router import router as prep_router
from app.modules.priors import service as priors
from app.modules.priors.router import router as priors_router
from app.modules.protocols.router import router as protocols_router
from app.modules.reports.router import router as reports_router
from app.modules.requisitions import service as requisitions
from app.modules.requisitions.router import router as requisitions_router
from app.modules.scheduling.router import router as scheduling_router

logging.basicConfig(level=logging.INFO, format='{"level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}')
log = logging.getLogger("app")
logging.getLogger("httpx2").setLevel(logging.WARNING)


async def _dispatch_loop() -> None:
    """Sends scheduled reminders and other messages when they come due."""
    while True:
        await asyncio.sleep(5)
        try:
            dispatch_due()
        except Exception:  # keep the loop alive; log without payloads (may contain PHI)
            log.exception("dispatch failed")


async def _intake_loop() -> None:
    """Runs new requisitions through the AI pipeline, works prior-imaging retrievals
    moves critical-result cases through notification and escalation, and starts
    the nightly peer-review sampling run.
    Runs in a thread so slow LLM or mock-archive calls never block requests."""
    while True:
        await asyncio.sleep(2)
        try:
            await asyncio.to_thread(requisitions.process_pending, get_store())
            await asyncio.to_thread(priors.process_due)
            await asyncio.to_thread(critical.process_due, get_store())
            await asyncio.to_thread(peer_review.maybe_run_scheduled, get_store())
        except Exception:
            log.exception("intake worker failed")


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    get_store()  # build the synthetic data set up front
    tasks = [asyncio.create_task(_dispatch_loop()), asyncio.create_task(_intake_loop())]
    yield
    for task in tasks:
        task.cancel()


app = FastAPI(title="Imaging Ops Copilot API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(admin_router)
app.include_router(scheduling_router)
app.include_router(reports_router)
app.include_router(frontdesk_router)
app.include_router(requisitions_router)
app.include_router(protocols_router)
app.include_router(contrast_router)
app.include_router(mri_router)
app.include_router(prep_router)
app.include_router(priors_router)
app.include_router(evals_router)
app.include_router(backlog_router)
app.include_router(critical_router)
app.include_router(peer_review_router)
