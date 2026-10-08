"""Tools the front-desk voice agent can call. Identity verification is enforced
here, on the server, not just in the prompt."""

import re
from datetime import date, datetime, timedelta

from pydantic import BaseModel, Field

from app.core.models import ACTIVE_STATUSES, Appointment
from app.core.store import Store
from app.core.templates import prep_text
from app.modules.scheduling import service as scheduling

MONTHS = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
     "november", "december"])}


class Turn(BaseModel):
    role: str  # caller, agent, tool
    text: str
    ts: datetime = Field(default_factory=datetime.now)


class CallSession(BaseModel):
    id: str
    started_at: datetime
    ended_at: datetime | None = None
    agent_mode: str  # claude, gemini (LLM tool use) or scripted
    verified_patient_id: str | None = None
    failed_verifications: int = 0
    transcript: list[Turn] = []
    actions: list[dict] = []
    outcome: str = "in_progress"  # in_progress, resolved, transferred, abandoned
    summary: str | None = None
    state: dict = {}
    latencies_ms: list[int] = []


def parse_dob(text: str) -> date | None:
    t = text.lower().replace(",", " ")
    t = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", t)
    if m := re.search(r"\b(19\d{2}|20\d{2})-(\d{1,2})-(\d{1,2})\b", t):
        y, mo, d = map(int, m.groups())
    elif m := re.search(r"\b(\d{1,2})/(\d{1,2})/(19\d{2}|20\d{2})\b", t):
        mo, d, y = map(int, m.groups())
    elif m := re.search(r"\b([a-z]+)\s+(\d{1,2})\s+(19\d{2}|20\d{2})\b", t):
        if m.group(1) not in MONTHS:
            return None
        mo, d, y = MONTHS[m.group(1)], int(m.group(2)), int(m.group(3))
    elif m := re.search(r"\b(\d{1,2})\s+([a-z]+)\s+(19\d{2}|20\d{2})\b", t):
        if m.group(2) not in MONTHS:
            return None
        d, mo, y = int(m.group(1)), MONTHS[m.group(2)], int(m.group(3))
    elif m := re.search(r"(19\d{2}|20\d{2})年(\d{1,2})月(\d{1,2})", text):
        y, mo, d = map(int, m.groups())
    else:
        return None
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def find_named_patients(store: Store, text: str) -> list[str]:
    lowered = text.lower()
    return [p.id for p in store.patients.values() if p.full_name.lower() in lowered]


class ToolError(Exception):
    pass


def _require_verified(session: CallSession) -> str:
    if not session.verified_patient_id:
        raise ToolError("Caller identity is not verified. Ask for full name and date of birth first.")
    return session.verified_patient_id


def _appt_summary(store: Store, a: Appointment) -> dict:
    return {
        "appointment_id": a.id,
        "exam": store.exams[a.exam_code].name,
        "when": a.start.strftime("%A %B %d at %H:%M"),
        "site": store.sites[a.site_id].name,
        "status": a.status,
    }


def _own_appointment(store: Store, session: CallSession, appointment_id: str) -> Appointment:
    pid = _require_verified(session)
    appt = store.appointments.get(appointment_id)
    if appt is None or appt.patient_id != pid:
        raise ToolError("No such appointment for this caller.")
    if appt.status not in ACTIVE_STATUSES:
        raise ToolError(f"That appointment is {appt.status}.")
    return appt


def verify_identity(store: Store, session: CallSession, full_name: str, date_of_birth: str) -> dict:
    dob = parse_dob(date_of_birth)
    # Names and birth dates are encrypted at rest: look up by the birth date's blind index, then compare names.
    said = full_name.lower()
    matches = [p for p in store.patients.find_by("dob", dob) if p.full_name.lower() in said] if dob else []
    if len(matches) == 1:
        session.verified_patient_id = matches[0].id
        return {"verified": True, "first_name": matches[0].given_name}
    session.failed_verifications += 1
    return {"verified": False, "attempts_left": max(0, 3 - session.failed_verifications)}


def lookup_appointment(store: Store, session: CallSession) -> dict:
    pid = _require_verified(session)
    now = datetime.now()
    upcoming = sorted((a for a in store.appointments.values()
                       if a.patient_id == pid and a.status in ACTIVE_STATUSES and a.start > now),
                      key=lambda a: a.start)
    return {"appointments": [_appt_summary(store, a) for a in upcoming[:3]]}


def find_open_slots(store: Store, session: CallSession, appointment_id: str, count: int = 3) -> dict:
    appt = _own_appointment(store, session, appointment_id)
    scanner = store.scanners[appt.scanner_id]
    site = store.sites[scanner.site_id]
    minutes = store.exams[appt.exam_code].minutes
    busy = [(a.start, a.end) for a in store.appointments.values()
            if a.scanner_id == scanner.id and a.status in ACTIVE_STATUSES]
    slots = []
    start_day = datetime.now().date() + timedelta(days=1)
    for offset in range(0, 10):
        day = start_day + timedelta(days=offset)
        if day.weekday() not in site.open_days:
            continue
        t = datetime.combine(day, datetime.min.time()) + timedelta(hours=site.open_hour)
        close = datetime.combine(day, datetime.min.time()) + timedelta(hours=site.close_hour)
        while t + timedelta(minutes=minutes) <= close and len(slots) < count:
            end = t + timedelta(minutes=minutes)
            if t != appt.start and not any(s < end and e > t for s, e in busy) and 8 <= t.hour < 17:
                slots.append({"slot_id": f"{scanner.id}|{t.isoformat(timespec='minutes')}",
                              "when": t.strftime("%A %B %d at %H:%M")})
                t += timedelta(hours=3)  # spread the options out
                continue
            t += timedelta(minutes=scanner.slot_minutes)
        if len(slots) >= count:
            break
    session.state["offered_slots"] = slots
    return {"slots": slots, "site": site.name}


