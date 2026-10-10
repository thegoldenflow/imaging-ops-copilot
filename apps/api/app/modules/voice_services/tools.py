"""Tool handlers of the voice services (spec 7.4; config/tools/*.yaml point here).

The Tool Gateway calls these after every check passed; `ctx.fhir` is a FhirGateway for the actor the gateway chose
(the agent itself, the nurse a module acts for, or the approving clerk of a privileged call), so the EHR's
allow-list, role policy, unit scope and audit apply underneath. Errors follow app/agents/handlers.py: LookupError
(not found), ValueError (invalid), FhirAccessDenied (refused) and FhirConflict.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from app.agents.handlers import MEDIUM, PARTICIPATION_MODE
from app.core.store import get_store
from app.ehr import access
from app.ehr.clock import hospital_now
from app.ehr.codes import (CALL_KIND, EXT, HCN_SYSTEM, LOINC, OBS_CATEGORY, PATIENT_REPORTED, PATIENT_REPORTED_CODE,
                           PRACTITIONER_ROLE, TASK_CODE, concept)
from app.fhir.dt import fhir_datetime, parse, ref
from app.llm.fhir_deid import _name_spellings
from app.modules.voice_services.phrases import SERVICES

if TYPE_CHECKING:
    from app.agents.gateway import ToolContext

ACTIVE = ("proposed", "pending", "booked")
TASK_DISPLAY = {"call-transfer": "Call transferred to staff", "nurse-line": "Nurse line call-back",
                "followup-escalation": "Follow-up call escalation", "followup-manual": "Manual follow-up",
                "followup-referral": "Follow-up referred to a physician"}
KIND_DISPLAY = {"call-centre": "Call-centre call", "prep-instructions": "Preparation instructions by phone",
                "nurse-line": "Question for the nurse line", "followup-call": "Post-discharge follow-up call",
                "nurse-callback": "Nurse call-back", "nurse-referral": "Referred to a physician",
                "nurse-close": "Follow-up escalation closed"}
SPACES = re.compile(r"\s+")


def _fhir(ctx: ToolContext):
    """The FhirGateway the Tool Gateway opened for this call (the actor it chose)."""
    return ctx.fhir


def _now() -> datetime:
    return hospital_now(get_store())


def _norm(text: str) -> str:
    return SPACES.sub(" ", re.sub(r"[,.;:!?，。：；！？'\"]", " ", (text or "").lower())).strip()


def _refs(resources: list[dict]) -> list[str]:
    return [f"{r['resourceType']}/{r['id']}" for r in resources]


def _patient_of_encounter(ctx: ToolContext, encounter_id: str) -> str:
    found = _fhir(ctx).read("Encounter", encounter_id)
    if found is None:
        raise LookupError(f"Encounter/{encounter_id} not found")
    patient = access.patient_of(found.to_fhir())
    if patient is None:
        raise LookupError(f"Encounter/{encounter_id} has no patient")
    return patient


# ---------- read ----------


def name_matches(patient: dict, said: str) -> bool:
    """A name of the patient, in any of its spellings (official and Chinese), is in what the caller said."""
    said = _norm(said)
    compact = said.replace(" ", "")
    for name in patient.get("name") or []:
        for spelling in _name_spellings(name):
            s = _norm(spelling)
            if s and (s in said if " " in s or s.isascii() else s in compact):
                return True
    return False


def find_caller(ctx: ToolContext, name: str, birth_date: str, card_last4: str) -> dict:
    """All three must match one patient (7.4). The comparison runs here, on the server; the model never sees it."""
    matches = []
    for patient in _fhir(ctx).search("Patient"):
        if patient.get("birthDate") != birth_date:
            continue
        card = next((i.get("value") or "" for i in patient.get("identifier") or [] if i.get("system") == HCN_SYSTEM),
                    "")
        if not card.endswith(card_last4) or not name_matches(patient, name):
            continue
        matches.append(patient)
    if len(matches) != 1:
        return {"verified": False, "patient_id": None, "given_name": None, "language": None}
    p = matches[0]
    official = next((n for n in p.get("name") or [] if n.get("use") == "official"), (p.get("name") or [{}])[0])
    language = next((c.get("code") for comm in p.get("communication") or []
                     for c in (comm.get("language") or {}).get("coding") or []), "en")
    return {"verified": True, "patient_id": p["id"], "given_name": (official.get("given") or [""])[0],
            "language": language, "_refs": [f"Patient/{p['id']}"]}


def _service(appt: dict) -> tuple[str, str]:
    concept_ = (appt.get("serviceType") or [{}])[0]
    code = next((c.get("code") for c in concept_.get("coding") or []), "") or ""
    return code, concept_.get("text") or next((c.get("display") for c in concept_.get("coding") or []), "") or code


def _place(appt: dict) -> str:
    for participant in appt.get("participant") or []:
        actor = participant.get("actor") or {}
        if not (actor.get("reference") or "").startswith("Patient/") and actor.get("display"):
            return actor["display"]
    return ""


def appointment_view(appt: dict) -> dict:
    code, display = _service(appt)
    encounter = next((e.get("valueReference", {}).get("reference", "").split("/")[-1]
                      for e in appt.get("extension") or [] if e.get("url") == EXT + "encounter"), None)
    return {"appointment_id": appt["id"], "service": code, "service_display": display, "start": appt.get("start"),
            "minutes": appt.get("minutesDuration") or 30, "location": _place(appt), "status": appt.get("status"),
            "encounter_id": encounter or None}


def get_patient_appointments(ctx: ToolContext, patient_id: str) -> dict:
    since = _now() - timedelta(hours=1)
    found = [a for a in _fhir(ctx).search("Appointment", patient=patient_id)
             if a.get("status") in ACTIVE and (parse(a.get("start")) or since) >= since]
    found.sort(key=lambda a: a.get("start") or "")
    return {"appointments": [appointment_view(a) for a in found[:20]], "_refs": _refs(found[:20])}


def find_appointment_slots(ctx: ToolContext, appointment_id: str, count: int = 3) -> dict:
    """The first free time on each of the next weekdays in the clinic's hours (the same length), skipping the
    appointment's own time: spread over several days, as a clerk would offer them."""
    found = _fhir(ctx).read("Appointment", appointment_id)
    if found is None:
        raise LookupError(f"Appointment/{appointment_id} not found")
    appt = found.to_fhir()
    code, _ = _service(appt)
    service = SERVICES.get(code)
    if service is None:
        raise ValueError(f"no clinic hours for the service {code!r}")
    minutes = appt.get("minutesDuration") or service["minutes"]
    start_day = (_now() + timedelta(days=1)).date()
    window = (datetime.combine(start_day, datetime.min.time()), datetime.combine(start_day + timedelta(days=15),
                                                                              datetime.min.time()))
    busy = [(parse(a.get("start")), parse(a.get("end"))) for a in _fhir(ctx).search("Appointment", code=code)
            if a.get("status") in ACTIVE and a.get("start") and a.get("end")]
    busy = [(s, e) for s, e in busy if s and e and e > window[0] and s < window[1]]
    own = parse(appt.get("start"))
    slots = []
    for offset in range(14):
        day = start_day + timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        t = datetime.combine(day, datetime.min.time()) + timedelta(hours=service["hours"][0], minutes=30)
        close = datetime.combine(day, datetime.min.time()) + timedelta(hours=service["hours"][1])
        while t + timedelta(minutes=minutes) <= close:
            end = t + timedelta(minutes=minutes)
            if t != own and not any(s < end and e > t for s, e in busy):
                slots.append({"start": fhir_datetime(t), "minutes": minutes})
                break
            t += timedelta(minutes=30)
        if len(slots) >= count:
            break
    return {"slots": slots, "_refs": [f"Appointment/{appointment_id}"]}


