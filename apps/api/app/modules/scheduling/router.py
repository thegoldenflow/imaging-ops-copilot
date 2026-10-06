"""System 2 · Scheduling Command Center API."""

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.core.auth import CLINICAL_STAFF, audit_phi, ensure_site, require_roles
from app.core.models import ACTIVE_STATUSES, Appointment, AppointmentStatus, Role, StaffUser
from app.core.store import Store
from app.modules.scheduling import service
from app.modules.scheduling.noshow import HIGH_RISK, get_model

router = APIRouter(prefix="/api/scheduling", tags=["scheduling"])

VIEWERS = require_roles(*CLINICAL_STAFF)
SCHEDULERS = require_roles(Role.FRONT_DESK, Role.OPERATIONS_MANAGER, Role.ADMIN)
MANAGERS = require_roles(Role.OPERATIONS_MANAGER, Role.ADMIN)


def appointment_view(store: Store, appt: Appointment) -> dict:
    patient = store.patients[appt.patient_id]
    return {
        **appt.model_dump(mode="json"),
        "patient_name": patient.full_name,
        "patient_language": patient.preferred_language,
        "exam_name": store.exams[appt.exam_code].name,
        "site_name": store.sites[appt.site_id].name,
        "modality": store.scanners[appt.scanner_id].modality,
        "high_risk": (appt.no_show_risk or 0) >= HIGH_RISK,
    }


def _site_scope(user: StaffUser, site_id: str | None) -> list[str] | None:
    if user.site_ids:
        return [site_id] if site_id in user.site_ids else user.site_ids
    return [site_id] if site_id else None


@router.get("/dashboard")
def dashboard(site_id: str | None = None, user: StaffUser = Depends(VIEWERS)):
    store = service.get_store_ready()
    nsm = get_model(store)
    scope = _site_scope(user, site_id)
    today = datetime.now().date()
    week_start = today - timedelta(days=today.weekday())
    todays = [a for a in store.appointments.values()
              if a.start.date() == today and (not scope or a.site_id in scope)]
    return {
        "today": service.utilization(store, today, 1, scope),
        "week": service.utilization(store, week_start, 7, scope),
        "heatmap": service.heatmap(store, today, scope),
        "kpis": {
            "appointments_today": sum(a.status != AppointmentStatus.CANCELLED for a in todays),
            "cancelled_today": sum(a.status == AppointmentStatus.CANCELLED for a in todays),
            "no_shows_today": sum(a.status == AppointmentStatus.NO_SHOW for a in todays),
            "high_risk_upcoming": sum(
                1 for a in store.appointments.values()
                if a.status in ACTIVE_STATUSES and (a.no_show_risk or 0) >= HIGH_RISK
                and (not scope or a.site_id in scope) and a.start.date() <= today + timedelta(days=7)
            ),
            "waitlist_active": sum(1 for w in store.waitlist.values() if w.active),
            "open_backfill_cases": sum(1 for c in service.cases(store).values() if c.status in ("open", "offered")),
        },
        "model": {"auc": nsm.auc, "base_rate": nsm.base_rate},
        "version": store.version,
    }


@router.get("/appointments")
def list_appointments(request: Request, day: date | None = None, site_id: str | None = None,
                      high_risk_only: bool = False, limit: int = 150, user: StaffUser = Depends(VIEWERS)):
    store = service.get_store_ready()
    get_model(store)
    scope = _site_scope(user, site_id)
    day = day or datetime.now().date()
    rows = [
        a for a in store.appointments.values()
        if a.start.date() == day and (not scope or a.site_id in scope)
        and (not high_risk_only or (a.status in ACTIVE_STATUSES and (a.no_show_risk or 0) >= HIGH_RISK))
    ]
    rows.sort(key=lambda a: (a.start, a.site_id))
    audit_phi(request, user, action="read", resource_type="appointment_list", resource_id=f"{day}:{scope}")
    return {"day": day.isoformat(), "appointments": [appointment_view(store, a) for a in rows[:limit]],
            "total": len(rows)}


class CancelRequest(BaseModel):
    reason: str = "Patient request"


