"""Clinical knowledge Q&A API (staff-facing reference over the knowledge graph)."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.core.auth import audit_phi, require_roles
from app.core.models import Role, StaffUser
from app.core.store import get_store
from app.modules.clinical_kg import service

router = APIRouter(prefix="/api/clinical-kg", tags=["clinical_kg"])

# Clinical readers only: no front desk (the phone agent must not answer medical questions), no referrers.
READERS = require_roles(Role.RADIOLOGIST, Role.MEDICAL_DIRECTOR, Role.TECHNOLOGIST, Role.ADMIN)


class Question(BaseModel):
    question: str = Field(min_length=2, max_length=500)
    requisition_id: str | None = None


@router.get("/stats")
def stats(user: StaffUser = Depends(READERS)):
    return service.stats()


@router.post("/ask")
def ask(body: Question, request: Request, user: StaffUser = Depends(READERS)):
    store = get_store()
    patient = None
    if body.requisition_id:
        req = store.requisitions.get(body.requisition_id)
        if req is None:
            raise HTTPException(404, "Requisition not found")
        patient = store.patients[req.patient_id]
        # The question may carry requisition text: audit it and let the gateway redact that patient.
        audit_phi(request, user, action="knowledge_query", resource_type="requisition", resource_id=req.id)
    return service.ask(store, body.question, user.name, datetime.now(), patient=patient,
                       requisition_id=body.requisition_id)


@router.get("/history")
def history(user: StaffUser = Depends(READERS)):
    return {"entries": list(reversed(service.log(get_store())[-20:]))}