# ---------- action ----------


def write_call_record(ctx: ToolContext, patient_id: str, kind: str, direction: str, status: str, text: str,
                      encounter_id: str | None = None, communication_id: str | None = None, language: str | None = None,
                      escalated: bool | None = None, priority: str | None = None, status_reason: str | None = None,
                      about: list[str] | None = None) -> dict:
    now = fhir_datetime(_now())
    if communication_id:
        found = _fhir(ctx).read("Communication", communication_id)
        if found is None:
            raise LookupError(f"Communication/{communication_id} not found")
        comm = found.to_fhir()
        if access.patient_of(comm) != patient_id:
            raise ValueError(f"Communication/{communication_id} is not this patient's call record")
    else:
        code, display = MEDIUM["phone"]
        patient = ref("Patient", patient_id)
        comm = {"resourceType": "Communication", "subject": patient, "medium": [concept(PARTICIPATION_MODE, code,
                                                                                         display)],
                "category": [concept(CALL_KIND, kind, KIND_DISPLAY[kind])]}
        if encounter_id:
            comm["encounter"] = ref("Encounter", encounter_id)
        if direction == "inbound":
            comm["sender"], comm["received"] = patient, now
        else:
            comm["recipient"] = [patient]
    comm["status"] = status
    comm["payload"] = [{"contentString": text}]
    if direction == "outbound" and status == "completed":
        comm["sent"] = now
    if priority:
        comm["priority"] = priority
    if status_reason:
        comm["statusReason"] = {"text": status_reason}
    elif "statusReason" in comm and status != "not-done":
        comm.pop("statusReason")
    if about:
        comm["about"] = [{"reference": r} for r in dict.fromkeys(about)]
    exts = [e for e in comm.get("extension") or [] if e.get("url") not in (EXT + "escalated", EXT + "call-language")]
    if escalated is not None:
        exts.append({"url": EXT + "escalated", "valueBoolean": escalated})
    if language:
        exts.append({"url": EXT + "call-language", "valueCode": language})
    comm["extension"] = exts
    stored = (_fhir(ctx).update if communication_id else _fhir(ctx).create)(ctx.stamp(comm)).to_fhir()
    return {"communication_id": stored["id"], "status": stored["status"], "_refs": [f"Communication/{stored['id']}"]}


