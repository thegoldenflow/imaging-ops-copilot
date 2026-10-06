"""Scheduling Command Center logic: utilization, cancellation backfill,
cross-site load balancing and priority ranking."""

import math
import time
from datetime import date, datetime, timedelta
from typing import Callable

from pydantic import BaseModel, Field

from app.core.models import ACTIVE_STATUSES, Appointment, AppointmentStatus, Modality, WaitlistEntry
from app.core.store import Store, get_store
from app.core.templates import format_when, render
from app.integrations.mocks import queue_message
from app.modules.scheduling.noshow import HIGH_RISK, get_model

URGENCY_RANK = {"P1": 1, "P2": 2, "P3": 3, "P4": 4}
BOOKED_FOR_UTILIZATION = ACTIVE_STATUSES | {AppointmentStatus.COMPLETED, AppointmentStatus.NO_SHOW}

# Called with (appointment) after a booking or cancellation. Front desk
# registers reminders, pre-registration and prep instructions here.
BOOKING_HOOKS: list[Callable[[Appointment], None]] = []
CANCEL_HOOKS: list[Callable[[Appointment], None]] = []
# Each guard returns a reason the appointment may not be confirmed yet, or None.
CONFIRM_GUARDS: list[Callable[[Store, Appointment], str | None]] = []


def confirm_blockers(store: Store, appt: Appointment) -> list[str]:
    return [reason for guard in CONFIRM_GUARDS if (reason := guard(store, appt))]


class PriorityWeights(BaseModel):
    """Clinical urgency always ranks first; these weights order patients within a tier."""

    wait_time: float = Field(0.5, ge=0, le=1)
    exam_value: float = Field(0.3, ge=0, le=1)
    key_referrer: float = Field(0.2, ge=0, le=1)


class Candidate(BaseModel):
    waitlist_id: str
    patient_id: str
    patient_name: str
    language: str
    exam_code: str
    exam_name: str
    urgency: str
    wait_days: float
    acceptable_sites: list[str]
    key_referrer: bool
    score: float
    breakdown: dict[str, float]
    eligible: bool
    exclusion: str | None = None


class Offer(BaseModel):
    id: str
    case_id: str
    waitlist_id: str
    patient_id: str
    patient_name: str
    language: str
    message_id: str
    status: str = "pending"  # pending, accepted, expired, declined
    sent_at: datetime
    responded_at: datetime | None = None


class Slot(BaseModel):
    scanner_id: str
    site_id: str
    modality: Modality
    start: datetime
    end: datetime


class BackfillCase(BaseModel):
    id: str
    created_at: datetime
    source_appointment_id: str
    slot: Slot
    candidates: list[Candidate]
    excluded: list[Candidate]
    offers: list[Offer] = []
    status: str = "open"  # open, offered, filled, expired
    new_appointment_id: str | None = None
    filled_by_patient_id: str | None = None
    compute_ms: int = 0


def weights(store: Store) -> PriorityWeights:
    return store.module("priority_weights", PriorityWeights)


def cases(store: Store) -> dict[str, BackfillCase]:
    return store.module("backfill_cases", dict)


def offers(store: Store) -> dict[str, Offer]:
    return store.module("offers", dict)


# ---------- Utilization ----------

def _open_minutes(store: Store, scanner_id: str, day: date) -> int:
    site = store.sites[store.scanners[scanner_id].site_id]
    if day.weekday() not in site.open_days:
        return 0
    return (site.close_hour - site.open_hour) * 60


