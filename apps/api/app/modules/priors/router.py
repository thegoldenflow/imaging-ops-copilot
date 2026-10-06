"""System 10 · prior imaging retrieval board."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.core.auth import CLINICAL_STAFF, audit_phi, require_roles
from app.core.models import StaffUser
from app.core.store import get_store
from app.integrations.mocks import MOCK_CONFIG
from app.modules.priors import service

router = APIRouter(prefix="/api/priors", tags=["priors"])
VIEWERS = require_roles(*CLINICAL_STAFF)


@router.get("/tasks")
def list_tasks(request: Request, user: StaffUser = Depends(VIEWERS)):
    store = get_store()
    rows = []
    for t in sorted(service.tasks(store).values(), key=lambda t: t.created_at, reverse=True):
        appt = store.appointments.get(t.appointment_id)
        rows.append({**t.model_dump(mode="json"), "patient_name": store.patients[t.patient_id].full_name,
                     "appointment_start": appt.start.isoformat() if appt else None,
                     "exam_name": store.exams[appt.exam_code].name if appt else None})
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    audit_phi(request, user, action="read", resource_type="prior_retrievals", resource_id=None)
    return {"tasks": rows, "counts": counts, "simulation": MOCK_CONFIG["outside_archive"].model_dump(),
            "policy": {"max_attempts": service.MAX_ATTEMPTS, "backoff_seconds": service.BACKOFF_SECONDS}}


@router.post("/tasks/{task_id}/retry")
def retry(task_id: str, user: StaffUser = Depends(VIEWERS)):
    store = get_store()
    task = service.tasks(store).get(task_id)
    if task is None:
        raise HTTPException(404, "Task not found")
    if task.status not in ("failed", "not_found"):
        raise HTTPException(409, f"Task is {task.status}")
    service.retry(store, task)
    return task.model_dump(mode="json")


class Simulation(BaseModel):
    failure_rate: float = Field(ge=0, le=1)


@router.put("/simulation")
def simulate(body: Simulation, user: StaffUser = Depends(VIEWERS)):
    """Demo control: make the mock outside archives fail some or all requests."""
    MOCK_CONFIG["outside_archive"].failure_rate = body.failure_rate
    return MOCK_CONFIG["outside_archive"].model_dump()