def create_call_task(ctx: ToolContext, code: str, description: str, performer_role: str, priority: str = "routine",
                     patient_id: str | None = None, encounter_id: str | None = None, focus: str | None = None,
                     call_id: str | None = None) -> dict:
    if encounter_id and not patient_id:
        patient_id = _patient_of_encounter(ctx, encounter_id)
    now = fhir_datetime(_now())
    task = {"resourceType": "Task", "status": "requested", "intent": "order", "priority": priority,
            "code": concept(TASK_CODE, code, TASK_DISPLAY[code]), "description": description, "authoredOn": now,
            "lastModified": now, "performerType": [concept(PRACTITIONER_ROLE, access.ROLE_CODE[performer_role])]}
    if patient_id:
        task["for"] = ref("Patient", patient_id)
    if encounter_id:
        task["encounter"] = ref("Encounter", encounter_id)
    if focus:
        task["focus"] = {"reference": focus}
    if call_id:
        task["input"] = [{"type": {"text": "call"}, "valueString": call_id}]
    stored = _fhir(ctx).create(ctx.stamp(task)).to_fhir()
    return {"task_id": stored["id"], "status": stored["status"], "_refs": [f"Task/{stored['id']}"]}


def record_patient_reported_observation(ctx: ToolContext, encounter_id: str, code: str, display: str, text: str,
                                        temperature_c: float | None = None, communication_id: str | None = None,
                                        red_flag: bool | None = None) -> dict:
    """What the patient said, as a preliminary patient-reported Observation (not a measurement by staff)."""
    patient = _patient_of_encounter(ctx, encounter_id)
    category = [concept(OBS_CATEGORY, PATIENT_REPORTED, "Patient-reported"),
                concept("http://terminology.hl7.org/CodeSystem/observation-category", "survey", "Survey")]
    obs = {"resourceType": "Observation", "status": "preliminary", "category": category,
           "subject": ref("Patient", patient), "encounter": ref("Encounter", encounter_id),
           "effectiveDateTime": fhir_datetime(_now()), "note": [{"text": text}]}
    if temperature_c is not None:
        obs["code"] = {"coding": [{"system": LOINC, "code": "8310-5", "display": "Body temperature"},
                                  {"system": PATIENT_REPORTED_CODE, "code": code, "display": display}],
                       "text": f"Body temperature (patient-reported): {display}"}
        obs["valueQuantity"] = {"value": temperature_c, "unit": "Cel", "system": "http://unitsofmeasure.org",
                                "code": "Cel"}
    else:
        obs["code"] = concept(PATIENT_REPORTED_CODE, code, display, text=f"{display} (patient-reported)")
        obs["valueString"] = text
    exts = []
    if communication_id:
        exts.append({"url": EXT + "call", "valueReference": ref("Communication", communication_id)})
    if red_flag is not None:
        exts.append({"url": EXT + "red-flag", "valueBoolean": red_flag})
    if exts:
        obs["extension"] = exts
    stored = _fhir(ctx).create(ctx.stamp(obs)).to_fhir()
    return {"observation_id": stored["id"], "status": stored["status"], "_refs": [f"Observation/{stored['id']}"]}


# ---------- privileged (ctx.fhir acts as the approving clerk) ----------


def move_appointment(ctx: ToolContext, appointment_id: str, replaces: str) -> dict:
    booked = _fhir(ctx).book_appointment(appointment_id, replaces=replaces).to_fhir()
    return {"appointment_id": appointment_id, "status": booked["status"], "replaced": replaces, "event_id": None,
            "_refs": [f"Appointment/{appointment_id}", f"Appointment/{replaces}"]}


def cancel_booked_appointment(ctx: ToolContext, appointment_id: str, reason: str) -> dict:
    cancelled = _fhir(ctx).cancel_appointment(appointment_id, reason).to_fhir()
    return {"appointment_id": appointment_id, "status": cancelled["status"], "_refs": [f"Appointment/{appointment_id}"]}
