"""System 8 · MRI Safety Screening Assistant.

Patients answer a questionnaire in their own language. Claude turns free-text
implant descriptions into device names and categories, which are matched
against a (synthetic) device list. The system only ever raises flags for
review; it never clears anyone. A flagged appointment cannot be confirmed
until a technologist or radiologist records a decision."""

import re
import secrets
from datetime import datetime

from pydantic import BaseModel

from app.core.models import Appointment, Requisition
from app.core.store import Store, get_store
from app.core.templates import render
from app.integrations.mocks import queue_message
from app.llm.gateway import get_gateway
from app.llm.prompts import Prompt
from app.llm.providers import mock_fixture
from app.modules.requisitions import service as requisitions
from app.modules.scheduling import service as scheduling

QUESTIONS = ["pacemaker", "aneurysm_clip", "cochlear_implant", "neurostimulator", "metal_fragments", "drug_pump",
             "recent_surgery", "pregnant", "claustrophobic"]
QUESTION_LABEL = {
    "pacemaker": "Pacemaker or defibrillator", "aneurysm_clip": "Aneurysm clip", "cochlear_implant": "Cochlear implant",
    "neurostimulator": "Neurostimulator", "metal_fragments": "Metal fragments in body or eyes", "drug_pump": "Drug or insulin pump",
    "recent_surgery": "Surgery in the last 6 weeks", "pregnant": "Pregnant or possibly pregnant", "claustrophobic": "Claustrophobia",
}
SAFETY_QUESTIONS = set(QUESTIONS) - {"claustrophobic"}

# Synthetic device list (not a real MR safety reference).
DEVICES = [
    {"id": "DEV-PM", "name": "Cardiac pacemaker", "category": "cardiac_device", "mr_status": "MR Conditional only for specific models; verify model and programming",
     "keywords": ["pacemaker", "起搏器", "stimulateur cardiaque", "ਪੇਸਮੇਕਰ"]},
    {"id": "DEV-ICD", "name": "Implantable defibrillator", "category": "cardiac_device", "mr_status": "Usually MR Unsafe unless labelled MR Conditional",
     "keywords": ["defibrillator", "icd", "除颤器", "défibrillateur"]},
    {"id": "DEV-CI", "name": "Cochlear implant", "category": "ear_implant", "mr_status": "MR Conditional; magnet may need removal or a head wrap",
     "keywords": ["cochlear", "人工耳蜗", "耳蜗", "implant cochléaire", "ਕੋਕਲੀਅਰ"]},
    {"id": "DEV-AC", "name": "Cerebral aneurysm clip", "category": "neuro_clip", "mr_status": "Depends on clip material; obtain the operative note",
     "keywords": ["aneurysm clip", "动脉瘤夹", "anévrisme", "ਐਨਿਉਰਿਜ਼ਮ"]},
    {"id": "DEV-NS", "name": "Neurostimulator", "category": "neurostimulator", "mr_status": "MR Conditional with specific settings",
     "keywords": ["neurostimulator", "stimulator", "神经刺激器", "neurostimulateur"]},
    {"id": "DEV-PUMP", "name": "Insulin or drug pump", "category": "drug_pump", "mr_status": "External pumps must be removed; implanted pumps need verification",
     "keywords": ["insulin pump", "pump", "胰岛素泵", "pompe"]},
    {"id": "DEV-EYE", "name": "Metal fragments in eye", "category": "foreign_body", "mr_status": "MR Unsafe until cleared by orbit X-ray",
     "keywords": ["metal in my eye", "metal fragment", "shrapnel", "welder", "金属碎片", "铁屑", "éclat", "ਧਾਤ ਦੇ ਟੁਕੜੇ"]},
    {"id": "DEV-JOINT", "name": "Joint replacement", "category": "orthopedic", "mr_status": "Generally MR Conditional",
     "keywords": ["hip replacement", "knee replacement", "joint replacement", "人工关节", "髋关节", "膝关节置换", "prothèse", "ਕੁੱਲ੍ਹੇ"]},
    {"id": "DEV-STERNAL", "name": "Sternal wires", "category": "surgical_hardware", "mr_status": "Generally MR Conditional",
     "keywords": ["sternal wire", "钢丝", "搭桥", "bypass", "fils sternaux"]},
    {"id": "DEV-STENT", "name": "Coronary or vascular stent", "category": "stent", "mr_status": "Most are MR Conditional; verify",
     "keywords": ["stent", "支架", "endoprothèse"]},
    {"id": "DEV-SHUNT", "name": "Programmable shunt", "category": "shunt", "mr_status": "MR Conditional; settings must be checked after the scan",
     "keywords": ["shunt", "分流管", "dérivation"]},
    {"id": "DEV-IUD", "name": "Intrauterine device", "category": "gynecologic", "mr_status": "Most are MR Conditional",
     "keywords": ["iud", "coil", "节育环", "stérilet"]},
]
DEVICE_BY_CATEGORY = {}
for _d in DEVICES:
    DEVICE_BY_CATEGORY.setdefault(_d["category"], _d)
CATEGORIES = sorted(DEVICE_BY_CATEGORY) + ["other"]


class Device(BaseModel):
    patient_words: str
    device_name: str
    category: str


class DeviceExtraction(BaseModel):
    devices: list[Device]


IMPLANT_PROMPT = Prompt(
    name="mri_implant_extract",
    version="mri_implant_extract@1",
    system=(
        "Patients describe implants and metal in their bodies in their own words, in any language. "
        "List each distinct device: quote the patient's words, give a standard English device name, and a category "
        f"from: {', '.join(CATEGORIES)}. Do not judge MRI safety; staff review every answer. Return an empty list "
        "if nothing is described."
    ),
    template="Patient answer:\n{text}",
)


