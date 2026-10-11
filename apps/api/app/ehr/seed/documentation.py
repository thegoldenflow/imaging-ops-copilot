"""Documentation demo data (spec 7.3, WP7), written at the very end of the generator.

1. Discharge plans: a CarePlan (`careplan-dc-<stay>`, category discharge-plan) for
   every stay with discharge prescriptions (WP6), as the team would have arranged the
   follow-up: the family doctor in a week, a clinic by diagnosis (heart failure, heart,
   lung, stroke, fracture, surgery), the blood thinner clinic three days out for a
   patient leaving on warfarin. Each activity has a clinic code, a date and a location.
2. Planted cases on Medicine A (the demo physician's and nurse's unit), on patients no
   other demo case uses, who consented to AI processing:
   - `warfarin_zh`: a Chinese-speaking patient (Simplified first, then Traditional; a
     woman about 72 preferred) on warfarin for atrial fibrillation, with a catheter
     ablation during the stay (a puncture site to look after), INR results, warfarin
     continued at a lower dose at discharge and the blood thinner clinic in the plan:
     the bilingual instructions with warfarin food advice and wound red flags (8.2
     steps 9 and 10);
   - `hallucination`: a stay whose hypertension note says a medicine "was discussed,
     not started"; the mock model takes it for a new prescription and the validator
     blocks it (8.2 step 9);
   - `discharged`: a stay discharged in the last 72 hours, given the discharge
     prescriptions it left with, a discharge plan and a blood culture still pending.
   Stays leaving within 24 hours are preferred (their discharge reconciliation opens in
   Medication safety); otherwise the stay gets discharge prescriptions here.

Everything here is written after every other section, with its own random stream and
explicit ids (`*-doc-*`, `careplan-dc-*`), so no earlier id or value changes.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from app.ehr import codes as C
from app.ehr.seed import medsafety
from app.ehr.seed.builder import Builder
from app.ehr.seed.people import PatientInfo
from app.ehr.seed.simulate import Stay, Timeline
from app.fhir.dt import fhir_datetime, parse, ref

CLINIC = "urn:demo-hospital:clinic"
WARFARIN, AFIB, ABLATION, HTN = "11289", "49436004", "18286008", "59621000"
CARDIAC = {"22298006", "42343007", "29857009", AFIB}  # a catheter ablation fits these stays best
TEMPTING = ("6918", "17767", "29046", "83367")  # metoprolol, amlodipine, lisinopril, atorvastatin
# clinic code -> (display, location)
CLINICS = {
    "family_doctor": ("Family doctor follow-up", "Family doctor's office"),
    "anticoag_clinic": ("INR blood test at the blood thinner clinic", "Anticoagulation Clinic, Main Building, Level 2"),
    "heart_failure_clinic": ("Heart failure clinic", "Heart Function Clinic, Level 3"),
    "heart_clinic": ("Cardiology clinic", "Cardiology Clinic, Level 3"),
    "lung_clinic": ("Respirology clinic", "Respirology Clinic, Level 4"),
    "stroke_clinic": ("Stroke prevention clinic", "Stroke Prevention Clinic, Level 5"),
    "fracture_clinic": ("Fracture clinic", "Fracture Clinic, Level 1"),
    "surgery_clinic": ("Surgery clinic", "General Surgery Clinic, Level 2"),
}
CLINIC_BY_DX = {"42343007": ("heart_failure_clinic", 14), "22298006": ("heart_clinic", 14),
                "195951007": ("lung_clinic", 28), "230690007": ("stroke_clinic", 14), "5913000": ("fracture_clinic", 14),
                "125605004": ("fracture_clinic", 14), AFIB: ("heart_clinic", 28)}


def _rng(b: Builder, *parts: str) -> random.Random:
    return random.Random(f"hospital:{b.seed}:documentation:" + ":".join(parts))


def _unit_at(stay: Stay, at: datetime) -> str:
    bed = next((bed for bed, since, until in stay.beds if since <= at < until), stay.beds[-1][0] if stay.beds else "")
    return bed.split("-", 1)[0]


def _consents(b: Builder, patient_id: str) -> dict[str, str]:
    return b.consents(patient_id)


def _meds(b: Builder, stay_id: str) -> list[dict]:
    return b.store.fhir.search("MedicationRequest", encounter=stay_id)


def _rxcui(resource: dict) -> str:
    return resource["medicationCodeableConcept"]["coding"][0]["code"]


def care_plan(b: Builder, stay: Stay, p: PatientInfo, leaving: datetime, *, warfarin: bool = False) -> dict:
    rng = _rng(b, "plan", stay.id)
    activities = [("family_doctor", 7)]
    if stay.dx in CLINIC_BY_DX:
        activities.append(CLINIC_BY_DX[stay.dx])
    elif stay.surgery:
        activities.append(("fracture_clinic" if _unit_at(stay, stay.admit) == "ORTH" or stay.service_unit == "ORTH"
                           else "surgery_clinic", 14))
    if warfarin:
        activities.insert(0, ("anticoag_clinic", 3))
    detail = []
    for code, days in activities:
        display, location = CLINICS[code]
        when = (leaving + timedelta(days=days)).replace(hour=rng.choice((9, 10, 11, 13, 14)), minute=rng.choice((0, 30)),
                                                         second=0, microsecond=0)
        if when.weekday() >= 5:  # clinics see patients on weekdays
            when += timedelta(days=7 - when.weekday())
        detail.append({"detail": {"kind": "Appointment", "code": C.concept(CLINIC, code, display), "status": "scheduled",
                                  "scheduledPeriod": {"start": fhir_datetime(when)}, "location": {"display": location},
                                  "description": f"{display} {days} days after discharge"}})
    plan = {"resourceType": "CarePlan", "id": f"careplan-dc-{stay.id}", "status": "active", "intent": "plan",
            "category": [C.concept(C.LOCAL, "discharge-plan", "Discharge plan")], "title": "Discharge plan",
            "subject": ref("Patient", p.id), "encounter": ref("Encounter", stay.id),
            "period": {"start": fhir_datetime(leaving)},
            "activity": detail}
    return b.add(plan)


def _ensure_prescriptions(b: Builder, stay: Stay, p: PatientInfo, at: datetime) -> None:
    if not any(m.get("intent") == "plan" for m in _meds(b, stay.id)):
        medsafety.discharge_prescriptions(b, stay, p, at)


def plant_warfarin(b: Builder, stay: Stay, p: PatientInfo, now: datetime) -> dict:
    _ensure_prescriptions(b, stay, p, now)
    start = (now - timedelta(days=1200)).date()
    if stay.dx != AFIB:
        b.condition(p.id, AFIB, start, enc=stay.id, category="encounter-diagnosis", rid="cond-doc-afib")
    if WARFARIN not in p.home_meds:
        b.medication_statement(p.id, WARFARIN, start, enc=p.clinic_encounter, rid="medst-doc-warfarin")
    meds = _meds(b, stay.id)
    if not any(_rxcui(m) == WARFARIN and m.get("intent") == "order" for m in meds):
        b.medication_request(p.id, stay.id, WARFARIN, stay.admit + timedelta(hours=2), requester=stay.attending,
                             dose=5, per_day=1, reason=AFIB, rid="medrx-doc-warfarin")
    plans = [m for m in meds if m.get("intent") == "plan"]
    if not any(_rxcui(m) == WARFARIN for m in plans):
        authored = max((m.get("authoredOn") for m in plans), default=fhir_datetime(now - timedelta(hours=3)))
        b.medication_request(p.id, stay.id, WARFARIN, parse(authored), intent="plan", category="discharge",
                             status="active", requester=stay.attending, dose=3, per_day=1, reason=AFIB,
                             rid=f"medrx-dc-{stay.id}-w1")
    span = max(timedelta(hours=6), (now - stay.admit) / 2)
    done = stay.admit + min(span, timedelta(hours=30))
    b.procedure(p.id, stay.id, ABLATION, done - timedelta(minutes=150), done, performer=stay.attending,
                rid="proc-doc-ablation")
    inr = ("6301-6", C.LABS["6301-6"][0])
    b.observation(p.id, stay.id, inr, stay.admit + timedelta(hours=3), value=3.1, unit="{INR}", category="laboratory",
                  interpretation="H", rid="obs-doc-inr-1")
    b.observation(p.id, stay.id, inr, now - timedelta(hours=5), value=2.4, unit="{INR}", category="laboratory",
                  interpretation="H", rid="obs-doc-inr-2")
    care_plan(b, stay, p, stay.discharge if stay.discharge > now else now + timedelta(hours=6), warfarin=True)
    return {"stay": stay.id, "patient": p.id, "language": p.lang, "procedure": "proc-doc-ablation"}


def plant_hallucination(b: Builder, stay: Stay, p: PatientInfo, now: datetime) -> dict:
    _ensure_prescriptions(b, stay, p, now)
    taken = set(p.home_meds) | {_rxcui(m) for m in _meds(b, stay.id)}
    drug = next((rx for rx in TEMPTING if rx not in taken), TEMPTING[-1])
    name = C.MEDICATIONS[drug][0]
    cond = b.condition(p.id, HTN, stay.admit + timedelta(hours=1), enc=stay.id, category="encounter-diagnosis",
                       rid="cond-doc-htn-note")
    cond["note"] = [{"text": f"Blood pressure high on admission. {name.capitalize()} was discussed with the patient but "
                             f"not started; the family doctor will review it."}]
    b.store.fhir.update(cond)
    if not b.store.fhir.read("CarePlan", f"careplan-dc-{stay.id}"):
        care_plan(b, stay, p, stay.discharge if stay.discharge > now else now + timedelta(hours=6))
    return {"stay": stay.id, "patient": p.id, "tempting_drug": name}


def plant_discharged(b: Builder, stay: Stay, p: PatientInfo, now: datetime) -> dict:
    medsafety.discharge_prescriptions(b, stay, p, stay.discharge)
    warfarin = any(_rxcui(m) == WARFARIN and m.get("intent") == "plan" for m in _meds(b, stay.id))
    care_plan(b, stay, p, stay.discharge, warfarin=warfarin)
    b.service_request(p.id, stay.id, C.concept(C.LOINC, "600-7", "Bacteria identified in Blood by Culture"),
                      stay.discharge - timedelta(hours=8), category="lab", requester=stay.attending,
                      rid=f"ord-doc-pending-{stay.id}")
    return {"stay": stay.id, "patient": p.id, "pending": f"ord-doc-pending-{stay.id}"}


def generate(b: Builder, tl: Timeline, patients: dict[str, PatientInfo], now: datetime, medsafety_cases: dict) -> dict:
    current = sorted((s for s in tl.stays if s.admit <= now < s.discharge), key=lambda s: s.id)
    leaving = set(medsafety_cases.get("discharge_prescriptions") or {})
    out: dict = {"care_plans": []}
    meda = [s for s in current if _unit_at(s, now) == "MEDA"]
    used = {s.patient for s in meda[-2:]}  # the WP4 demo drafts' patients (the two newest Medicine A stays)
    used |= {case["patient"] for case in medsafety_cases.values() if isinstance(case, dict) and case.get("patient")}

    def eligible(s: Stay) -> bool:
        return s.patient not in used and _consents(b, s.patient).get("ai_processing") == "permit"

    zh = [s for s in meda if eligible(s) and patients[s.patient].lang != "en"]
    if zh:
        stay = min(zh, key=lambda s: (s.id not in leaving, patients[s.patient].lang != "zh-CN",
                                      _consents(b, s.patient).get("sms") != "permit", s.dx not in CARDIAC,
                                      patients[s.patient].sex != "female", abs(patients[s.patient].age - 72), s.id))
        out["warfarin_zh"] = plant_warfarin(b, stay, patients[stay.patient], now)
        used.add(stay.patient)
    pool = [s for s in meda if eligible(s)]
    if pool:
        stay = min(pool, key=lambda s: (s.id not in leaving, patients[s.patient].lang != "en", s.id))
        out["hallucination"] = plant_hallucination(b, stay, patients[stay.patient], now)
        used.add(stay.patient)
    gone = [s for s in tl.stays if now - timedelta(hours=60) <= s.discharge <= now - timedelta(hours=2)
            and _unit_at(s, s.discharge - timedelta(minutes=1)) == "MEDA" and eligible(s)]
    if gone:
        stay = max(gone, key=lambda s: (patients[s.patient].lang == "en", s.discharge))
        out["discharged"] = plant_discharged(b, stay, patients[stay.patient], now)
    for stay in current:  # the discharge plans of every other stay leaving with prescriptions
        if stay.id in leaving and not b.store.fhir.read("CarePlan", f"careplan-dc-{stay.id}"):
            warfarin = any(_rxcui(m) == WARFARIN and m.get("intent") == "plan" for m in _meds(b, stay.id))
            out["care_plans"].append(care_plan(b, stay, patients[stay.patient], stay.discharge, warfarin=warfarin)["id"])
    return out