@router.post("/appointments/{appointment_id}/cancel")
def cancel_appointment(appointment_id: str, body: CancelRequest, request: Request,
                       user: StaffUser = Depends(SCHEDULERS)):
    store = service.get_store_ready()
    appt = store.appointments.get(appointment_id)
    if appt is None:
        raise HTTPException(404, "Appointment not found")
    ensure_site(request, user, appt.site_id, "appointment", appt.id)
    if appt.status not in ACTIVE_STATUSES:
        raise HTTPException(409, f"Appointment is {appt.status}")
    case = service.cancel(store, appt, body.reason)
    audit_phi(request, user, action="update", resource_type="appointment", resource_id=appt.id)
    return {"appointment": appointment_view(store, appt), "backfill_case": case.model_dump(mode="json") if case else None}


@router.get("/waitlist")
def waitlist(request: Request, user: StaffUser = Depends(VIEWERS)):
    store = service.get_store_ready()
    now = datetime.now()
    rows = []
    for w in store.waitlist.values():
        if not w.active:
            continue
        patient = store.patients[w.patient_id]
        rows.append({
            **w.model_dump(mode="json"), "patient_name": patient.full_name,
            "language": patient.preferred_language, "exam_name": store.exams[w.exam_code].name,
            "wait_days": round((now - w.added_at).total_seconds() / 86400, 1),
        })
    rows.sort(key=lambda r: (service.URGENCY_RANK[r["urgency"]], -r["wait_days"]))
    audit_phi(request, user, action="read", resource_type="waitlist", resource_id=None)
    return {"entries": rows}


@router.get("/backfill")
def list_cases(user: StaffUser = Depends(VIEWERS)):
    store = service.get_store_ready()
    items = sorted(service.cases(store).values(), key=lambda c: c.created_at, reverse=True)
    return {"cases": [c.model_dump(mode="json") for c in items]}


@router.get("/backfill/{case_id}")
def get_case(case_id: str, request: Request, user: StaffUser = Depends(VIEWERS)):
    store = service.get_store_ready()
    case = service.cases(store).get(case_id)
    if case is None:
        raise HTTPException(404, "Case not found")
    audit_phi(request, user, action="read", resource_type="backfill_case", resource_id=case_id)
    return case.model_dump(mode="json")


class OfferRequest(BaseModel):
    waitlist_ids: list[str]


@router.post("/backfill/{case_id}/offers")
def send_offers(case_id: str, body: OfferRequest, request: Request, user: StaffUser = Depends(SCHEDULERS)):
    store = service.get_store_ready()
    case = service.cases(store).get(case_id)
    if case is None:
        raise HTTPException(404, "Case not found")
    if case.status == "filled":
        raise HTTPException(409, "Slot already filled")
    sent = service.send_offers(store, case, body.waitlist_ids)
    audit_phi(request, user, action="create", resource_type="waitlist_offer", resource_id=case_id)
    return {"sent": [o.model_dump(mode="json") for o in sent], "case": case.model_dump(mode="json")}


@router.post("/offers/{offer_id}/accept")
def accept_offer(offer_id: str, user: StaffUser = Depends(VIEWERS)):
    """Demo control: simulates the patient replying YES by SMS."""
    store = service.get_store_ready()
    offer = service.offers(store).get(offer_id)
    if offer is None:
        raise HTTPException(404, "Offer not found")
    try:
        appt = service.accept_offer(store, offer)
    except service.OfferConflict as e:
        raise HTTPException(409, str(e)) from e
    return {"appointment": appointment_view(store, appt),
            "case": service.cases(store)[offer.case_id].model_dump(mode="json")}


@router.get("/cross-site")
def cross_site(user: StaffUser = Depends(VIEWERS)):
    return service.cross_site(service.get_store_ready())


@router.get("/weights")
def get_weights(user: StaffUser = Depends(VIEWERS)):
    return service.weights(service.get_store_ready()).model_dump()


@router.put("/weights")
def put_weights(body: service.PriorityWeights, request: Request, user: StaffUser = Depends(MANAGERS)):
    store = service.get_store_ready()
    store.modules["priority_weights"] = body
    audit_phi(request, user, action="update", resource_type="priority_weights", resource_id=None)
    store.touch()
    return body.model_dump()


@router.get("/noshow-model")
def noshow_model(user: StaffUser = Depends(VIEWERS)):
    nsm = get_model(service.get_store_ready())
    return {"auc": nsm.auc, "base_rate": nsm.base_rate, "n_train": nsm.n_train, "n_test": nsm.n_test,
            "coefficients": nsm.coefficients, "high_risk_threshold": HIGH_RISK,
            "trained_at": nsm.trained_at.isoformat(timespec="seconds"),
            "note": "Logistic regression on synthetic history; AUC measured on the most recent 25% of appointments."}
