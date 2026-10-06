"""System 13 · Peer review and QA API. QA results are for the QA lead (the
medical director) only; reviewers see their own assignments, blinded."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from app.core.auth import audit_phi, current_user, deny, require_roles
from app.core.models import Role, StaffUser
from app.core.store import Store, get_store
from app.llm.deid import age_from_dob
from app.modules.peer_review import service
from app.modules.reports.service import reports

router = APIRouter(prefix="/api/peer-review", tags=["peer-review"])

RADIOLOGIST = require_roles(Role.RADIOLOGIST)
QA_LEAD = require_roles(Role.MEDICAL_DIRECTOR)


def review_view(store: Store, review: service.PeerReview, *, blinded: bool) -> dict:
    """Blinded views hide who signed the original report (and who else reviewed)."""
    report = reports(store)[review.report_id]
    study = store.studies[review.study_id]
    patient = store.patients[study.patient_id]
    exam = store.exams[study.exam_code]
    view = {
        **review.model_dump(mode="json"),
        "exam_name": exam.name, "modality": exam.modality, "performed_at": study.performed_at.isoformat(timespec="minutes"),
        "patient_age": age_from_dob(patient.dob), "patient_sex": patient.sex, "indication": study.indication,
        "report_sections": [{"label": s.label, "text": s.final_text} for s in report.sections
                            if s.status != "deleted" and s.final_text],
        "report_signed_on": report.signed_at.date().isoformat() if report.signed_at else None,
        "has_image": bool(study.image_key),
    }
    if blinded:
        view["original_reader_id"] = None
    else:
        view["original_reader_name"] = store.staff[review.original_reader_id].name
        view["reviewer_name"] = store.staff[review.reviewer_id].name if review.reviewer_id else None
    return view


@router.get("/mine")
def my_reviews(request: Request, user: StaffUser = Depends(RADIOLOGIST)):
    store = get_store()
    mine = [r for r in service.reviews(store).values() if r.reviewer_id == user.id]
    # Open reviews oldest first, then completed ones newest first.
    mine = (sorted((r for r in mine if r.status != "completed"), key=lambda r: r.assigned_at)
            + sorted((r for r in mine if r.status == "completed"), key=lambda r: r.completed_at, reverse=True))
    audit_phi(request, user, action="read", resource_type="peer_review_list", resource_id=user.id)
    return {"reviews": [review_view(store, r, blinded=True) for r in mine[:60]],
            "open": sum(r.status == "assigned" for r in mine),
            "scores": service.SCORES, "discrepancy_types": service.DISCREPANCY_TYPES}


@router.get("/qa")
def qa(request: Request, user: StaffUser = Depends(QA_LEAD)):
    store = get_store()
    items = sorted(service.reviews(store).values(), key=lambda r: r.completed_at or r.assigned_at, reverse=True)
    audit_phi(request, user, action="read", resource_type="qa_report", resource_id=None)
    return {
        "config": service.config(store).model_dump(mode="json"),
        "report": service.qa_report(store),
        "runs": [r.model_dump(mode="json") for r in reversed(service.runs(store)[-14:])],
        "open": sum(r.status == "assigned" for r in items),
        "unassigned": sum(r.status == "unassigned" for r in items),
        "recent": [review_view(store, r, blinded=False) for r in items if r.status == "completed"][:40],
        "scores": service.SCORES, "discrepancy_types": service.DISCREPANCY_TYPES,
    }


@router.get("/qa/export")
def export(request: Request, user: StaffUser = Depends(QA_LEAD)):
    store = get_store()
    audit_phi(request, user, action="export", resource_type="qa_report", resource_id=None)
    name = f"peer-review-{datetime.now():%Y%m%d}.csv"
    return Response(content=service.export_csv(store), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.put("/config")
def put_config(body: service.QaConfig, request: Request, user: StaffUser = Depends(QA_LEAD)):
    store = get_store()
    body.updated_by, body.updated_at = user.name, datetime.now()
    store.modules["qa_config"] = body
    store.touch()
    audit_phi(request, user, action="update", resource_type="qa_config", resource_id=None)
    return body.model_dump(mode="json")


@router.post("/run")
def run_now(request: Request, user: StaffUser = Depends(QA_LEAD)):
    store = get_store()
    run = service.run_sampling(store, now=datetime.now(), trigger="manual", by=user.name)
    audit_phi(request, user, action="create", resource_type="qa_sampling_run", resource_id=run.id)
    return run.model_dump(mode="json")


@router.get("/{review_id}")
def get_review(review_id: str, request: Request, user: StaffUser = Depends(current_user)):
    store = get_store()
    review = service.reviews(store).get(review_id)
    if review is None:
        raise HTTPException(404, "Review not found")
    if user.role == Role.MEDICAL_DIRECTOR:
        blinded = False
    elif review.reviewer_id == user.id:
        blinded = True
    else:
        deny(request, user, resource_type="peer_review", resource_id=review_id)
    audit_phi(request, user, action="read", resource_type="peer_review", resource_id=review_id)
    return review_view(store, review, blinded=blinded)


class SubmitRequest(BaseModel):
    score: str
    discrepancy_type: str | None = None
    comment: str = ""


@router.post("/{review_id}/submit")
def submit(review_id: str, body: SubmitRequest, request: Request, user: StaffUser = Depends(RADIOLOGIST)):
    store = get_store()
    review = service.reviews(store).get(review_id)
    if review is None:
        raise HTTPException(404, "Review not found")
    if review.reviewer_id != user.id:
        deny(request, user, resource_type="peer_review", resource_id=review_id)
    try:
        service.submit(store, review, reviewer=user, score=body.score, discrepancy_type=body.discrepancy_type,
                       comment=body.comment)
    except service.ReviewError as e:
        raise HTTPException(409 if "already" in str(e) else 422, str(e))
    audit_phi(request, user, action="update", resource_type="peer_review", resource_id=review_id)
    return review_view(store, review, blinded=True)