def match_device(text: str) -> dict | None:
    lowered = text.lower()
    return next((d for d in DEVICES if any(k in lowered for k in d["keywords"])), None)


@mock_fixture("mri_implant_extract")
def _mock(text: str, images: list, attempt: int) -> dict:
    answer = text.split("\n", 1)[-1]
    devices = []
    for part in re.split(r"[.;,，。、\n]| and | et |和|以及", answer):
        if d := match_device(part):
            if not any(x["category"] == d["category"] for x in devices):
                devices.append({"patient_words": part.strip(), "device_name": d["name"], "category": d["category"]})
    return {"devices": devices}


class Screening(BaseModel):
    id: str
    token: str
    requisition_id: str
    patient_id: str
    appointment_id: str | None = None
    language: str
    status: str = "sent"  # sent, flagged, no_flags, cleared, not_cleared
    answers: dict[str, bool] = {}
    free_text: str = ""
    requisition_hints: list[str] = []
    devices: list[dict] = []
    flags: list[str] = []
    ai_status: str | None = None
    llm_call_id: str | None = None
    created_at: datetime
    submitted_at: datetime | None = None
    reviewed_by: str | None = None
    review_decision: str | None = None  # cleared, not_cleared
    review_note: str | None = None
    reviewed_at: datetime | None = None


def screenings(store: Store) -> dict[str, Screening]:
    return store.module("mri_screenings", dict)


def for_requisition(store: Store, requisition_id: str | None) -> Screening | None:
    return next((s for s in screenings(store).values() if s.requisition_id == requisition_id), None)


def by_token(store: Store, token: str) -> Screening | None:
    return next((s for s in screenings(store).values() if s.token == token), None)


def create_for(store: Store, req: Requisition) -> Screening | None:
    if requisitions.modality_of(store, req.id) != "MRI" or for_requisition(store, req.id):
        return None
    patient = store.patients[req.patient_id]
    rec = requisitions.extractions(store).get(req.id)
    hints = [h["value"] for h in rec.fields["implant_hints"]] if rec else []
    screening = Screening(id=store.next_id("MRS"), token=secrets.token_urlsafe(10), requisition_id=req.id,
                          patient_id=patient.id, language=patient.preferred_language, requisition_hints=hints,
                          created_at=datetime.now())
    screening.flags = [f"Requisition mentions: {h}" for h in hints]
    if screening.flags:
        screening.status = "flagged"
    screenings(store)[screening.id] = screening
    queue_message(channel="sms", kind="mri_screening", to=patient.phone, language=patient.preferred_language,
                  patient_id=patient.id,
                  body=render("mri_screening", patient.preferred_language, link=f"/mri-screening/{screening.token}"))
    store.touch()
    return screening


def submit(store: Store, screening: Screening, answers: dict[str, bool], free_text: str, language: str) -> Screening:
    patient = store.patients[screening.patient_id]
    screening.answers = {q: bool(answers.get(q)) for q in QUESTIONS}
    screening.free_text = free_text.strip()[:1000]
    screening.language = language
    screening.submitted_at = datetime.now()
    flags = [f"Requisition mentions: {h}" for h in screening.requisition_hints]
    flags += [f"Patient answered yes: {QUESTION_LABEL[q]}" for q in SAFETY_QUESTIONS if screening.answers[q]]
    devices = []
    if screening.free_text:
        outcome = get_gateway().structured(task="mri_implant_extract", prompt=IMPLANT_PROMPT,
                                           variables={"text": screening.free_text}, schema_cls=DeviceExtraction,
                                           tier="fast", patients=[patient])
        screening.ai_status, screening.llm_call_id = outcome.status, outcome.call_id
        if outcome.status == "ok":
            for d in outcome.data["devices"]:
                listed = match_device(d["device_name"]) or match_device(d["patient_words"]) or DEVICE_BY_CATEGORY.get(d["category"])
                devices.append({**d, "list_match": listed["name"] if listed else None,
                                "mr_status": listed["mr_status"] if listed else "Not on device list; verify"})
                flags.append(f"Described: “{d['patient_words']}” → {d['device_name']}")
        else:
            # AI unavailable: any free text still needs a person to read it.
            flags.append(f"Free-text answer needs manual review: “{screening.free_text[:80]}”")
    screening.devices = devices
    screening.flags = flags
    screening.status = "flagged" if flags else "no_flags"
    if screening.answers.get("claustrophobic"):
        screening.flags.append("Note: claustrophobia (plan for comfort, not a safety flag)")
    screening.review_decision = screening.reviewed_by = screening.reviewed_at = None
    store.touch()
    return screening


def review(store: Store, screening: Screening, reviewer: str, decision: str, note: str) -> Screening:
    screening.review_decision, screening.reviewed_by = decision, reviewer
    screening.review_note, screening.reviewed_at = note, datetime.now()
    screening.status = decision
    store.touch()
    return screening


def guard(store: Store, appt: Appointment) -> str | None:
    screening = for_requisition(store, appt.requisition_id) if appt.requisition_id else None
    if screening is None:
        return None
    if screening.status == "flagged":
        return "MRI safety screening is flagged and awaiting technologist or radiologist review"
    if screening.status == "not_cleared":
        return "MRI safety screening was not cleared"
    return None


def on_processed(store: Store, req: Requisition) -> None:
    create_for(store, req)


def on_booked(appt: Appointment) -> None:
    screening = for_requisition(get_store(), appt.requisition_id) if appt.requisition_id else None
    if screening:
        screening.appointment_id = appt.id


scheduling.CONFIRM_GUARDS.append(guard)
scheduling.BOOKING_HOOKS.append(on_booked)
requisitions.PROCESSED_HOOKS.append(on_processed)
