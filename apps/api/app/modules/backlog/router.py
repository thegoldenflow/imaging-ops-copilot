"""System 11 · Reporting backlog board API."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.core.auth import audit_phi, require_roles
from app.core.models import ACTIVE_STATUSES, Role, StaffUser
from app.core.store import Store, get_store
from app.llm.deid import age_from_dob
from app.modules.backlog import service
from app.modules.reports import dictation
from app.modules.reports.service import report_for_study
from app.modules.scheduling import service as scheduling

router = APIRouter(prefix="/api/backlog", tags=["backlog"])

VIEWERS = require_roles(Role.RADIOLOGIST, Role.OPERATIONS_MANAGER, Role.MEDICAL_DIRECTOR, Role.ADMIN)
MANAGERS = require_roles(Role.OPERATIONS_MANAGER, Role.MEDICAL_DIRECTOR, Role.ADMIN)
RADIOLOGIST = require_roles(Role.RADIOLOGIST)


def _row(store: Store, study, now: datetime) -> dict:
    patient = store.patients[study.patient_id]
    a = service.assignments(store).get(study.id)
    reader = store.staff.get(a.radiologist_id) if a else None
    draft = report_for_study(store, study.id)
    site = store.sites.get(study.site_id or "")
    return {
        "id": study.id, "patient_id": patient.id, "patient_name": patient.full_name,
        "exam_code": study.exam_code, "exam_name": store.exams[study.exam_code].name,
        "modality": service.modality(store, study), "site_id": study.site_id, "site_name": site.name if site else "Uploaded (no site)",
        "priority": study.priority, "performed_at": study.performed_at.isoformat(timespec="minutes"),
        "minutes": service.read_minutes(store, study), **service.study_state(store, study, now),
        "assigned_to": {"id": reader.id, "name": reader.name} if reader else None,
        "has_image": bool(study.image_key), "draft_report_id": draft.id if draft else None,
    }


def _group(rows: list[dict], key: str, label_key: str | None = None) -> list[dict]:
    groups: dict[str, dict] = {}
    for r in rows:
        g = groups.setdefault(r[key], {"key": r[key], "label": r[label_key] if label_key else r[key],
                                       "count": 0, "overdue": 0, "at_risk": 0})
        g["count"] += 1
        g["overdue"] += r["state"] == "overdue"
        g["at_risk"] += r["state"] == "at_risk"
    return sorted(groups.values(), key=lambda g: -g["count"])


@router.get("")
def board(request: Request, user: StaffUser = Depends(VIEWERS)):
    store, now = get_store(), datetime.now()
    studies = service.unread(store)
    rows = sorted((_row(store, s, now) for s in studies), key=lambda r: r["remaining_h"])
    for r in rows:
        r["age_bucket"] = next(label for limit, label in service.AGE_BUCKETS if r["age_h"] < limit)
    load = service.queue_minutes(store, studies)
    signed_today: dict[str, int] = {}
    for s in list(store.studies.values()):
        rep = report_for_study(store, s.id)
        if rep and rep.status == "signed" and rep.signed_at and rep.signed_at.date() == now.date() and rep.signed_by_id:
            signed_today[rep.signed_by_id] = signed_today.get(rep.signed_by_id, 0) + 1
    readers = []
    for rad in service.radiologists(store):
        mine = [r for r in rows if r["assigned_to"] and r["assigned_to"]["id"] == rad.id]
        readers.append({"id": rad.id, "name": rad.name, "modalities": rad.reading_modalities,
                        "on_shift": service.on_shift(store, rad.id), "queue_count": len(mine),
                        "queue_minutes": load.get(rad.id, 0), "overdue": sum(r["state"] == "overdue" for r in mine),
                        "signed_today": signed_today.get(rad.id, 0)})
    tat = service.turnaround(store, now)
    audit_phi(request, user, action="read", resource_type="reading_backlog", resource_id=None)
    return {
        "version": store.version,
        "kpis": {
            "unread": len(rows), "overdue": sum(r["state"] == "overdue" for r in rows),
            "at_risk": sum(r["state"] == "at_risk" for r in rows),
            "oldest_h": max((r["age_h"] for r in rows), default=0),
            "median_tat_h": tat["overall"]["median_h"], "within_target": tat["overall"]["within_target"],
        },
        "studies": rows,
        "groups": {
            "site": _group(rows, "site_id", "site_name"), "modality": _group(rows, "modality"),
            "priority": sorted(_group(rows, "priority"), key=lambda g: g["key"]),
            "age": [g for label in [lbl for _, lbl in service.AGE_BUCKETS]
                    for g in _group(rows, "age_bucket") if g["key"] == label],
        },
        "radiologists": readers,
        "suggestions": service.suggestions(store, now),
        "turnaround": tat,
        "targets": service.targets(store).model_dump(),
    }


class AssignRequest(BaseModel):
    radiologist_id: str
    reason: str = Field("Workload balancing", min_length=3)


def _reassign(store: Store, request: Request, user: StaffUser, study_id: str, rad_id: str, reason: str) -> dict:
    study = store.studies.get(study_id)
    if study is None or not service.is_unread(store, study):
        raise HTTPException(404, "No unread study with that id")
    rad = store.staff.get(rad_id)
    if rad is None or rad.role != Role.RADIOLOGIST:
        raise HTTPException(422, "Unknown radiologist")
    if not service.credentialed(store, rad, study):
        raise HTTPException(422, f"{rad.name} is not credentialed to read {service.modality(store, study).value}")
    service.assign(store, study, rad.id, user.name, reason)
    audit_phi(request, user, action="reassign", resource_type="study_assignment", resource_id=study_id)
    return {"study_id": study_id, "radiologist_id": rad.id}


@router.post("/studies/{study_id}/assign")
def reassign(study_id: str, body: AssignRequest, request: Request, user: StaffUser = Depends(MANAGERS)):
    return _reassign(get_store(), request, user, study_id, body.radiologist_id, body.reason)


class ApplyRequest(BaseModel):
    study_ids: list[str] | None = None  # None = all current suggestions


@router.post("/suggestions/apply")
def apply_suggestions(body: ApplyRequest, request: Request, user: StaffUser = Depends(MANAGERS)):
    store = get_store()
    applied = []
    for s in service.suggestions(store, datetime.now()):
        if body.study_ids is None or s["study_id"] in body.study_ids:
            applied.append(_reassign(store, request, user, s["study_id"], s["to_id"], f"Suggested: {s['reason']}"))
    return {"applied": applied}


class ShiftRequest(BaseModel):
    on_shift: bool


@router.put("/roster/{rad_id}")
def set_shift(rad_id: str, body: ShiftRequest, user: StaffUser = Depends(MANAGERS)):
    store = get_store()
    rad = store.staff.get(rad_id)
    if rad is None or rad.role != Role.RADIOLOGIST:
        raise HTTPException(404, "Unknown radiologist")
    service.roster(store)[rad_id] = body.on_shift
    store.touch()
    return {"id": rad_id, "on_shift": body.on_shift}


@router.put("/targets")
def set_targets(body: service.TatTargets, request: Request, user: StaffUser = Depends(MANAGERS)):
    if set(body.hours) != {"P1", "P2", "P3", "P4"} or any(h <= 0 for h in body.hours.values()):
        raise HTTPException(422, "Give a positive target in hours for P1 to P4")
    store = get_store()
    store.modules["tat_targets"] = body
    store.touch()
    audit_phi(request, user, action="update", resource_type="tat_targets", resource_id=None)
    return body.model_dump()


@router.post("/simulate-completion")
def simulate_completion(request: Request, user: StaffUser = Depends(MANAGERS)):
    """Demo control: the technologist finishes the next exam on the schedule."""
    store = scheduling.get_store_ready()
    now = datetime.now()
    upcoming = sorted((a for a in store.appointments.values() if a.status in ACTIVE_STATUSES and a.end >= now
                       and not scheduling.confirm_blockers(store, a)), key=lambda a: a.start)
    if not upcoming:
        raise HTTPException(409, "No upcoming exams to complete")
    study = scheduling.complete(store, upcoming[0], now)
    audit_phi(request, user, action="update", resource_type="appointment", resource_id=upcoming[0].id)
    return {"study_id": study.id, "appointment_id": upcoming[0].id}


@router.get("/studies/{study_id}")
def study_detail(study_id: str, request: Request, user: StaffUser = Depends(RADIOLOGIST)):
    store = get_store()
    study = store.studies.get(study_id)
    if study is None:
        raise HTTPException(404, "Study not found")
    patient = store.patients[study.patient_id]
    findings, impression = dictation.template(study.exam_code)
    report = report_for_study(store, study_id)
    audit_phi(request, user, action="read", resource_type="imaging_study", resource_id=study_id)
    return {
        **_row(store, study, datetime.now()), "patient_age": age_from_dob(patient.dob), "patient_sex": patient.sex,
        "indication": study.indication, "referrer_name": store.referrers[study.referrer_id].name,
        "report_status": report.status if report else "unreported",
        "template": {"findings": findings, "impression": impression},
        "assignment_history": a.history if (a := service.assignments(store).get(study_id)) else [],
    }


class SignRequest(BaseModel):
    findings: str = Field(min_length=1)
    impression: str = Field(min_length=1)
    critical_finding: str | None = None
    critical_level: str = "urgent"


@router.post("/studies/{study_id}/sign")
def sign(study_id: str, body: SignRequest, request: Request, user: StaffUser = Depends(RADIOLOGIST)):
    store = get_store()
    study = store.studies.get(study_id)
    if study is None:
        raise HTTPException(404, "Study not found")
    existing = report_for_study(store, study_id)
    if existing and existing.status == "signed":
        raise HTTPException(409, "Report already signed")
    if existing and existing.source == "ai_draft":
        raise HTTPException(409, "This study has an AI draft; review and sign it in the reading room")
    if not service.credentialed(store, user, study):
        raise HTTPException(403, f"You are not credentialed to read {service.modality(store, study).value}")
    report = dictation.sign_dictated(store, study, findings=body.findings.strip(), impression=body.impression.strip(),
                                     signer_id=user.id, signer_name=user.name)
    cases = []
    if body.critical_finding and body.critical_finding.strip():
        from app.modules.critical import service as critical

        cases.append(critical.open_case(store, report, body.critical_finding.strip(), body.critical_level,
                                        opened_by=user.name).model_dump(mode="json"))
    a = service.assignments(store).get(study_id)
    if a and a.radiologist_id != user.id:
        service.assign(store, study, user.id, user.name, "Read by another radiologist")
    store.touch()
    audit_phi(request, user, action="sign", resource_type="report", resource_id=report.id)
    return {"report_id": report.id, "critical_results": cases}