def reschedule(store: Store, session: CallSession, appointment_id: str, slot_id: str) -> dict:
    appt = _own_appointment(store, session, appointment_id)
    try:
        scanner_id, iso = slot_id.split("|")
        start = datetime.fromisoformat(iso)
    except ValueError as e:
        raise ToolError("Unknown slot id. Call find_open_slots first.") from e
    if scanner_id != appt.scanner_id:
        raise ToolError("Slot is for a different scanner.")
    case = scheduling.cancel(store, appt, "Rescheduled by patient (phone)")
    new = scheduling.book(store, patient_id=appt.patient_id, referrer_id=appt.referrer_id, scanner_id=scanner_id,
                          exam_code=appt.exam_code, start=start, urgency=appt.urgency)
    session.actions.append({"tool": "reschedule", "old": appt.id, "new": new.id,
                            "backfill_case": case.id if case else None})
    return {"rescheduled": True, "new_appointment": _appt_summary(store, new)}


def cancel(store: Store, session: CallSession, appointment_id: str, reason: str = "Patient request (phone)") -> dict:
    appt = _own_appointment(store, session, appointment_id)
    case = scheduling.cancel(store, appt, reason)
    session.actions.append({"tool": "cancel", "appointment": appt.id, "backfill_case": case.id if case else None})
    return {"cancelled": True, "appointment": _appt_summary(store, appt)}


def get_prep_instructions(store: Store, session: CallSession, appointment_id: str) -> dict:
    appt = _own_appointment(store, session, appointment_id)
    patient = store.patients[appt.patient_id]
    return {"instructions": prep_text(appt.exam_code, "en"),
            "note": f"Written instructions were also sent in the patient's preferred language ({patient.preferred_language})."}


def get_site_info(store: Store, session: CallSession, site: str = "") -> dict:
    match = next((s for s in store.sites.values() if site and site.lower() in s.name.lower()), None)
    if match is None and session.verified_patient_id:
        upcoming = lookup_appointment(store, session)["appointments"]
        if upcoming:
            match = next(s for s in store.sites.values() if s.name == upcoming[0]["site"])
    match = match or store.sites["LKS"]
    days = "Monday to Saturday" if 5 in match.open_days else "Monday to Friday"
    return {"site": match.name, "address": match.address, "phone": match.phone,
            "hours": f"{days}, {match.open_hour}:00 to {match.close_hour}:00", "parking": match.parking}


def transfer_to_human(store: Store, session: CallSession, reason: str) -> dict:
    session.outcome = "transferred"
    session.actions.append({"tool": "transfer_to_human", "reason": reason})
    return {"transferred": True, "queue": "Front desk", "estimated_wait_minutes": 3}


TOOLS = {
    "verify_identity": verify_identity,
    "lookup_appointment": lookup_appointment,
    "find_open_slots": find_open_slots,
    "reschedule": reschedule,
    "cancel": cancel,
    "get_prep_instructions": get_prep_instructions,
    "get_site_info": get_site_info,
    "transfer_to_human": transfer_to_human,
}


def _schema(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


TOOL_DEFINITIONS = [
    {"name": "verify_identity", "description": "Verify the caller with full name and date of birth. Required before any appointment details.",
     "input_schema": _schema({"full_name": {"type": "string"}, "date_of_birth": {"type": "string"}}, ["full_name", "date_of_birth"])},
    {"name": "lookup_appointment", "description": "List the verified caller's upcoming appointments.",
     "input_schema": _schema({}, [])},
    {"name": "find_open_slots", "description": "Find up to three open slots to move an appointment to.",
     "input_schema": _schema({"appointment_id": {"type": "string"}}, ["appointment_id"])},
    {"name": "reschedule", "description": "Move an appointment to a slot from find_open_slots. Confirm with the caller first.",
     "input_schema": _schema({"appointment_id": {"type": "string"}, "slot_id": {"type": "string"}}, ["appointment_id", "slot_id"])},
    {"name": "cancel", "description": "Cancel an appointment. Confirm with the caller first.",
     "input_schema": _schema({"appointment_id": {"type": "string"}, "reason": {"type": "string"}}, ["appointment_id", "reason"])},
    {"name": "get_prep_instructions", "description": "Preparation instructions for an appointment.",
     "input_schema": _schema({"appointment_id": {"type": "string"}}, ["appointment_id"])},
    {"name": "get_site_info", "description": "Address, hours, phone and parking for a site.",
     "input_schema": _schema({"site": {"type": "string"}}, ["site"])},
    {"name": "transfer_to_human", "description": "Transfer the caller to staff. Use for medical questions or anything you cannot handle.",
     "input_schema": _schema({"reason": {"type": "string"}}, ["reason"])},
]


def run_tool(store: Store, session: CallSession, name: str, args: dict) -> dict:
    fn = TOOLS.get(name)
    if fn is None:
        return {"error": f"Unknown tool {name}"}
    try:
        return fn(store, session, **args)
    except ToolError as e:
        return {"error": str(e)}
    except TypeError as e:
        return {"error": f"Bad arguments: {e}"}
