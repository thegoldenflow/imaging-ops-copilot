"""FastAPI application entry point."""

import asyncio
import contextlib
import gc
import logging
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.agents import ondemand
from app.agents.ondemand_router import router as ai_router
from app.agents.router import router as agents_router
from app.core import activity, config
from app.core.config import settings
from app.core.context import RequestContextMiddleware
from app.core.db.migrate import init_db
from app.core.store import REWRITE_LOCK, unit_of_work
from app.core.unit_of_work import UnitOfWorkMiddleware
from app.ehr import accepted as accepted_suggestions
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
from app.modules.control_tower import service as control_tower
from app.modules.control_tower.router import router as control_tower_router
from app.modules.documentation import service as documentation
from app.modules.documentation.router import router as documentation_router
from app.modules.med_safety import service as med_safety
from app.modules.med_safety.router import router as med_safety_router
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
from app.modules.storyline import service as storyline
from app.modules.storyline.router import router as storyline_router
from app.modules.roadmap.router import router as roadmap_router
from app.modules.voice_services import followup as voice_followup
from app.modules.voice_services.router import router as voice_router
from app.workflows import bridge as workflow_bridge
from app.aiops import alerts as aiops_alerts
from app.aiops.router import router as aiops_router
from app.workflows.router import router as workflows_router

logging.basicConfig(level=logging.INFO, format='{"level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}')
log = logging.getLogger("app")
logging.getLogger("httpx2").setLevel(logging.WARNING)


