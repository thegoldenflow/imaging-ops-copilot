"""System 8 · MRI safety screening: staff review and the public questionnaire."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.core.auth import CLINICAL_STAFF, audit_phi, require_roles
from app.core.models import Role, StaffUser
from app.core.store import get_store
from app.core.templates import LANGUAGES
from app.modules.mri_safety import service

router = APIRouter(tags=["mri-safety"])


@router.get("/api/mri-screening")
def list_screenings(request: Request, user: StaffUser = Depends(require_roles(*CLINICAL_STAFF))):
    store = get_store()
    order = {"flagged": 0, "sent": 1, "no_flags": 2, "not_cleared": 3, "cleared": 4}
    rows = []
    for s in sorted(service.screenings(store).values(), key=lambda s: (order.get(s.status, 9), s.created_at)):
        appt = store.appointments.get(s.appointment_id or "")
        rows.append({**s.model_dump(mode="json", exclude={"token"}), "patient_name": store.patients[s.patient_id].full_name,
                     "appointment_start": appt.start.isoformat() if appt else None})
    audit_phi(request, user, action="read", resource_type="mri_screening_list", resource_id=None)
    return {"screenings": rows, "questions": service.QUESTION_LABEL}


class Review(BaseModel):
    decision: Literal["cleared", "not_cleared"]
    note: str


@router.post("/api/mri-screening/{screening_id}/review")
def review(screening_id: str, body: Review, request: Request,
           user: StaffUser = Depends(require_roles(Role.TECHNOLOGIST, Role.RADIOLOGIST))):
    store = get_store()
    screening = service.screenings(store).get(screening_id)
    if screening is None:
        raise HTTPException(404, "Screening not found")
    if screening.status == "sent" and not screening.flags:
        raise HTTPException(409, "The patient has not answered the questionnaire yet")
    if len(body.note.strip()) < 3:
        raise HTTPException(422, "Add a short note explaining the decision")
    service.review(store, screening, user.name, body.decision, body.note.strip())
    audit_phi(request, user, action="review", resource_type="mri_screening", resource_id=screening_id)
    return screening.model_dump(mode="json", exclude={"token"})


@router.get("/api/public/mri-screening/{token}")
def public_get(token: str):
    store = get_store()
    screening = service.by_token(store, token)
    if screening is None:
        raise HTTPException(404, "This link is invalid or has expired")
    patient = store.patients[screening.patient_id]
    return {"first_name": patient.given_name, "language": screening.language, "languages": LANGUAGES,
            "questions": service.QUESTIONS, "submitted": screening.submitted_at is not None}


class Answers(BaseModel):
    answers: dict[str, bool]
    free_text: str = ""
    language: str


@router.post("/api/public/mri-screening/{token}")
def public_submit(token: str, body: Answers, request: Request):
    store = get_store()
    screening = service.by_token(store, token)
    if screening is None:
        raise HTTPException(404, "This link is invalid or has expired")
    if body.language not in LANGUAGES:
        raise HTTPException(422, "Unsupported language")
    service.submit(store, screening, body.answers, body.free_text, body.language)
    store.audit.record(user_id=f"patient:{screening.patient_id}", user_name="Patient (self-service)", role="patient",
                       action="update", resource_type="mri_screening", resource_id=screening.id,
                       source_ip=request.client.host if request.client else None, reason="MRI safety questionnaire")
    # Patients only learn that staff will review their answers, never a safety verdict.
    return {"received": True, "needs_review": screening.status == "flagged"}
