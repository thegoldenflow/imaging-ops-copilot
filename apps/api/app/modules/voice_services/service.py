"""What the voice-services API shows (spec 7.4): call views, the clerk's requests to confirm, the demo cases."""

from __future__ import annotations

import json

from app.agents import approvals, registry
from app.core.models import StaffUser
from app.core.store import Store
from app.ehr.codes import MRN_SYSTEM
from app.ehr.gateway import Actor, FhirGateway
from app.fhir.dt import parse
from app.modules.voice_services import callcenter, followup, kb
from app.modules.voice_services import phrases as P
from app.modules.voice_services.callcenter import MODULE
from app.modules.voice_services.models import HospitalCall
from app.modules.documentation.phrases import MONTHS_EN
from app.modules.voice_services.tools import appointment_view

REQUEST_TOOLS = ("moveAppointment", "cancelBookedAppointment")


def _mrn(fhir: FhirGateway, patient_id: str | None) -> str | None:
    if not patient_id:
        return None
    found = fhir.read("Patient", patient_id)
    return next((i.value for i in (found.identifier or []) if i.system == MRN_SYSTEM), None) if found else None


def call_view(call: HospitalCall, user: StaffUser | None = None, *, detail: bool = True) -> dict:
    out = call.model_dump(mode="json", exclude={"state"} if detail else {"state", "transcript"})
    out["pending"] = call.state.get("pending")
    out["agent"] = callcenter.acting_agent(callcenter.AGENT)
    out["evaluated"] = (registry.agent(callcenter.AGENT) or registry.agent(MODULE)).evaluated
    if user is not None and call.patient_id:
        out["mrn"] = _mrn(FhirGateway(Actor.of(user), MODULE), call.patient_id)
    return out


def recent_calls(store: Store, user: StaffUser, limit: int = 30) -> list[dict]:
    calls = sorted(callcenter.table(store).values(), key=lambda c: c.started_at, reverse=True)[:limit]
    return [call_view(c, user, detail=False) for c in calls]


def requests(store: Store, user: StaffUser) -> list[dict]:
    """The call centre's requests waiting for a clerk: reschedules and cancellations (approval Tasks)."""
    fhir = FhirGateway(Actor.of(user), MODULE)
    out = []
    for task in approvals.queue(user, limit=100):
        if task.get("tool_id") not in REQUEST_TOOLS or task.get("agent_id") not in (callcenter.AGENT, MODULE):
            continue
        found = fhir.read("Task", task["task_id"])
        raw = found.to_fhir() if found is not None else {}
        args = json.loads(approvals.inputs(raw).get("args") or "{}")
        item = {**task, "args": args, "kind": "reschedule" if task["tool_id"] == "moveAppointment" else "cancel"}
        current = fhir.read("Appointment", args.get("replaces") or args.get("appointment_id"))
        proposed = fhir.read("Appointment", args["appointment_id"]) if task["tool_id"] == "moveAppointment" else None
        if current is not None:
            item["appointment"] = appointment_view(current.to_fhir())
            item["patient_id"] = next((p.actor.reference.split("/")[1] for p in current.participant or []
                                       if p.actor and (p.actor.reference or "").startswith("Patient/")), None)
            item["mrn"] = _mrn(fhir, item["patient_id"])
        if proposed is not None:
            item["proposed"] = appointment_view(proposed.to_fhir())
        for key in ("appointment", "proposed"):
            if item.get(key):
                service = P.SERVICES.get(item[key]["service"])
                item[key]["service_name"] = service["name"]["en"] if service else item[key]["service_display"]
                item[key]["place"] = service["place"]["en"] if service else item[key]["location"]
        out.append(item)
    return out


def faq() -> list[dict]:
    return [{"id": s.id, "topic": s.topic, "keywords": list(s.keywords), "answer": s.answer}
            for s in kb.sections().values()]


def demo_cases(store: Store, user: StaffUser | None = None) -> dict:
    """The planted demo patients (app/ehr/seed/voice.py, ids only), plus what the presenter says to play them: the
    caller's identity line (name, birth date, last four card digits) and the follow-up patient's birth date, read
    through the user's own FhirGateway (audited; nothing the user may not read)."""
    cases = json.loads(json.dumps(store.modules.get("voice_demo") or {}))
    fhir = FhirGateway(Actor.of(user), MODULE) if user is not None else None
    for case in (cases.get("followups") or {}).values():
        call = followup.get(followup.call_id_for(case["stay"]), store)
        case["call"] = {"id": call.id, "status": call.status, "review_status": call.review_status} if call else None
        if (patient := _readable(fhir, case["patient"])) is not None:
            y, m, d = patient["birthDate"].split("-")
            case["birth_date_line"] = {"en": f"{MONTHS_EN[int(m) - 1]} {int(d)}, {y}", "zh": f"{y}年{int(m)}月{int(d)}日"}
    for case in (cases.get("callers") or {}).values():
        if (patient := _readable(fhir, case["patient"])) is not None:
            lines = identity_lines(patient)
            case["identity_line"], case["mrn"] = lines["en"], lines["mrn"]
            case["identity_line_zh"] = lines["zh"]
    return cases


def _readable(fhir: FhirGateway | None, patient_id: str) -> dict | None:
    from app.ehr.gateway import FhirAccessDenied

    if fhir is None:
        return None
    try:
        found = fhir.read("Patient", patient_id)
    except FhirAccessDenied:
        return None
    return found.to_fhir() if found is not None else None


def identity_lines(patient: dict) -> dict:
    """What a caller says to pass the identity check (synthetic demo data)."""
    from app.ehr.codes import HCN_SYSTEM

    official = next((n.get("text") for n in patient.get("name") or [] if n.get("use") == "official"), "")
    usual = next((n.get("text") for n in patient.get("name") or [] if n.get("use") == "usual"), official)
    card = next((i.get("value") or "" for i in patient.get("identifier") or [] if i.get("system") == HCN_SYSTEM), "")
    y, m, d = (patient.get("birthDate") or "0000-00-00").split("-")
    mrn = next((i.get("value") for i in patient.get("identifier") or [] if i.get("system") == MRN_SYSTEM), None)
    return {"en": f"This is {official}, born {patient.get('birthDate')}, card ending {card[-4:]}.",
            "zh": f"我叫{usual}，{y}年{int(m)}月{int(d)}日出生，健康卡尾号{card[-4:]}。", "mrn": mrn}


def when_text(iso: str | None, lang: str = "en") -> str | None:
    dt = parse(iso) if iso else None
    return P.when(dt, lang) if dt else None
