"""System 3 · Report Generator API."""

import random
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel

from app.core.auth import audit_phi, current_user, deny, require_roles
from app.core.models import ImagingStudy, Role, StaffUser
from app.core.store import Store, get_store
from app.llm.deid import age_from_dob
from app.modules.reports import service

router = APIRouter(prefix="/api/reports", tags=["reports"])

READERS = require_roles(Role.RADIOLOGIST, Role.MEDICAL_DIRECTOR, Role.ADMIN)
RADIOLOGIST = require_roles(Role.RADIOLOGIST)
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def study_view(store: Store, study: ImagingStudy) -> dict:
    patient = store.patients[study.patient_id]
    report = service.report_for_study(store, study.id)
    return {
        **study.model_dump(mode="json"),
        "patient_name": patient.full_name,
        "patient_age": age_from_dob(patient.dob),
        "patient_sex": patient.sex,
        "exam_name": store.exams[study.exam_code].name,
        "referrer_name": store.referrers[study.referrer_id].name,
        "report_id": report.id if report else None,
        "report_status": report.status if report else "unreported",
    }


def report_view(store: Store, report: service.Report) -> dict:
    study = store.studies[report.study_id]
    return {**report.model_dump(mode="json"), "study": study_view(store, study)}


def _can_view_report(user: StaffUser, report: service.Report) -> bool:
    if user.role in (Role.RADIOLOGIST, Role.MEDICAL_DIRECTOR, Role.ADMIN):
        return True
    # Referrers see only signed reports for their own patients; other roles never see drafts.
    if user.role == Role.REFERRER:
        return report.status == "signed" and report.referrer_id == user.referrer_id
    return report.status == "signed"


@router.get("/worklist")
def worklist(request: Request, user: StaffUser = Depends(READERS)):
    store = get_store()
    studies = sorted(store.studies.values(), key=lambda s: s.performed_at)
    audit_phi(request, user, action="read", resource_type="reading_worklist", resource_id=None)
    return {"studies": [study_view(store, s) for s in studies]}


@router.get("/studies/{study_id}/image")
def study_image(study_id: str, request: Request, user: StaffUser = Depends(current_user)):
    store = get_store()
    study = store.studies.get(study_id)
    if study is None:
        raise HTTPException(404, "Study not found")
    report = service.report_for_study(store, study_id)
    if user.role not in (Role.RADIOLOGIST, Role.MEDICAL_DIRECTOR, Role.ADMIN) and not (
        report and _can_view_report(user, report)
    ):
        deny(request, user, resource_type="study_image", resource_id=study_id)
    data, media_type = store.images[study.image_key]
    audit_phi(request, user, action="read", resource_type="study_image", resource_id=study_id)
    return Response(content=data, media_type=media_type)


@router.post("/upload")
async def upload(request: Request, file: UploadFile = File(...), user: StaffUser = Depends(RADIOLOGIST)):
    if file.content_type not in ("image/png", "image/jpeg"):
        raise HTTPException(415, "Upload a PNG or JPEG image (DICOM is not supported in this demo)")
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Image larger than 10 MB")
    store = get_store()
    key = store.next_id("IMG-UP")
    store.images[key] = (data, file.content_type)
    # Uploads are attached to a random synthetic patient; filenames are not kept.
    patient_id = random.choice([p for p in store.patients if not p.startswith("PT-DEMO")])
    study = ImagingStudy(
        id=store.next_id("ST"), appointment_id=None, patient_id=patient_id,
        referrer_id=random.choice(list(store.referrers)), exam_code="XR_CHEST", performed_at=datetime.now(),
        image_key=key, study_uid=f"2.25.{random.getrandbits(100)}", indication="Uploaded image",
    )
    store.studies[study.id] = study
    store.touch()
    audit_phi(request, user, action="create", resource_type="imaging_study", resource_id=study.id)
    return study_view(store, study)


@router.post("/studies/{study_id}/draft")
def create_draft(study_id: str, request: Request, user: StaffUser = Depends(RADIOLOGIST)):
    store = get_store()
    study = store.studies.get(study_id)
    if study is None:
        raise HTTPException(404, "Study not found")
    existing = service.report_for_study(store, study_id)
    if existing and existing.status == "signed":
        raise HTTPException(409, "Report already signed")
    report = service.generate_draft(store, study)
    audit_phi(request, user, action="create", resource_type="report_draft", resource_id=report.id)
    return report_view(store, report)