def _step(name: str, fn, *args) -> None:
    """One background step in its own transaction, so a failure rolls back only that step. Skipped while the
    demo data is being rewritten (a reset holds REWRITE_LOCK)."""
    try:
        with unit_of_work() as store:
            if not store.conn().execute(text("SELECT pg_try_advisory_xact_lock_shared(:key)"),
                                        {"key": REWRITE_LOCK}).scalar():
                return
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
    Runs in a thread so slow LLM or mock-archive calls never block requests.
    The steps that call the model (third item True) wait while nobody uses the app (app/core/activity.py); the
    rule-based ones go on, so retrievals, escalations, reminders and the daily reset keep their times."""
    steps = [
        ("requisition intake", requisitions.process_pending, True),
        ("prior retrieval", lambda store: priors.process_due(store), False),
        ("critical escalation", critical.process_due, False),
        ("peer review sampling", peer_review.maybe_run_scheduled, False),
        ("feedback classification", feedback.process_pending, True),
        ("inspection reminders", lambda store: inspection.process_reminders(store, datetime.now()), False),
        ("daily demo reset", admin.maybe_daily_reset, False),
    ]
    while True:
        await asyncio.sleep(2)
        for name, fn, calls_model in steps:
            if calls_model and activity.idle():
                continue
            await asyncio.to_thread(_step, name, fn)


def _drain_events() -> None:
    try:
        bus.drain()  # one transaction per delivered event
    except Exception:
        log.exception("event delivery failed")


async def _hospital_loop() -> None:
    """Moves the hospital clock while the day simulator runs, delivers the domain events it published to the
    subscribers, then refreshes the Control Tower (boards and exception stream) and sweeps new orders and stays into
    the medication-safety queue (each a no-op when nothing changed).
    Every second (the time the steps took counts towards it), so a running simulation reaches the screens quickly.
    While nobody uses the app the clock stands still (every order it writes means model calls) and resumes where it
    stopped; the rest goes on, so events already published still reach their subscribers."""
    loop = asyncio.get_running_loop()
    while True:
        started = loop.time()
        if not activity.idle():
            await asyncio.to_thread(_step, "day simulator",
                                    lambda store: simulator.tick(store, awake_since=activity.awake_since()))
        await asyncio.to_thread(_drain_events)
        await asyncio.to_thread(_step, "control tower", control_tower.refresh_step)
        await asyncio.to_thread(_step, "medication safety intake", med_safety.intake_step)
        await asyncio.to_thread(_step, "storyline AI", storyline.ai_step)
        await asyncio.to_thread(_step, "voice follow-up sweep", voice_followup.sweep_step)
        await asyncio.sleep(max(0.1, 1 - (loop.time() - started)))


async def _workflow_loop() -> None:
    """Sends the workflow bridge's start and signal commands to Temporal (6.5); a no-op without TEMPORAL_ADDRESS.
    Apart from the hospital loop, so an unreachable Temporal never slows the boards."""
    while True:
        await asyncio.sleep(1)
        await asyncio.to_thread(_step, "workflow dispatch", workflow_bridge.dispatch_step)


async def _workflow_worker() -> None:
    """TEMPORAL_WORKER_IN_API=1: the workflow worker in this process (development, Playwright); otherwise it runs
    on its own (`python -m app.workflows.worker`)."""
    from app.workflows import worker

    while True:
        try:
            await worker.run(threads=worker.IN_API_THREADS)
        except asyncio.CancelledError:
            raise
        except Exception:  # Temporal not up yet, or gone: try again shortly
            log.exception("in-process workflow worker stopped; restarting in 5 s")
            await asyncio.sleep(5)


async def _narrator_loop() -> None:
    """Writes the narrative and recommended actions of new Control Tower exceptions, then the AI commentary of
    order reviews and the indication judgements of medication reconciliations, and the patient instructions of
    newly signed discharge summaries (a model call each), apart from the hospital loop so a slow model never delays
    the boards. Waits while nobody uses the app: what is pending is written when someone is back.
    With BACKGROUND_AI=on_demand the narration, commentary and judgements are written when a person opens the
    exception, review or reconciliation, so only the discharge instructions (a person signed the summary) stay here."""
    while True:
        await asyncio.sleep(2)
        if activity.idle():
            continue
        if not config.ai_on_open():
            await asyncio.to_thread(_step, "control tower narration", control_tower.narrate_step)
            await asyncio.to_thread(_step, "medication safety AI", med_safety.ai_step)
        await asyncio.to_thread(_step, "documentation intake", documentation.intake_step)


def _ai_job(job: ondemand.Job) -> None:
    """One queued AI job in its own transaction; put back while a demo reset rewrites the data."""
    try:
        with unit_of_work() as store:
            if not store.conn().execute(text("SELECT pg_try_advisory_xact_lock_shared(:key)"),
                                        {"key": REWRITE_LOCK}).scalar():
                ondemand.requeue(job)
                return
            ondemand.run_job(store, job)
    except Exception:  # the item's own outcome is on its record; keep the loop alive (no payloads: PHI)
        log.exception("AI job %s failed", job.kind)


async def _ai_jobs_loop() -> None:
    """Runs the AI a person asked for without waiting on the page (app/agents/ondemand.py): the new work their own
    action created (a requisition they submitted, a patient's survey answer, a storyline step) and the batches
    they asked for on a list page. Two at a time, so a batch never starves the people opening items. Not paused
    while idle: someone asked for each of these."""
    async def worker() -> None:
        while True:
            job = ondemand.next_job()
            if job is None:
                await asyncio.sleep(0.3)
                continue
            await asyncio.to_thread(_ai_job, job)

    await asyncio.gather(*(worker() for _ in range(ondemand.CONCURRENCY)))


async def _aiops_loop() -> None:
    """Compares today's trace aggregates with the AI Ops alert thresholds (config/alerts.yaml, 6.6) every minute;
    a breach opens one admin Task per alert, scope and day."""
    while True:
        await asyncio.sleep(60)
        await asyncio.to_thread(_step, "ai ops alerts", aiops_alerts.check)


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    # Requests copy thousands of cached rows; with the default thresholds the cyclic GC keeps
    # rescanning the large, long-lived row cache (app/core/db/repo.py). Measured ~30% faster.
    gc.set_threshold(50_000, 50, 100)
    await asyncio.to_thread(init_db)  # migrate; generate the demo data on first start
    workflow_bridge.install()  # domain events -> Temporal (only with TEMPORAL_ADDRESS)
    accepted_suggestions.install()  # accepted suggestion cards -> the EHR records them (WP9)
    storyline.install()  # the hospital storyline's cards and EHR script (WP9; a no-op until its reset)
    loops = [_dispatch_loop, _intake_loop, _hospital_loop, _narrator_loop, _ai_jobs_loop, _aiops_loop]
    if settings.temporal_address:
        loops.append(_workflow_loop)
        if settings.temporal_worker_in_api:
            loops.append(_workflow_worker)
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
app.include_router(agents_router)
app.include_router(ai_router)
app.include_router(control_tower_router)
app.include_router(workflows_router)
app.include_router(med_safety_router)
app.include_router(documentation_router)
app.include_router(voice_router)
app.include_router(aiops_router)
app.include_router(storyline_router)
app.include_router(roadmap_router)


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
