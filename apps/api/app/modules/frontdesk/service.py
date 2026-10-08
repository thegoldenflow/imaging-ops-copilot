"""Reminders, pre-registration, call logs and SMS reply handling."""

import secrets
from datetime import datetime, timedelta

from pydantic import BaseModel

from app.core.models import ACTIVE_STATUSES, Appointment, AppointmentStatus
from app.core.store import Store, get_store
from app.core.templates import format_when, render
from app.integrations.mocks import CoverageResult, adapter, queue_message
from app.llm.gateway import get_gateway
from app.llm.prompts import CALL_SUMMARY
from app.llm.providers import mock_fixture
from app.modules.frontdesk.tools import CallSession
from app.modules.scheduling import service as scheduling

PUBLIC_BASE = "/prereg"


class PreRegistration(BaseModel):
    token: str
    appointment_id: str
    patient_id: str
    status: str = "sent"  # sent, completed
    submitted_at: datetime | None = None
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    preferred_language: str | None = None
    insurance_type: str | None = None  # ohip, private
    coverage: CoverageResult | None = None
    consent: bool = False


def calls(store: Store) -> dict[str, CallSession]:
    return store.module("calls", dict)


def preregs(store: Store) -> dict[str, PreRegistration]:
    return store.module("prereg", dict)


def prereg_for(store: Store, appointment_id: str) -> PreRegistration | None:
    return next((p for p in preregs(store).values() if p.appointment_id == appointment_id), None)


# ---------- Booking / cancellation hooks ----------

def on_booked(appt: Appointment) -> None:
    store = get_store()
    patient = store.patients[appt.patient_id]
    site = store.sites[appt.site_id]
    exam = store.exams[appt.exam_code]
    lang = patient.preferred_language
    now = datetime.now()
    when = format_when(appt.start, lang)

    token = secrets.token_urlsafe(10)
    preregs(store)[token] = PreRegistration(token=token, appointment_id=appt.id, patient_id=patient.id)
    queue_message(channel="sms", kind="booking_confirmation", to=patient.phone, language=lang,
                  patient_id=patient.id, appointment_id=appt.id,
                  body=render("booking_confirmation", lang, exam=exam.name, when=when, site=site.name,
                              address=site.address, link=f"{PUBLIC_BASE}/{token}"))
    # Prep instructions are sent by the prep module (System 9), approved text only.
    for hours, kind in ((72, "reminder_72h"), (24, "reminder_24h")):
        send_at = appt.start - timedelta(hours=hours)
        if send_at > now:
            queue_message(channel="sms", kind=kind, to=patient.phone, language=lang, patient_id=patient.id,
                          appointment_id=appt.id, scheduled_for=send_at,
                          body=render("reminder", lang, exam=exam.name, when=when, site=site.name))


def on_cancelled(appt: Appointment) -> None:
    store = get_store()
    for msg in store.outbox.values():
        if msg.appointment_id == appt.id and msg.status == "scheduled":
            msg.status = "cancelled"
    patient = store.patients[appt.patient_id]
    lang = patient.preferred_language
    queue_message(channel="sms", kind="cancel_confirmation", to=patient.phone, language=lang,
                  patient_id=patient.id, appointment_id=appt.id,
                  body=render("cancel_confirmation", lang, exam=store.exams[appt.exam_code].name,
                              when=format_when(appt.start, lang)))


scheduling.BOOKING_HOOKS.append(on_booked)
scheduling.CANCEL_HOOKS.append(on_cancelled)


# ---------- Inbound SMS replies ----------

class ReplyResult(BaseModel):
    action: str  # confirmed, blocked, cancelled, offer_accepted, offer_filled, ignored
    detail: str
    appointment_id: str | None = None
    backfill_case_id: str | None = None


def handle_reply(store: Store, message_id: str, text: str) -> ReplyResult:
    msg = store.outbox[message_id]
    adapter(msg.channel).receive("reply", correlation_id=msg.id)  # inbound, under the outbound message's id
    answer = text.strip().upper()
    if msg.kind == "waitlist_offer":
        offer = next((o for o in scheduling.offers(store).values() if o.message_id == msg.id), None)
        if offer is None or answer not in ("YES", "Y", "OUI", "是", "好"):
            return ReplyResult(action="ignored", detail="No matching offer or not a YES reply")
        try:
            appt = scheduling.accept_offer(store, offer)
        except scheduling.OfferConflict as e:
            return ReplyResult(action="offer_filled", detail=str(e))
        return ReplyResult(action="offer_accepted", detail="Slot booked", appointment_id=appt.id)

    appt = store.appointments.get(msg.appointment_id or "")
    if appt is None or appt.status not in ACTIVE_STATUSES:
        return ReplyResult(action="ignored", detail="Appointment is not active")
    if answer in ("C", "CONFIRM", "YES", "确认"):
        if blockers := scheduling.confirm_blockers(store, appt):
            return ReplyResult(action="blocked", detail="; ".join(blockers), appointment_id=appt.id)
        appt.status = AppointmentStatus.CONFIRMED
        appt.reminder_confirmed = True
        store.touch()
        return ReplyResult(action="confirmed", detail="Appointment confirmed", appointment_id=appt.id)
    if answer in ("X", "CANCEL", "取消"):
        case = scheduling.cancel(store, appt, "Patient replied X to reminder")
        return ReplyResult(action="cancelled", detail="Appointment cancelled; slot released for backfill",
                           appointment_id=appt.id, backfill_case_id=case.id if case else None)
    return ReplyResult(action="ignored", detail="Reply not recognized")


# ---------- Call summaries ----------

class CallSummary(BaseModel):
    summary: str
    follow_up_needed: bool


@mock_fixture("call_summary")
def _mock_summary(text: str, images: list, attempt: int) -> dict:
    actions = text.split("Actions taken:", 1)[-1].strip()
    follow_up = "transfer_to_human" in actions
    if actions in ("", "none"):
        summary = "Caller spoke with the automated assistant; no changes were made."
    else:
        summary = f"Automated assistant handled the call. Actions: {actions}."
    return {"summary": summary, "follow_up_needed": follow_up}


def summarize_call(store: Store, session: CallSession) -> None:
    transcript = "\n".join(f"{t.role}: {t.text}" for t in session.transcript if t.role != "tool")
    actions = ", ".join(a["tool"] for a in session.actions) or "none"
    patients = [store.patients[session.verified_patient_id]] if session.verified_patient_id else []
    outcome = get_gateway().structured(
        task="call_summary", prompt=CALL_SUMMARY, variables={"transcript": transcript, "actions": actions},
        schema_cls=CallSummary, tier="fast", patients=patients,
    )
    if outcome.status == "ok":
        session.summary = outcome.data["summary"]
    else:
        session.summary = f"Summary unavailable ({outcome.status}). Actions: {actions}."