@router.get("/critical-results")
def list_critical(user: StaffUser = Depends(READERS)):
    store = get_store()
    items = sorted(service.critical_results(store).values(), key=lambda c: c.created_at, reverse=True)
    return {"critical_results": [
        {**c.model_dump(mode="json"), "patient_name": store.patients[c.patient_id].full_name,
         "referrer_name": store.referrers[c.referrer_id].name} for c in items
    ]}


@router.get("/mine")
def referrer_reports(request: Request, user: StaffUser = Depends(require_roles(Role.REFERRER))):
    store = get_store()
    items = [r for r in service.reports(store).values() if r.referrer_id == user.referrer_id and r.status == "signed"]
    audit_phi(request, user, action="read", resource_type="report_list", resource_id=user.referrer_id)
    return {"reports": [report_view(store, r) for r in items]}


@router.get("/{report_id}")
def get_report(report_id: str, request: Request, user: StaffUser = Depends(current_user)):
    store = get_store()
    report = service.reports(store).get(report_id)
    if report is None:
        raise HTTPException(404, "Report not found")
    if not _can_view_report(user, report):
        deny(request, user, resource_type="report", resource_id=report_id)
    audit_phi(request, user, action="read", resource_type="report", resource_id=report_id)
    return report_view(store, report)


class SectionAction(BaseModel):
    action: str  # accept, edit, delete, reset
    text: str | None = None


@router.patch("/{report_id}/sections/{key}")
def review_section(report_id: str, key: str, body: SectionAction, request: Request,
                   user: StaffUser = Depends(RADIOLOGIST)):
    store = get_store()
    report = service.reports(store).get(report_id)
    if report is None:
        raise HTTPException(404, "Report not found")
    if report.status == "signed":
        raise HTTPException(409, "Signed reports cannot be edited")
    section = next((s for s in report.sections if s.key == key), None)
    if section is None:
        raise HTTPException(404, "Unknown section")
    if body.action == "accept":
        section.status, section.final_text = "accepted", section.ai_text
    elif body.action == "edit":
        section.status, section.final_text = "edited", (body.text or "").strip()
    elif body.action == "delete":
        section.status, section.final_text = "deleted", ""
    elif body.action == "reset":
        section.status, section.final_text = "pending", section.ai_text
    else:
        raise HTTPException(422, "Unknown action")
    store.touch()
    audit_phi(request, user, action="update", resource_type="report", resource_id=report_id)
    return report_view(store, report)


class SignRequest(BaseModel):
    confirmed_urgent: list[int] = []


@router.post("/{report_id}/sign")
def sign_report(report_id: str, body: SignRequest, request: Request, user: StaffUser = Depends(RADIOLOGIST)):
    store = get_store()
    report = service.reports(store).get(report_id)
    if report is None:
        raise HTTPException(404, "Report not found")
    if report.status == "signed":
        raise HTTPException(409, "Already signed")
    pending = [s.label for s in report.sections if s.status == "pending"]
    if pending:
        raise HTTPException(422, f"Review every section before signing: {', '.join(pending)}")
    if not any(s.final_text.strip() for s in report.sections if s.key == "impression"):
        raise HTTPException(422, "Impression is required")
    created = service.sign(store, report, user.name, body.confirmed_urgent)
    audit_phi(request, user, action="sign", resource_type="report", resource_id=report_id)
    return {"report": report_view(store, report), "critical_results": [c.model_dump(mode="json") for c in created]}


@router.post("/{report_id}/send")
def send_to_referrer(report_id: str, request: Request,
                     user: StaffUser = Depends(require_roles(Role.RADIOLOGIST, Role.ADMIN, Role.FRONT_DESK))):
    store = get_store()
    report = service.reports(store).get(report_id)
    if report is None:
        raise HTTPException(404, "Report not found")
    # Enforced on the server: drafts never leave the reading room.
    if report.status != "signed":
        raise HTTPException(409, "Only signed reports can be sent to the referring physician")
    report.sent_to_referrer_at = datetime.now()
    store.touch()
    audit_phi(request, user, action="disclose", resource_type="report", resource_id=report_id)
    return report_view(store, report)
