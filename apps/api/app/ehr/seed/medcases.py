"""Synthetic eval patients for medication safety (spec 7.2, evals/order_review and evals/medrec).

`materialize(store, case)` writes one eval case as FHIR resources, the way the
generator writes the hospital (it plays the EHR, so it writes the store directly):
a patient with an MRN and AI-processing consent, an outpatient visit holding the
problem list, home medications, allergies and labs, and a current admission on
Medicine A with its diagnosis, its orders, the order under review (or the
discharge prescriptions) and earlier tests. Every id starts with `ev-<case id>-`,
so cases never collide with the seeded hospital. Run it inside a transaction that
is rolled back (scripts/med_safety_eval.py, tests).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.core.store import Store
from app.ehr import codes as C
from app.ehr.reference import IMAGING_ORDERS
from app.ehr.seed.builder import Builder
from app.fhir.dt import fhir_date, fhir_datetime, ref

ALLERGEN_BY_CODE = {a[1]: a for a in C.ALLERGENS}
EVAL_UNIT_BED = "MEDA"  # the stay sits at the unit (no bed), so no seeded bed changes


def _code(entry: dict) -> dict:
    code = entry["code"]
    if entry.get("category") == "imaging":
        return C.concept(C.LOCAL, code, IMAGING_ORDERS.get(code, code))
    return C.loinc(code)


def materialize(store: Store, case: dict, now: datetime, *, index: int = 0) -> dict:
    """Write the case; returns {patient, encounter, order_ref, refs: {home|order|discharge rxcui: ref}}."""
    b = Builder(store, 0, now)
    key = case["id"]
    pid, clinic, stay = f"ev-{key}-pat", f"ev-{key}-clinic", f"ev-{key}-stay"
    p = case["patient"]
    birth = (now - timedelta(days=int(p["age"] * 365.25) + 30)).date()
    b.add({"resourceType": "Patient", "id": pid, "active": True, "gender": p.get("sex", "female"),
           "birthDate": fhir_date(birth),
           "identifier": [{"use": "usual", "system": C.MRN_SYSTEM, "value": f"9{index + 1:07d}"}],
           "name": [{"use": "official", "family": "Evalcase", "given": [key.replace("-", "")],
                     "text": f"{key.replace('-', '')} Evalcase"}]})
    visit = now - timedelta(days=60)
    b.encounter(pid, "AMB", visit, end=visit + timedelta(minutes=30), service="outpatient-clinic", rid=clinic)
    admitted = now - timedelta(hours=10)
    b.encounter(pid, "IMP", admitted, status="in-progress", service="Medicine A", reason=C.snomed(case["encounter_dx"]),
                locations=[(EVAL_UNIT_BED, admitted, None)], admit_source="emd", rid=stay)
    b.add({"resourceType": "Consent", "id": f"ev-{key}-consent", "status": "active",
           "scope": C.concept(C.CONSENT_SCOPE, "patient-privacy"),
           "category": [C.concept(C.CONSENT_CATEGORY, "ai_processing", "ai_processing")],
           "patient": ref("Patient", pid), "dateTime": fhir_datetime(visit),
           "provision": {"type": "permit", "purpose": [C.coding(C.CONSENT_CATEGORY, "ai_processing")]}})
    b.condition(pid, case["encounter_dx"], admitted, enc=stay, category="encounter-diagnosis",
                rid=f"ev-{key}-dx")
    for n, code in enumerate(case.get("conditions") or [], start=1):
        b.condition(pid, code, visit.date() - timedelta(days=400), enc=clinic, rid=f"ev-{key}-cond-{n}")
    for n, code in enumerate(case.get("allergies") or [], start=1):
        b.allergy(pid, ALLERGEN_BY_CODE[code], visit.date() - timedelta(days=900), criticality="high",
                  reaction="Hives", severity="moderate", enc=clinic, rid=f"ev-{key}-alg-{n}")
    labs = [("33914-3", p.get("egfr"), "mL/min/{1.73_m2}", "laboratory"), ("29463-7", p.get("weight"), "kg",
                                                                            "vital-signs"),
            ("6301-6", p.get("inr"), "{INR}", "laboratory"), ("2823-3", p.get("potassium"), "mmol/L", "laboratory")]
    for code, value, unit, category in labs:
        if value is None:
            continue
        display = C.LABS[code][0] if code in C.LABS else C.WEIGHT[1]
        b.observation(pid, stay, (code, display), admitted + timedelta(hours=1), value=value, unit=unit,
                      category=category, rid=f"ev-{key}-obs-{code}")
    refs: dict[str, str] = {}

    def request(entry: dict, rid: str, at: datetime, *, intent: str = "order", category: str = "inpatient") -> str:
        b.medication_request(pid, stay, entry["rxcui"], at, intent=intent, category=category,
                             dose=entry.get("dose"), per_day=entry.get("per_day"), prn=entry.get("prn", False),
                             reason=entry.get("reason"), rid=rid)
        return f"MedicationRequest/{rid}"

    for n, entry in enumerate(case.get("home") or [], start=1):
        rid = f"ev-{key}-home-{n}"
        b.medication_statement(pid, entry["rxcui"], visit.date() - timedelta(days=300), enc=clinic,
                               dose=entry.get("dose"), per_day=entry.get("per_day"), rid=rid)
        refs[f"home:{entry['rxcui']}"] = f"MedicationStatement/{rid}"
    for n, entry in enumerate(case.get("orders") or [], start=1):
        refs[f"order:{entry['rxcui']}"] = request(entry, f"ev-{key}-ord-{n}", admitted + timedelta(hours=1))
    for n, entry in enumerate(case.get("discharge") or [], start=1):
        refs[f"discharge:{entry['rxcui']}"] = request(entry, f"ev-{key}-dc-{n}", now - timedelta(hours=1),
                                                      intent="plan", category="discharge")
    for n, entry in enumerate(case.get("services") or [], start=1):
        authored = now - timedelta(minutes=5) - timedelta(hours=entry["hours_before"])
        rid = f"ev-{key}-sr-{n}"
        resulted = entry.get("resulted_after")
        done = resulted is not None
        b.service_request(pid, stay, _code(entry), authored, category=entry.get("category", "lab"),
                          status="completed" if done else "active",
                          occurrence=authored + timedelta(hours=resulted) if done else None, rid=rid)
        if done and entry.get("category", "lab") == "lab":
            code = entry["code"]
            lo, hi = C.LABS[code][2]
            b.observation(pid, stay, (code, C.LABS[code][0]), authored + timedelta(hours=resulted),
                          value=round((lo + hi) / 2, 2), unit=C.LABS[code][1], category="laboratory",
                          rid=f"obs-{rid}")
        elif done:
            b.diagnostic_report(pid, stay, _code(entry), authored + timedelta(hours=resulted), category="RAD",
                                conclusion="No acute change.", based_on=rid, rid=f"ev-{key}-dr-{n}")
    order = case.get("order")
    order_ref = None
    if order and "rxcui" in order:
        order_ref = request(order, f"ev-{key}-order", now - timedelta(minutes=5))
    elif order:
        rid = f"ev-{key}-test"
        b.service_request(pid, stay, _code({"code": order["service"], "category": order["category"]}),
                          now - timedelta(minutes=5), category=order["category"], rid=rid)
        order_ref = f"ServiceRequest/{rid}"
    return {"patient": pid, "encounter": stay, "order_ref": order_ref, "refs": refs}
