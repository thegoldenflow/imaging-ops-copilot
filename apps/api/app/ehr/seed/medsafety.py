"""Medication-safety demo data (spec 7.2, WP6), written at the end of the generator.

1. Discharge prescriptions (MedicationRequest intent plan, category discharge) for
   the current stays planned to leave within 24 hours of the hospital day's start,
   as the attending would have prepared them: most home medications continued, a
   few stopped (NSAIDs more often) or changed, inpatient medications continued,
   converted (an intravenous antibiotic to amoxicillin) or stopped by their kind.
   At least one Medicine A stay gets them (the demo physician co-signs its
   reconciliation).
2. Planted cases with fixed ids (`*-ms-*`):
   - the 8.2 storyline's case on a recent hip-fracture stay: warfarin at home and
     continued, a new naproxen order (major interaction), an INR of 3.8, and a home
     antihypertensive that was not ordered on admission (omission);
   - a blocking allergy (penicillin allergy, piperacillin ordered) on Medicine A;
   - renal dosing (eGFR 24, ketorolac ordered) on Medicine B;
   - dose bounds (tramadol 100 mg four times a day for a patient over 75);
   - a duplicate test (a lab test ordered again while its result is a day old) on Medicine A;
   - an admission reconciliation on Medicine A with a dose change, duplicate
     therapy and a new medication without an indication (quetiapine).

Everything here is written after every other section, with its own random
streams and explicit ids, so no earlier id or value changes. The day simulator
completes inpatient orders at discharge and leaves discharge prescriptions active.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from app.ehr import codes as C
from app.ehr.seed.builder import Builder
from app.ehr.seed.people import PatientInfo
from app.ehr.seed.simulate import Stay, Timeline
from app.fhir.dt import parse

HIP_FRACTURE = "5913000"
ANTIHYPERTENSIVES = ("17767", "29046", "5487")  # amlodipine, lisinopril, hydrochlorothiazide
# Inpatient-only medications at discharge: (probability it continues, the oral medication it becomes or None).
DISCHARGE_FATE: dict[str, tuple[float, str | None]] = {
    "67108": (0.6, None), "5224": (0.0, None), "161": (0.8, None), "1191": (0.95, None), "32968": (0.9, None),
    "83367": (0.95, None), "4603": (0.9, None), "8640": (0.8, None), "435": (0.9, None), "21212": (0.5, None),
    "3423": (0.25, None), "7052": (0.2, None), "26225": (0.1, None), "35827": (0.0, None),
    "2193": (0.6, "723"), "8339": (0.6, "723"), "2180": (0.0, None), "11124": (0.0, None), "6922": (0.5, None),
}
DOSE_CHANGE_CLASSES = ("loop diuretic", "beta blocker", "ace inhibitor")


def _rng(b: Builder, *parts: str) -> random.Random:
    return random.Random(f"hospital:{b.seed}:medsafety:" + ":".join(parts))


def _current(tl: Timeline, now: datetime) -> list[Stay]:
    return sorted((s for s in tl.stays if s.admit <= now < s.discharge), key=lambda s: s.id)


def _unit(stay: Stay, now: datetime) -> str:
    bed = next((bed for bed, since, until in stay.beds if since <= now < until), stay.beds[-1][0] if stay.beds else "")
    return bed.split("-", 1)[0]


def _orders(b: Builder, stay_id: str) -> list[dict]:
    return [r for r in b.store.fhir.search("MedicationRequest", encounter=stay_id) if r.get("intent") == "order"]


def _rxcui(resource: dict) -> str:
    return resource["medicationCodeableConcept"]["coding"][0]["code"]


def _dose(resource: dict) -> tuple[float | None, int | None]:
    dosage = (resource.get("dosageInstruction") or resource.get("dosage") or [{}])[0]
    dose = ((dosage.get("doseAndRate") or [{}])[0].get("doseQuantity") or {}).get("value")
    per_day = ((dosage.get("timing") or {}).get("repeat") or {}).get("frequency")
    return dose, per_day


# ---------- 1. discharge prescriptions ----------


def discharge_prescriptions(b: Builder, stay: Stay, p: PatientInfo, now: datetime) -> int:
    rng = _rng(b, "discharge", stay.id)
    inpatient = _orders(b, stay.id)
    by_rx = {_rxcui(r): r for r in inpatient}
    plan: list[tuple[str, float | None, int | None, str | None, bool]] = []  # rxcui, dose, per_day, reason, prn
    for rxcui in p.home_meds:
        name, drug_class, typical, _unit_, freq, _route = C.MEDICATIONS[rxcui]
        stop = 0.5 if drug_class == "nsaid" else 0.1
        if rng.random() < stop:
            continue
        dose, per_day = typical, freq
        if drug_class in DOSE_CHANGE_CLASSES and rng.random() < 0.3:
            dose = typical * 2 if rng.random() < 0.5 else typical / 2
        plan.append((rxcui, dose, per_day, None, False))
    for rxcui, order in by_rx.items():
        if rxcui in p.home_meds:
            continue
        keep, becomes = DISCHARGE_FATE.get(rxcui, (0.5, None))
        if rng.random() >= keep:
            continue
        target = becomes or rxcui
        if any(t == target for t, *_ in plan):
            continue
        reason = (order.get("reasonCode") or [{}])[0].get("coding", [{}])[0].get("code") if order.get("reasonCode") \
            else None
        dose, per_day = (None, None) if becomes else _dose(order)
        prn = bool(((order.get("dosageInstruction") or [{}])[0]).get("asNeededBoolean")) and not becomes
        plan.append((target, dose, per_day, reason, prn))
    authored = max(stay.admit, now - timedelta(hours=rng.uniform(1, 10))).replace(second=0, microsecond=0)
    for n, (rxcui, dose, per_day, reason, prn) in enumerate(plan, start=1):
        b.medication_request(p.id, stay.id, rxcui, authored, intent="plan", category="discharge", status="active",
                             requester=stay.attending, dose=dose, per_day=per_day, reason=reason, prn=prn,
                             rid=f"medrx-dc-{stay.id}-{n:02d}")
    return len(plan)


# ---------- 2. planted cases ----------


def _pick_82(stays: list[Stay], patients: dict[str, PatientInfo], now: datetime) -> Stay | None:
    """A hip-fracture stay admitted as recently as possible, preferring the storyline's patient (a woman about 72
    whose language is Chinese)."""
    for hours in (24, 48, 72, 168, None):
        for pool in ([s for s in stays if s.dx == HIP_FRACTURE], [s for s in stays if _unit(s, now) == "ORTH"]):
            found = [s for s in pool if hours is None or s.admit >= now - timedelta(hours=hours)]
            if found:
                return min(found, key=lambda s: (patients[s.patient].sex != "female", patients[s.patient].lang == "en",
                                                 abs(patients[s.patient].age - 72), -s.admit.timestamp()))
    return stays[-1] if stays else None


def _pick(stays: list[Stay], used: set[str], *, unit: str | None = None, recent: bool = False,
          key=None, now: datetime) -> Stay | None:
    pool = [s for s in stays if s.patient not in used and (unit is None or _unit(s, now) == unit)]
    if recent:
        within = [s for s in pool if s.admit >= now - timedelta(hours=24)]
        pool = within or pool
    if not pool:
        return None
    return min(pool, key=key) if key else pool[-1]


def plant_82(b: Builder, stay: Stay, p: PatientInfo, now: datetime) -> dict:
    clinic = p.clinic_encounter
    statement_start = (now - timedelta(days=900)).date()
    if "49436004" not in p.conditions:
        b.condition(p.id, "49436004", statement_start, enc=clinic, rid="cond-ms-82-afib")
    if "59621000" not in p.conditions:
        b.condition(p.id, "59621000", statement_start, enc=clinic, rid="cond-ms-82-htn")
    if "11289" not in p.home_meds:
        b.medication_statement(p.id, "11289", statement_start, enc=clinic, rid="medst-ms-82-warfarin")
    ordered = {_rxcui(r) for r in _orders(b, stay.id)}
    missed = next((rx for rx in ANTIHYPERTENSIVES if rx not in ordered), ANTIHYPERTENSIVES[0])
    if missed not in p.home_meds:
        b.medication_statement(p.id, missed, statement_start, enc=clinic, rid="medst-ms-82-antihypertensive")
    b.observation(p.id, stay.id, ("6301-6", C.LABS["6301-6"][0]), stay.admit + timedelta(hours=1), value=3.8,
                  unit="{INR}", category="laboratory", interpretation="H", rid="obs-ms-82-inr")
    if "11289" not in ordered:
        b.medication_request(p.id, stay.id, "11289", stay.admit + timedelta(hours=2), requester=stay.attending,
                             rid="medrx-ms-82-warfarin")
    b.medication_request(p.id, stay.id, "7258", now - timedelta(minutes=90), requester=stay.attending,
                         note="For hip pain.", rid="medrx-ms-82-naproxen")
    return {"stay": stay.id, "patient": p.id, "naproxen": "medrx-ms-82-naproxen", "missed": missed}


def plant_allergy(b: Builder, stay: Stay, p: PatientInfo, now: datetime) -> dict:
    b.allergy(p.id, C.ALLERGENS[0], (now - timedelta(days=2000)).date(), criticality="high", reaction="Anaphylaxis",
              severity="severe", enc=p.clinic_encounter, rid="alg-ms-penicillin")
    b.medication_request(p.id, stay.id, "8339", now - timedelta(hours=3), requester=stay.attending, reason=stay.dx,
                         rid="medrx-ms-allergy-piperacillin")
    return {"stay": stay.id, "patient": p.id, "order": "medrx-ms-allergy-piperacillin"}


def plant_renal(b: Builder, stay: Stay, p: PatientInfo, now: datetime) -> dict:
    b.observation(p.id, stay.id, ("33914-3", C.LABS["33914-3"][0]), now - timedelta(hours=8), value=24,
                  unit="mL/min/{1.73_m2}", category="laboratory", interpretation="L", rid="obs-ms-renal-egfr")
    b.medication_request(p.id, stay.id, "35827", now - timedelta(hours=4), requester=stay.attending, dose=15,
                         per_day=4, rid="medrx-ms-renal-ketorolac")
    return {"stay": stay.id, "patient": p.id, "order": "medrx-ms-renal-ketorolac"}


def plant_dose(b: Builder, stay: Stay, p: PatientInfo, now: datetime) -> dict:
    b.medication_request(p.id, stay.id, "10689", now - timedelta(hours=2), requester=stay.attending, dose=100,
                         per_day=4, rid="medrx-ms-dose-tramadol")
    return {"stay": stay.id, "patient": p.id, "order": "medrx-ms-dose-tramadol"}


def plant_duplicate_test(b: Builder, stay: Stay, p: PatientInfo, now: datetime) -> dict | None:
    done = [r for r in b.store.fhir.search("ServiceRequest", encounter=stay.id, status="completed")
            if r["category"][0]["coding"][0]["code"] == "lab"
            and parse(r.get("occurrenceDateTime")) and parse(r["occurrenceDateTime"]) >= now - timedelta(hours=48)]
    if not done:
        return None
    prior = max(done, key=lambda r: r["occurrenceDateTime"])
    code = prior["code"]["coding"][0]["code"]
    b.service_request(p.id, stay.id, C.loinc(code), now - timedelta(minutes=20), category="lab",
                      requester=stay.attending, rid="ord-ms-duplicate")
    return {"stay": stay.id, "patient": p.id, "order": "ord-ms-duplicate", "prior": prior["id"]}


DOSE_CHANGE_DRUGS = ("6918", "4603", "29046", "17767")  # metoprolol, furosemide, lisinopril, amlodipine
NO_ANTIPSYCHOTIC_INDICATION = ("26929004", "35489007")  # Alzheimer's disease, depression: avoid them for this case


def plant_admission_medrec(b: Builder, stay: Stay, p: PatientInfo, now: datetime) -> dict:
    """A home medication ordered at half its dose (metoprolol 50 mg twice daily at home, 25 mg ordered, or the
    next of DOSE_CHANGE_DRUGS the patient neither takes nor has ordered); atorvastatin at home and continued with
    simvastatin added (duplicate statins); quetiapine started with no diagnosis that explains it."""
    clinic, start = p.clinic_encounter, (now - timedelta(days=700)).date()
    at = stay.admit + timedelta(hours=1, minutes=30)
    ordered = {_rxcui(r) for r in _orders(b, stay.id)}
    changed = next((rx for rx in DOSE_CHANGE_DRUGS if rx not in p.home_meds and rx not in ordered), "6918")
    b.medication_statement(p.id, changed, start, enc=clinic, rid="medst-ms-medrec-dose")
    b.medication_request(p.id, stay.id, changed, at, requester=stay.attending, dose=C.MEDICATIONS[changed][2] / 2,
                         rid="medrx-ms-medrec-dose")
    if "83367" not in p.home_meds:
        b.medication_statement(p.id, "83367", start, enc=clinic, rid="medst-ms-medrec-atorvastatin")
    if "83367" not in ordered:
        b.medication_request(p.id, stay.id, "83367", at, requester=stay.attending, rid="medrx-ms-medrec-atorvastatin")
    b.medication_request(p.id, stay.id, "36567", at, requester=stay.attending, rid="medrx-ms-medrec-simvastatin")
    b.medication_request(p.id, stay.id, "51272", at + timedelta(minutes=10), requester=stay.attending,
                         rid="medrx-ms-medrec-quetiapine")
    return {"stay": stay.id, "patient": p.id, "dose_change": changed}


# ---------- the section ----------


def generate(b: Builder, tl: Timeline, patients: dict[str, PatientInfo], now: datetime) -> dict:
    stays = _current(tl, now)
    out: dict = {"discharge_prescriptions": {}}
    leaving = [s for s in stays if s.discharge <= now + timedelta(hours=24)]
    if not any(_unit(s, now) == "MEDA" for s in leaving):
        meda = [s for s in stays if _unit(s, now) == "MEDA"]
        if meda:
            leaving.append(min(meda, key=lambda s: s.discharge))
    for stay in leaving:
        out["discharge_prescriptions"][stay.id] = discharge_prescriptions(b, stay, patients[stay.patient], now)
    leaving_ids = {s.id for s in leaving}
    # cases on admitted patients who are not leaving today and not the WP4 demo drafts' (the two newest Medicine A stays)
    meda_newest = {s.patient for s in sorted((s for s in stays if _unit(s, now) == "MEDA"), key=lambda s: s.id)[-2:]}
    used: set[str] = set(meda_newest) | {s.patient for s in stays if s.id in leaving_ids}
    staying = [s for s in stays if s.id not in leaving_ids]
    s82 = _pick_82([s for s in staying if s.patient not in used], patients, now)
    if s82:
        out["case_82"] = plant_82(b, s82, patients[s82.patient], now)
        used.add(s82.patient)
    plants = [
        ("allergy", plant_allergy, dict(unit="MEDA", recent=True)),
        ("medrec", plant_admission_medrec, dict(unit="MEDA", recent=True, key=lambda s: (
            any(c in patients[s.patient].conditions for c in NO_ANTIPSYCHOTIC_INDICATION), -s.admit.timestamp()))),
        ("duplicate_test", plant_duplicate_test, dict(unit="MEDA")),
        ("renal", plant_renal, dict(unit="MEDB")),
        ("dose", plant_dose, dict(key=lambda s: (patients[s.patient].age < 76, s.id))),
    ]
    for name, plant, options in plants:
        stay = _pick(staying, used, now=now, **options)
        if stay is None:
            continue
        case = plant(b, stay, patients[stay.patient], now)
        if case:
            out[name] = case
            used.add(stay.patient)
    return out
