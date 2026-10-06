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
from app.modules.frontdesk.router import router as frontdesk_router
from app.modules.reports.router import router as reports_router
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


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    get_store()  # build the synthetic data set up front
    task = asyncio.create_task(_dispatch_loop())
    yield
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