def utilization(store: Store, start_day: date, days: int, site_ids: list[str] | None = None) -> dict:
    end_day = start_day + timedelta(days=days)
    scanners = [sc for sc in store.scanners.values() if not site_ids or sc.site_id in site_ids]
    booked: dict[str, int] = {sc.id: 0 for sc in scanners}
    for appt in store.appointments.values():
        if appt.scanner_id in booked and start_day <= appt.start.date() < end_day \
                and appt.status in BOOKED_FOR_UTILIZATION:
            booked[appt.scanner_id] += int((appt.end - appt.start).total_seconds() // 60)
    scanner_rows = []
    for sc in scanners:
        open_min = sum(_open_minutes(store, sc.id, start_day + timedelta(days=d)) for d in range(days))
        scanner_rows.append({
            "scanner_id": sc.id, "site_id": sc.site_id, "modality": sc.modality, "name": sc.name,
            "booked_min": booked[sc.id], "open_min": open_min,
            "idle_min": max(open_min - booked[sc.id], 0),
            "utilization": round(booked[sc.id] / open_min, 3) if open_min else 0.0,
        })
    site_rows = []
    for site in store.sites.values():
        rows = [r for r in scanner_rows if r["site_id"] == site.id]
        if not rows:
            continue
        b, o = sum(r["booked_min"] for r in rows), sum(r["open_min"] for r in rows)
        site_rows.append({"site_id": site.id, "name": site.name, "booked_min": b, "open_min": o,
                          "idle_min": max(o - b, 0), "utilization": round(b / o, 3) if o else 0.0})
    return {"sites": site_rows, "scanners": scanner_rows}


def heatmap(store: Store, day: date, site_ids: list[str] | None = None) -> list[dict]:
    """Utilization by site x hour for one day."""
    cells: dict[tuple[str, int], list[int]] = {}
    for sc in store.scanners.values():
        site = store.sites[sc.site_id]
        if (site_ids and site.id not in site_ids) or day.weekday() not in site.open_days:
            continue
        for hour in range(site.open_hour, site.close_hour):
            cells.setdefault((site.id, hour), [0, 0])[1] += 60
    for appt in store.appointments.values():
        if appt.start.date() != day or appt.status not in BOOKED_FOR_UTILIZATION:
            continue
        key = (appt.site_id, appt.start.hour)
        if key in cells:
            cells[key][0] += int((appt.end - appt.start).total_seconds() // 60)
    return [{"site_id": s, "hour": h, "utilization": round(min(b / o, 1.0), 3)} for (s, h), (b, o) in sorted(cells.items())]


# ---------- Waitlist ranking ----------

def rank_waitlist(store: Store, slot: Slot, now: datetime | None = None) -> tuple[list[Candidate], list[Candidate]]:
    now = now or datetime.now()
    w = weights(store)
    slot_minutes = (slot.end - slot.start).total_seconds() / 60
    hours_until = (slot.start - now).total_seconds() / 3600
    eligible, excluded = [], []
    for entry in store.waitlist.values():
        exam = store.exams[entry.exam_code]
        if not entry.active or exam.modality != slot.modality or (entry.duration_minutes or exam.minutes) > slot_minutes:
            continue
        patient = store.patients[entry.patient_id]
        referrer = store.referrers.get(entry.referrer_id)
        wait_days = (now - entry.added_at).total_seconds() / 86400
        parts = {
            "wait_time": round(w.wait_time * min(wait_days / 30, 1.0), 3),
            "exam_value": round(w.exam_value * exam.value / 5, 3),
            "key_referrer": round(w.key_referrer * (1.0 if referrer and referrer.is_key else 0.0), 3),
        }
        exclusion = None
        if slot.site_id not in entry.acceptable_site_ids:
            exclusion = f"Site not acceptable (prefers {', '.join(entry.acceptable_site_ids)})"
        elif entry.earliest_date > slot.start.date():
            exclusion = f"Not ready until {entry.earliest_date:%b %d}"
        elif exam.prep_hours > hours_until:
            exclusion = f"Needs {exam.prep_hours}h preparation notice"
        candidate = Candidate(
            waitlist_id=entry.id, patient_id=patient.id, patient_name=patient.full_name,
            language=patient.preferred_language, exam_code=exam.code, exam_name=exam.name,
            urgency=entry.urgency, wait_days=round(wait_days, 1), acceptable_sites=entry.acceptable_site_ids,
            key_referrer=bool(referrer and referrer.is_key), score=round(sum(parts.values()), 3),
            breakdown=parts, eligible=exclusion is None, exclusion=exclusion,
        )
        (eligible if exclusion is None else excluded).append(candidate)
    order = lambda c: (URGENCY_RANK[c.urgency], -c.score)  # noqa: E731
    eligible.sort(key=order)
    excluded.sort(key=order)
    return eligible, excluded


# ---------- Booking, cancellation and backfill ----------

def book(store: Store, *, patient_id: str, referrer_id: str, scanner_id: str, exam_code: str,
         start: datetime, urgency: str, duration_minutes: int | None = None, requisition_id: str | None = None,
         protocol_id: str | None = None) -> Appointment:
    scanner = store.scanners[scanner_id]
    exam = store.exams[exam_code]
    appt = Appointment(
        id=store.next_id("AP"), patient_id=patient_id, referrer_id=referrer_id, site_id=scanner.site_id,
        scanner_id=scanner_id, exam_code=exam_code, start=start,
        end=start + timedelta(minutes=duration_minutes or exam.minutes),
        status=AppointmentStatus.BOOKED, urgency=urgency, booked_at=datetime.now(),
        requisition_id=requisition_id, protocol_id=protocol_id,
    )
    store.appointments[appt.id] = appt
    nsm = store.modules.get("noshow")
    if nsm is not None:
        from app.modules.scheduling.noshow import score_upcoming
        score_upcoming(store, nsm)
    for hook in BOOKING_HOOKS:
        hook(appt)
    store.touch()
    return appt


def cancel(store: Store, appt: Appointment, reason: str, now: datetime | None = None) -> BackfillCase | None:
    """Cancel and, for a future slot, open a backfill case with ranked candidates."""
    now = now or datetime.now()
    started = time.monotonic()
    appt.status = AppointmentStatus.CANCELLED
    appt.cancel_reason = reason
    for hook in CANCEL_HOOKS:
        hook(appt)
    case = None
    if appt.start > now:
        scanner = store.scanners[appt.scanner_id]
        slot = Slot(scanner_id=scanner.id, site_id=scanner.site_id, modality=scanner.modality,
                    start=appt.start, end=appt.end)
        eligible, excluded = rank_waitlist(store, slot, now)
        case = BackfillCase(
            id=store.next_id("BF"), created_at=now, source_appointment_id=appt.id, slot=slot,
            candidates=eligible[:10], excluded=excluded[:6],
        )
        case.compute_ms = int((time.monotonic() - started) * 1000)
        cases(store)[case.id] = case
    store.touch()
    return case


def send_offers(store: Store, case: BackfillCase, waitlist_ids: list[str]) -> list[Offer]:
    sent = []
    site = store.sites[case.slot.site_id]
    for wid in waitlist_ids:
        candidate = next((c for c in case.candidates if c.waitlist_id == wid), None)
        if candidate is None or any(o.waitlist_id == wid for o in case.offers):
            continue
        patient = store.patients[candidate.patient_id]
        msg = queue_message(
            channel="sms", kind="waitlist_offer", to=patient.phone, language=patient.preferred_language,
            patient_id=patient.id,
            body=render("waitlist_offer", patient.preferred_language, exam=candidate.exam_name,
                        when=format_when(case.slot.start, patient.preferred_language), site=site.name),
        )
        offer = Offer(id=store.next_id("OF"), case_id=case.id, waitlist_id=wid, patient_id=patient.id,
                      patient_name=patient.full_name, language=patient.preferred_language, message_id=msg.id,
                      sent_at=datetime.now())
        case.offers.append(offer)
        offers(store)[offer.id] = offer
        sent.append(offer)
    if sent and case.status == "open":
        case.status = "offered"
    store.touch()
    return sent


class OfferConflict(Exception):
    pass


def accept_offer(store: Store, offer: Offer) -> Appointment:
    """First patient to confirm gets the slot; later replies are told it is filled."""
    case = cases(store)[offer.case_id]
    if offer.status in ("accepted", "declined"):
        raise OfferConflict(f"Offer is already {offer.status}")
    patient = store.patients[offer.patient_id]
    if case.status == "filled":
        offer.status, offer.responded_at = "expired", datetime.now()
        queue_message(channel="sms", kind="offer_filled", to=patient.phone, language=patient.preferred_language,
                      patient_id=patient.id,
                      body=render("offer_filled", patient.preferred_language,
                                  exam=store.exams[store.waitlist[offer.waitlist_id].exam_code].name,
                                  when=format_when(case.slot.start, patient.preferred_language)))
        raise OfferConflict("Slot already filled by another patient")
    entry: WaitlistEntry = store.waitlist[offer.waitlist_id]
    appt = book(store, patient_id=patient.id, referrer_id=entry.referrer_id, scanner_id=case.slot.scanner_id,
                exam_code=entry.exam_code, start=case.slot.start, urgency=entry.urgency,
                duration_minutes=entry.duration_minutes, requisition_id=entry.requisition_id,
                protocol_id=entry.protocol_id)
    entry.active = False
    if entry.requisition_id and entry.requisition_id in store.requisitions:
        req = store.requisitions[entry.requisition_id]
        req.status, req.appointment_id = "booked", appt.id
    offer.status, offer.responded_at = "accepted", datetime.now()
    case.status, case.new_appointment_id, case.filled_by_patient_id = "filled", appt.id, patient.id
    for other in case.offers:
        if other.id != offer.id and other.status == "pending":
            other.status = "expired"
    store.touch()
    return appt


# ---------- Cross-site load balancing ----------

def cross_site(store: Store, days: int = 7) -> dict:
    today = datetime.now().date() + timedelta(days=1)
    util = {row["site_id"]: row["utilization"] for row in utilization(store, today, days)["sites"]}
    status = {sid: ("full" if u >= 0.85 else "available" if u <= 0.65 else "balanced") for sid, u in util.items()}
    suggestions = []
    for entry in store.waitlist.values():
        if not entry.active:
            continue
        exam = store.exams[entry.exam_code]
        if not entry.acceptable_site_ids or not all(status.get(s) == "full" for s in entry.acceptable_site_ids):
            continue
        origin = store.sites[entry.acceptable_site_ids[0]]
        options = [
            site for site in store.sites.values()
            if status.get(site.id) == "available" and exam.modality in site.modalities
        ]
        if not options:
            continue
        best = min(options, key=lambda s: math.dist((s.x_km, s.y_km), (origin.x_km, origin.y_km)))
        patient = store.patients[entry.patient_id]
        suggestions.append({
            "waitlist_id": entry.id, "patient_id": patient.id, "patient_name": patient.full_name,
            "exam_name": exam.name, "urgency": entry.urgency, "current_sites": entry.acceptable_site_ids,
            "suggested_site_id": best.id, "suggested_site": best.name,
            "distance_km": round(math.dist((best.x_km, best.y_km), (origin.x_km, origin.y_km)), 1),
            "suggested_site_utilization": util[best.id],
        })
    suggestions.sort(key=lambda s: (URGENCY_RANK[s["urgency"]], s["distance_km"]))
    return {
        "window_days": days,
        "sites": [{"site_id": sid, "name": store.sites[sid].name, "utilization": u, "status": status[sid]}
                  for sid, u in util.items()],
        "suggestions": suggestions,
    }


def queue_extra_reminders(store: Store, now: datetime | None = None) -> int:
    """High no-show risk appointments get one extra reminder 48h before."""
    now = now or datetime.now()
    get_model(store)
    count = 0
    for appt in store.appointments.values():
        if appt.status not in ACTIVE_STATUSES or appt.extra_reminder or (appt.no_show_risk or 0) < HIGH_RISK:
            continue
        if appt.start - now > timedelta(days=7):
            continue
        patient = store.patients[appt.patient_id]
        queue_message(
            channel="sms", kind="reminder_extra", to=patient.phone, language=patient.preferred_language,
            patient_id=patient.id, appointment_id=appt.id,
            scheduled_for=max(appt.start - timedelta(hours=48), now),
            body=render("reminder", patient.preferred_language, exam=store.exams[appt.exam_code].name,
                        when=format_when(appt.start, patient.preferred_language), site=store.sites[appt.site_id].name),
        )
        appt.extra_reminder = True
        count += 1
    return count


def get_store_ready() -> Store:
    store = get_store()
    if "extra_reminders_done" not in store.modules:
        queue_extra_reminders(store)
        store.modules["extra_reminders_done"] = True
    return store
