"""System 7 · contrast settings and upcoming checks."""

from datetime import datetime

from fastapi import APIRouter, Depends, Request

from app.core.auth import CLINICAL_STAFF, audit_phi, require_roles
from app.core.models import Role, StaffUser
from app.core.store import get_store
from app.modules.contrast import service
from app.modules.scheduling.router import appointment_view

router = APIRouter(prefix="/api/contrast", tags=["contrast"])


@router.get("/config")
def get_config(user: StaffUser = Depends(require_roles(*CLINICAL_STAFF))):
    return service.config(get_store()).model_dump(mode="json")


@router.put("/config")
def put_config(body: service.ContrastConfig, request: Request,
               user: StaffUser = Depends(require_roles(Role.MEDICAL_DIRECTOR, Role.ADMIN))):
    store = get_store()
    body.updated_by, body.updated_at = user.name, datetime.now()
    store.modules["contrast_config"] = body
    audit_phi(request, user, action="update", resource_type="contrast_config", resource_id=None)
    store.touch()
    return body.model_dump(mode="json")


@router.get("/checks")
def checks(request: Request, days: int = 14, user: StaffUser = Depends(require_roles(*CLINICAL_STAFF))):
    store = get_store()
    rows = [{"appointment": appointment_view(store, a), "check": c.model_dump(mode="json")}
            for a, c in service.upcoming_checks(store, days)]
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["check"]["status"]] = counts.get(row["check"]["status"], 0) + 1
    audit_phi(request, user, action="read", resource_type="contrast_checks", resource_id=None)
    return {"checks": rows, "counts": counts, "config": service.config(store).model_dump(mode="json")}
