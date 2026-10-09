"""FastAPI application entry point."""

import asyncio
import contextlib
import gc
import logging
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.context import RequestContextMiddleware
from app.core.db.migrate import init_db
from app.core.store import unit_of_work
from app.core.unit_of_work import UnitOfWorkMiddleware
from app.ehr import simulator
from app.ehr.events import bus
from app.ehr.gateway import BreakGlassRequired, FhirAccessDenied, FhirConflict
from app.ehr.platform_router import router as platform_router
from app.ehr.router import router as hospital_router
from app.integrations.mocks import dispatch_due
from app.modules.admin import service as admin
from app.modules.admin.router import router as admin_router
from app.modules.backlog.router import router as backlog_router
from app.modules.billing.router import router as billing_router
from app.modules.contrast.router import router as contrast_router
from app.modules.critical import service as critical
from app.modules.critical.router import router as critical_router
from app.modules.dose.router import router as dose_router
from app.modules.evals.router import router as evals_router
from app.modules.feedback import service as feedback
from app.modules.feedback.router import router as feedback_router
from app.modules.frontdesk.router import router as frontdesk_router
from app.modules.inspection import service as inspection
from app.modules.inspection.router import router as inspection_router
from app.modules.clinical_kg.router import router as clinical_kg_router
from app.modules.inventory.router import router as inventory_router
from app.modules.mri_safety.router import router as mri_router
from app.modules.peer_review import service as peer_review
from app.modules.phipa.router import router as phipa_router
from app.modules.portal.router import router as portal_router
from app.modules.peer_review.router import router as peer_review_router
from app.modules.prep.router import router as prep_router
from app.modules.priors import service as priors
from app.modules.priors.router import router as priors_router
from app.modules.protocols.router import router as protocols_router
from app.modules.referrals.router import router as referrals_router
from app.modules.reports.router import router as reports_router
from app.modules.requisitions import service as requisitions
from app.modules.requisitions.router import router as requisitions_router
from app.modules.scheduling.router import router as scheduling_router

logging.basicConfig(level=logging.INFO, format='{"level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}')
log = logging.getLogger("app")
logging.getLogger("httpx2").setLevel(logging.WARNING)


def _step(name: str, fn, *args) -> None:
    """One background step in its own transaction, so a failure rolls back only that step."""
    try:
        with unit_of_work() as store:
            fn(store, *args)
    except Exception:  # keep the loop alive; log without payloads (may contain PHI)
        log.exception("%s failed", name)


async def _dispatch_loop() -> None:
    """Sends scheduled reminders and other messages when they come due."""
    while True:
        await asyncio.sleep(5)
        await asyncio.to_thread(_step, "dispatch", lambda store: dispatch_due())


async def _intake_loop() -> None:
    """Runs new requisitions through the AI pipeline, works prior-imaging retrievals,
    moves critical-result cases through notification and escalation, starts the
    nightly peer-review sampling run, classifies new patient feedback and sends
    inspection reminders as items come due. Each step claims its due rows with
    SELECT ... FOR UPDATE SKIP LOCKED, so a second API process would not repeat it.
    Runs in a thread so slow LLM or mock-archive calls never block requests."""
    steps = [
        ("requisition intake", requisitions.process_pending),
        ("prior retrieval", lambda store: priors.process_due(store)),
        ("critical escalation", critical.process_due),
        ("peer review sampling", peer_review.maybe_run_scheduled),
        ("feedback classification", feedback.process_pending),
        ("inspection reminders", lambda store: inspection.process_reminders(store, datetime.now())),
        ("daily demo reset", admin.maybe_daily_reset),
    ]
    while True:
        await asyncio.sleep(2)
        for name, fn in steps:
            await asyncio.to_thread(_step, name, fn)


def _drain_events() -> None:
    try:
        bus.drain()  # one transaction per delivered event
    except Exception:
        log.exception("event delivery failed")


async def _hospital_loop() -> None:
    """Moves the hospital clock while the day simulator runs, then delivers the domain events it
    published to the subscribers. Every second, so a running simulation reaches the screens quickly."""
    while True:
        await asyncio.sleep(1)
        await asyncio.to_thread(_step, "day simulator", simulator.tick)
        await asyncio.to_thread(_drain_events)


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    # Requests copy thousands of cached rows; with the default thresholds the cyclic GC keeps
    # rescanning the large, long-lived row cache (app/core/db/repo.py). Measured ~30% faster.
    gc.set_threshold(50_000, 50, 100)
    await asyncio.to_thread(init_db)  # migrate; generate the demo data on first start
    loops = (_dispatch_loop, _intake_loop, _hospital_loop)
    tasks = [asyncio.create_task(loop()) for loop in loops] if settings.background_workers else []
    yield
    for task in tasks:
        task.cancel()


app = FastAPI(title="Imaging Ops Copilot API", version="0.1.0", lifespan=lifespan)
app.add_middleware(UnitOfWorkMiddleware)  # one transaction per request
app.add_middleware(RequestContextMiddleware)  # who is acting, for the ai_call audit event
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
app.include_router(dose_router)
app.include_router(inventory_router)
app.include_router(referrals_router)
app.include_router(portal_router)
app.include_router(billing_router)
app.include_router(feedback_router)
app.include_router(phipa_router)
app.include_router(inspection_router)
app.include_router(clinical_kg_router)
app.include_router(hospital_router)
app.include_router(platform_router)


@app.exception_handler(FhirAccessDenied)
async def _fhir_denied(request: Request, exc: FhirAccessDenied):
    """Refused by the FhirGateway (already audited). A patient outside the user's units says so, so the
    web app can offer break-glass."""
    body = {"detail": str(exc), "code": "forbidden"}
    if isinstance(exc, BreakGlassRequired):
        body["code"] = "break_glass_required"
    return JSONResponse(status_code=403, content=body)


@app.exception_handler(FhirConflict)
async def _fhir_conflict(request: Request, exc: FhirConflict):
    return JSONResponse(status_code=409, content={"detail": str(exc)})
