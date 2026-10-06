"""System 4 · Front Desk Automation API, plus the public pre-registration form."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.core.auth import CLINICAL_STAFF, audit_phi, require_roles
from app.core.models import Role, StaffUser
from app.core.store import get_store
from app.core.templates import LANGUAGES, format_when
from app.integrations.mocks import validate_health_card, verify_private_insurance
from app.modules.frontdesk import service
from app.modules.prep import service as prep
from app.modules.frontdesk.agent import agent_reply, new_session
from app.modules.frontdesk.tools import Turn

router = APIRouter(tags=["frontdesk"])

FRONT = require_roles(Role.FRONT_DESK, Role.OPERATIONS_MANAGER, Role.ADMIN)
VIEWERS = require_roles(*CLINICAL_STAFF)


# ---------- Voice / text calls ----------

@router.post("/api/frontdesk/calls")
def start_call(user: StaffUser = Depends(FRONT)):
    store = get_store()
    session = new_session(store)
    service.calls(store)[session.id] = session
    return session.model_dump(mode="json")


class TurnRequest(BaseModel):
    text: str


@router.post("/api/frontdesk/calls/{call_id}/turn")
def call_turn(call_id: str, body: TurnRequest, user: StaffUser = Depends(FRONT)):
    store = get_store()
    session = service.calls(store).get(call_id)
    if session is None:
        raise HTTPException(404, "Call not found")
    if session.ended_at:
        raise HTTPException(409, "Call has ended")
    text = body.text.strip()[:500]
    if not text:
        raise HTTPException(422, "Empty utterance")
    session.transcript.append(Turn(role="caller", text=text))
    reply, mode = agent_reply(store, session, text)
    session.transcript.append(Turn(role="agent", text=reply))
    ended = session.outcome == "transferred" or session.state.get("ended", False)
    if ended:
        _end(store, session)
    store.touch()
    return {"reply": reply, "mode": mode, "latency_ms": session.latencies_ms[-1], "ended": ended,
            "session": session.model_dump(mode="json")}


def _end(store, session) -> None:
    if session.ended_at:
        return
    session.ended_at = datetime.now()
    if session.outcome == "in_progress":
        session.outcome = "abandoned"
    service.summarize_call(store, session)


@router.post("/api/frontdesk/calls/{call_id}/end")
def end_call(call_id: str, user: StaffUser = Depends(FRONT)):
    store = get_store()
    session = service.calls(store).get(call_id)
    if session is None:
        raise HTTPException(404, "Call not found")
    _end(store, session)
    store.touch()
    return session.model_dump(mode="json")


@router.get("/api/frontdesk/calls")
def list_calls(request: Request, user: StaffUser = Depends(FRONT)):
    store = get_store()
    items = sorted(service.calls(store).values(), key=lambda c: c.started_at, reverse=True)
    out = []
    for c in items:
        patient = store.patients.get(c.verified_patient_id or "")
        out.append({**c.model_dump(mode="json"), "patient_name": patient.full_name if patient else None})
    audit_phi(request, user, action="read", resource_type="call_log", resource_id=None)
    return {"calls": out}


# ---------- Outbox (mock SMS / email) ----------

@router.get("/api/frontdesk/outbox")
def outbox(request: Request, kind: str | None = None, status: str | None = None, limit: int = 100,
           user: StaffUser = Depends(VIEWERS)):
    store = get_store()
    items = [m for m in store.outbox.values()
             if (not kind or m.kind == kind) and (not status or m.status == status)]
    items.sort(key=lambda m: m.id, reverse=True)  # newest first
    rows = []
    for m in items[:limit]:
        patient = store.patients.get(m.patient_id or "")
        rows.append({**m.model_dump(mode="json"), "patient_name": patient.full_name if patient else None})
    audit_phi(request, user, action="read", resource_type="outbox", resource_id=None)
    return {"messages": rows, "total": len(items)}


@router.post("/api/frontdesk/outbox/{message_id}/send-now")
def send_now(message_id: str, user: StaffUser = Depends(VIEWERS)):
    """Demo control: deliver a scheduled message immediately (fast-forward time)."""
    store = get_store()
    msg = store.outbox.get(message_id)
    if msg is None:
        raise HTTPException(404, "Message not found")
    if msg.status == "scheduled":
        msg.status, msg.sent_at = "sent", datetime.now()
        store.touch()
    return msg.model_dump(mode="json")


class ReplyRequest(BaseModel):
    text: str


@router.post("/api/frontdesk/outbox/{message_id}/reply")
def patient_reply(message_id: str, body: ReplyRequest, request: Request, user: StaffUser = Depends(VIEWERS)):
    """Demo control: simulates the patient texting back (C, X, YES)."""
    store = get_store()
    if message_id not in store.outbox:
        raise HTTPException(404, "Message not found")
    result = service.handle_reply(store, message_id, body.text)
    audit_phi(request, user, action="update", resource_type="sms_reply", resource_id=message_id)
    return result.model_dump()


# ---------- Pre-registration status for staff ----------

@router.get("/api/frontdesk/preregistrations")
def list_preregs(request: Request, user: StaffUser = Depends(VIEWERS)):
    store = get_store()
    rows = []
    for p in service.preregs(store).values():
        appt = store.appointments.get(p.appointment_id)
        patient = store.patients[p.patient_id]
        rows.append({**p.model_dump(mode="json"), "patient_name": patient.full_name,
                     "exam_name": store.exams[appt.exam_code].name if appt else None,
                     "start": appt.start.isoformat() if appt else None})
    audit_phi(request, user, action="read", resource_type="preregistration_list", resource_id=None)
    return {"preregistrations": rows}


# ---------- Public patient pages (token links, no staff login) ----------

def _prereg_or_404(token: str):
    store = get_store()
    reg = service.preregs(store).get(token)
    if reg is None:
        raise HTTPException(404, "This link is invalid or has expired")
    return store, reg


@router.get("/api/public/prereg/{token}")
def public_prereg(token: str):
    store, reg = _prereg_or_404(token)
    appt = store.appointments[reg.appointment_id]
    patient = store.patients[reg.patient_id]
    site = store.sites[appt.site_id]
    lang = reg.preferred_language or patient.preferred_language
    return {
        "status": reg.status,
        "first_name": patient.given_name,
        "language": lang,
        "languages": LANGUAGES,
        "appointment": {
            "exam": store.exams[appt.exam_code].name,
            "when": format_when(appt.start, lang),
            "site": site.name,
            "address": site.address,
            "status": appt.status,
        },
        "prep": prep.message_text(store, appt.exam_code, lang)[0],
    }


class PreRegForm(BaseModel):
    phone: str
    email: str
    address: str
    preferred_language: str
    insurance_type: str  # ohip, private
    health_card: str | None = None
    health_card_version: str | None = None
    insurer: str | None = None
    policy_number: str | None = None
    consent: bool


@router.post("/api/public/prereg/{token}")
def submit_prereg(token: str, form: PreRegForm, request: Request):
    store, reg = _prereg_or_404(token)
    if not form.consent:
        raise HTTPException(422, "Consent is required")
    if form.preferred_language not in LANGUAGES:
        raise HTTPException(422, "Unsupported language")
    if form.insurance_type == "ohip":
        coverage = validate_health_card(form.health_card or "", form.health_card_version or "")
    elif form.insurance_type == "private":
        coverage = verify_private_insurance(form.insurer or "", form.policy_number or "")
    else:
        raise HTTPException(422, "Choose OHIP or private insurance")
    patient = store.patients[reg.patient_id]
    patient.phone, patient.email, patient.address = form.phone, form.email, form.address
    patient.preferred_language = form.preferred_language
    reg.phone, reg.email, reg.address = form.phone, form.email, form.address
    reg.preferred_language, reg.insurance_type, reg.coverage, reg.consent = (
        form.preferred_language, form.insurance_type, coverage, True)
    reg.status, reg.submitted_at = "completed", datetime.now()
    store.audit.record(user_id=f"patient:{patient.id}", user_name="Patient (self-service)", role="patient",
                       action="update", resource_type="preregistration", resource_id=token,
                       source_ip=request.client.host if request.client else None, reason="pre-registration")
    store.touch()
    return {"status": reg.status, "coverage": coverage.model_dump()}
