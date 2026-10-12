"""Synthetic eval patients for the documentation agent (spec 7.3, evals/documentation).

`materialize(store, case, now)` writes one eval case as FHIR resources, the way the
generator writes the hospital (it plays the EHR, so it writes the store directly): a
patient (MRN, language, AI-processing and SMS consent), an outpatient visit with the
home medications, and a stay on Medicine A (at the unit, no bed, so no seeded bed
changes) admitted `days` ago with its diagnoses, a procedure, inpatient orders,
vital-sign panels (admission and the last 12 hours), laboratory results, a report, a
pending test, an open task, a flag, the discharge prescriptions and the discharge
plan (CarePlan). A case may carry a clinical note naming a medicine that was only
discussed (the temptation the mock model falls for). Every id starts with
`ev-<case id>-`. Run it inside a transaction that is rolled back
(scripts/documentation_eval.py, tests).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.core.store import Store
from app.ehr import codes as C
from app.ehr.seed.builder import Builder
from app.fhir.dt import fhir_date, fhir_datetime, ref

UNIT = "MEDA"
LANG_DISPLAY = {"en": "English", "zh-CN": "Chinese (Simplified)", "zh-TW": "Chinese (Traditional)"}
CLINICS = {"family_doctor": ("Family doctor follow-up", "Family doctor's office"),
           "anticoag_clinic": ("INR blood test at the blood thinner clinic", "Anticoagulation Clinic, Main Building"),
           "heart_failure_clinic": ("Heart failure clinic", "Heart Function Clinic, Level 3"),
           "heart_clinic": ("Cardiology clinic", "Cardiology Clinic, Level 3"),
           "lung_clinic": ("Respirology clinic", "Respirology Clinic, Level 4"),
           "stroke_clinic": ("Stroke prevention clinic", "Stroke Prevention Clinic, Level 5"),
           "fracture_clinic": ("Fracture clinic", "Fracture Clinic, Level 1"),
           "surgery_clinic": ("Surgery clinic", "General Surgery Clinic, Level 2")}
REPORTS = {"chest-xray": ("Chest X-ray", "36643-5"), "ct-head": ("CT head", "24725-4"),
           "us-abdomen": ("Ultrasound abdomen", "24558-9"), "echo": ("Echocardiogram", "34552-0"),
           "xray-hip": ("X-ray hip", "37637-6")}
PENDING = {"600-7": "Bacteria identified in Blood by Culture", "630-4": "Bacteria identified in Urine by Culture"}


def _vitals(b: Builder, pid: str, stay: str, when: datetime, values: dict, rid: str) -> None:
    comps = [(code, values[key], C.VITALS[code][1]) for key, code in (
        ("hr", "8867-4"), ("sbp", "8480-6"), ("dbp", "8462-4"), ("rr", "9279-1"), ("temp", "8310-5"),
        ("spo2", "59408-5"), ("pain", "72514-3")) if key in values]
    b.observation(pid, stay, C.VITAL_PANEL, when, components=comps, rid=rid)


def materialize(store: Store, case: dict, now: datetime, *, index: int = 0) -> dict:
    """Write the case; returns {patient, encounter, refs}."""
    b = Builder(store, 0, now)
    key = case["id"]
    pid, clinic, stay = f"ev-{key}-pat", f"ev-{key}-clinic", f"ev-{key}-stay"
    p = case["patient"]
    birth = (now - timedelta(days=int(p["age"] * 365.25) + 40)).date()
    lang = p.get("language", "en")
    b.add({"resourceType": "Patient", "id": pid, "active": True, "gender": p.get("sex", "female"),
           "birthDate": fhir_date(birth),
           "identifier": [{"use": "usual", "system": C.MRN_SYSTEM, "value": f"8{index + 1:07d}"}],
           "name": [{"use": "official", "family": "Doccase", "given": [key.replace("-", "")],
                     "text": f"{key.replace('-', '')} Doccase"}],
           "communication": [{"language": {"coding": [{"system": "urn:ietf:bcp:47", "code": lang,
                                                       "display": LANG_DISPLAY[lang]}]}, "preferred": True}]})
    visit = now - timedelta(days=90)
    b.encounter(pid, "AMB", visit, end=visit + timedelta(minutes=30), service="outpatient-clinic", rid=clinic)
    admitted = (now - timedelta(days=case["days"], hours=3)).replace(minute=10, second=0, microsecond=0)
    b.encounter(pid, "IMP", admitted, status="in-progress", service="Medicine A", reason=C.snomed(case["dx"]),
                locations=[(UNIT, admitted, None)], admit_source="emd", rid=stay)
    for category in ("ai_processing", "sms"):
        b.add({"resourceType": "Consent", "id": f"ev-{key}-consent-{category}", "status": "active",
               "scope": C.concept(C.CONSENT_SCOPE, "patient-privacy"),
               "category": [C.concept(C.CONSENT_CATEGORY, category, category)],
               "patient": ref("Patient", pid), "dateTime": fhir_datetime(visit),
               "provision": {"type": "permit", "purpose": [C.coding(C.CONSENT_CATEGORY, category)]}})
    b.condition(pid, case["dx"], admitted, enc=stay, category="encounter-diagnosis", rid=f"ev-{key}-dx")
    for n, code in enumerate(case.get("secondary") or [], start=1):
        cond = b.condition(pid, code, admitted + timedelta(hours=2), enc=stay, category="encounter-diagnosis",
                           rid=f"ev-{key}-dx-{n}")
        if case.get("note_drug") and n == 1:
            name = C.MEDICATIONS[case["note_drug"]][0]
            cond["note"] = [{"text": f"{name.capitalize()} was discussed with the patient; not started in hospital, "
                                     f"the family doctor will decide."}]
            store.fhir.update(cond)
    if (proc := case.get("procedure")) is not None:
        start = admitted + timedelta(days=proc["day"], hours=5)
        b.procedure(pid, stay, proc["code"], start, start + timedelta(minutes=90), rid=f"ev-{key}-proc")
    for n, entry in enumerate(case.get("home") or [], start=1):
        b.medication_statement(pid, entry["rxcui"], visit.date() - timedelta(days=300), enc=clinic,
                               dose=entry.get("dose"), per_day=entry.get("per_day"), rid=f"ev-{key}-home-{n}")
    for n, entry in enumerate(case.get("inpatient") or [], start=1):
        b.medication_request(pid, stay, entry["rxcui"], admitted + timedelta(hours=1), dose=entry.get("dose"),
                             per_day=entry.get("per_day"), prn=entry.get("prn", False), reason=entry.get("reason"),
                             rid=f"ev-{key}-ord-{n}")
    for n, entry in enumerate(case.get("discharge") or [], start=1):
        b.medication_request(pid, stay, entry["rxcui"], now - timedelta(hours=2), intent="plan", category="discharge",
                             dose=entry.get("dose"), per_day=entry.get("per_day"), reason=entry.get("reason"),
                             rid=f"ev-{key}-dc-{n}")
    v = case.get("vitals") or {}
    _vitals(b, pid, stay, admitted + timedelta(minutes=30), v.get("admission", {}), f"ev-{key}-vit-adm")
    _vitals(b, pid, stay, now - timedelta(hours=10), v.get("earlier", {}), f"ev-{key}-vit-1")
    _vitals(b, pid, stay, now - timedelta(hours=4), v.get("latest", {}), f"ev-{key}-vit-2")
    for n, lab in enumerate(case.get("labs") or [], start=1):
        code = lab["code"]
        display, unit, (lo, hi) = C.LABS[code]
        for m, value in enumerate(lab["values"], start=1):
            when = admitted + timedelta(hours=2) if m == 1 else now - timedelta(hours=6)
            flag = "H" if value > hi else "L" if value < lo else "N"
            b.observation(pid, stay, (code, display), when, value=value, unit=unit, category="laboratory",
                          interpretation=flag, rid=f"ev-{key}-lab-{n}-{m}")
    if (rep := case.get("report")) is not None:
        name, code = REPORTS[rep["kind"]]
        b.diagnostic_report(pid, stay, C.concept(C.LOINC, code, name), admitted + timedelta(hours=4),
                            conclusion=rep["conclusion"], category="RAD", rid=f"ev-{key}-report")
    if (pending := case.get("pending")) is not None:
        b.service_request(pid, stay, C.concept(C.LOINC, pending, PENDING[pending]), now - timedelta(hours=8),
                          category="lab", rid=f"ev-{key}-pending")
    if case.get("task"):  # explicit ids: the Builder's counters would collide with the seeded hospital's
        at = fhir_datetime(now - timedelta(hours=5))
        b.add({"resourceType": "Task", "id": f"ev-{key}-task", "status": "requested", "intent": "order",
               "priority": "routine", "code": C.concept(C.TASK_CODE, "care-coordination", "Care coordination"),
               "authoredOn": at, "lastModified": at, "for": ref("Patient", pid), "encounter": ref("Encounter", stay),
               "performerType": [C.concept(C.PRACTITIONER_ROLE, "nurse")], "description": case["task"]})
    for n, code in enumerate(case.get("flags") or [], start=1):
        b.add({"resourceType": "Flag", "id": f"ev-{key}-flag-{n}", "status": "active",
               "category": [C.concept("http://terminology.hl7.org/CodeSystem/flag-category", "clinical")],
               "code": C.concept(C.FLAG_CODE, code, {"fall-risk": "Fall risk", "isolation": "Isolation"}.get(code, code)),
               "subject": ref("Patient", pid), "encounter": ref("Encounter", stay),
               "period": {"start": fhir_datetime(admitted + timedelta(hours=2))}})
    activities = []
    for code, days in case.get("follow_up") or []:
        display, location = CLINICS[code]
        when = (now + timedelta(days=days)).replace(hour=10, minute=0, second=0, microsecond=0)
        activities.append({"detail": {"kind": "Appointment", "code": C.concept("urn:demo-hospital:clinic", code, display),
                                      "status": "scheduled", "scheduledPeriod": {"start": fhir_datetime(when)},
                                      "location": {"display": location}, "description": display}})
    if activities:
        b.add({"resourceType": "CarePlan", "id": f"ev-{key}-plan", "status": "active", "intent": "plan",
               "category": [C.concept(C.LOCAL, "discharge-plan", "Discharge plan")], "title": "Discharge plan",
               "subject": ref("Patient", pid), "encounter": ref("Encounter", stay),
               "period": {"start": fhir_datetime(now)}, "activity": activities})
    return {"patient": pid, "encounter": stay}
