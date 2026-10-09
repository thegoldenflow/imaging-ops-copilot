"""Write the timeline as FHIR resources as of `now`, and the rest as the simulator's plan.

Resource ids come from the timeline: an ED visit `ed-01234` is Encounter
`ed-01234`, an admission `stay-00456` is Encounter `stay-00456`, a surgery
`surg-00012` is Appointment `surg-00012` with Procedure `proc-surg-00012`, and
the ED bed request is ServiceRequest `bedreq-ed-01234`. Events after `now` (up to
the plan horizon) go into the plan; the day simulator (app/ehr/simulator.py)
writes their resources when the hospital clock reaches them.
"""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from app.ehr import codes as C
from app.ehr.reference import CONSULTS, IMAGING_ORDERS, INPATIENT_MEDS, ORDER_SETS, POSTOP_MEDS, UNIT_BY_ID
from app.ehr.fhirstore import unit_of
from app.ehr.seed.builder import Builder, ext
from app.ehr.seed.people import PatientInfo
from app.ehr.seed.simulate import EdVisit, Stay, Surgery, Timeline
from app.fhir.dt import fhir_datetime, ref


@dataclass
class OrderSpec:
    id: str
    patient: str
    encounter: str
    kind: str  # lab, imaging, consult
    code: str
    authored: datetime
    completed: datetime
    value: float | None = None  # lab result


def _rng(b: Builder, *parts: str) -> random.Random:
    return random.Random(f"hospital:{b.seed}:" + ":".join(parts))


# ---------- orders and results ----------


def _lab_value(rng: random.Random, code: str, p: PatientInfo, dx: str) -> float:
    lo, hi = C.LABS[code][2]
    value = rng.uniform(lo, hi)
    if code == "33914-3":
        value = p.egfr * rng.uniform(0.85, 1.05)
    elif code == "2160-0":
        value = 88 / max(p.egfr, 8) * rng.uniform(0.9, 1.15)
    elif code == "6301-6":
        value = rng.uniform(2.0, 3.6) if "11289" in p.home_meds else rng.uniform(0.9, 1.2)
    elif code == "6690-2" and dx in ("91302008", "233604007", "74400008", "65275009"):
        value = rng.uniform(12, 22)
    elif code == "10839-9" and dx == "22298006":
        value = rng.uniform(0.3, 4.0)
    elif code == "33762-6" and dx == "42343007":
        value = rng.uniform(900, 6000)
    elif code == "2524-7" and dx == "91302008":
        value = rng.uniform(2.2, 5.0)
    elif code == "1988-5" and dx in ("91302008", "233604007"):
        value = rng.uniform(40, 220)
    return round(value, 2 if hi < 10 else 1)


def stay_orders(b: Builder, stay: Stay, p: PatientInfo) -> list[OrderSpec]:
    """Orders over the stay: busy early, tapering off, none in the last ~20 hours
    before discharge (the signal the discharge-readiness model picks up)."""
    rng = _rng(b, "orders", stay.id)
    out: list[OrderSpec] = []
    los_days = max(1, int((stay.discharge - stay.admit).total_seconds() // 86400) + 1)
    first_set = ORDER_SETS["default"] + ORDER_SETS.get(stay.dx, [])
    n = 0
    for day in range(los_days):
        day_start = stay.admit + timedelta(days=day)
        if day == 0:
            batch = list(first_set)
        else:
            lam = 3.2 * max(0.12, 1 - day / max(stay.expected_los, 1))
            if stay.alc_from and day_start >= stay.alc_from:
                lam = 0.3
            k = sum(rng.random() < lam / 4 for _ in range(4))
            pool = ORDER_SETS["default"] + ORDER_SETS.get(stay.dx, [])
            batch = [rng.choice(pool) for _ in range(k)]
            if p.age >= 75 and day == 1:
                batch += [("consult", rng.choice(["physio", "ot"]))]
            if stay.alc_from and day_start.date() == stay.alc_from.date():
                batch += [("consult", "sw")]
        for kind, code in batch:
            authored = day_start + timedelta(hours=rng.uniform(0.5, 10))
            if authored > stay.discharge - timedelta(hours=20) and day > 0:
                continue
            delay = {"lab": rng.uniform(1.5, 8), "imaging": rng.uniform(3, 26), "consult": rng.uniform(10, 40)}[kind]
            completed = min(authored + timedelta(hours=delay), stay.discharge - timedelta(hours=2))
            n += 1
            value = _lab_value(rng, code, p, stay.dx) if kind == "lab" else None
            out.append(OrderSpec(f"ord-{stay.id}-{n:02d}", p.id, stay.id, kind, code, authored, completed, value))
    return out


def ed_orders(b: Builder, visit: EdVisit, p: PatientInfo) -> list[OrderSpec]:
    rng = _rng(b, "ed-orders", visit.id)
    pool = ["6690-2", "718-7", "2160-0", "2823-3"] + {"chest pain": ["10839-9"], "shortness of breath": ["33762-6"],
                                                       "fever": ["2524-7", "1988-5"]}.get(visit.complaint, [])
    out = []
    for i, code in enumerate(rng.sample(pool, k=min(len(pool), rng.randint(1, 3))), start=1):
        authored = visit.seen + timedelta(minutes=rng.randint(5, 25))
        out.append(OrderSpec(f"ord-{visit.id}-{i:02d}", p.id, visit.id, "lab", code, authored,
                             authored + timedelta(minutes=rng.randint(40, 160)), _lab_value(rng, code, p, visit.dx)))
    if visit.complaint in ("fall", "injury", "chest pain", "shortness of breath") and visit.ctas <= 3:
        img = {"fall": "XR_HIP", "injury": "XR_HIP", "chest pain": "XR_CHEST", "shortness of breath": "XR_CHEST"}[visit.complaint]
        authored = visit.seen + timedelta(minutes=rng.randint(10, 40))
        out.append(OrderSpec(f"ord-{visit.id}-img", p.id, visit.id, "imaging", img, authored,
                             authored + timedelta(minutes=rng.randint(60, 200))))
    return out


def order_code(order: OrderSpec) -> dict:
    if order.kind == "lab":
        return C.loinc(order.code)
    if order.kind == "imaging":
        return C.concept(C.LOCAL, order.code, IMAGING_ORDERS[order.code])
    return C.concept(C.LOCAL, order.code, CONSULTS[order.code])


def write_order(b: Builder, order: OrderSpec, as_of: datetime) -> None:
    done = order.completed <= as_of
    b.service_request(order.patient, order.encounter, order_code(order), order.authored, rid=order.id,
                      category=order.kind, status="completed" if done else "active",
                      occurrence=order.completed if done else None)
    if done:
        write_result(b, order)


def write_result(b: Builder, order: OrderSpec) -> None:
    if order.kind == "lab":
        code, (display, unit, (lo, hi)) = order.code, C.LABS[order.code]
        flag = "H" if order.value > hi else ("L" if order.value < lo else "N")
        b.observation(order.patient, order.encounter, (code, display), order.completed, value=order.value, unit=unit,
                      category="laboratory", interpretation=flag, rid=f"obs-{order.id}")
    elif order.kind == "imaging":
        b.diagnostic_report(order.patient, order.encounter, order_code(order), order.completed, category="RAD",
                            conclusion=f"{IMAGING_ORDERS[order.code]}: findings documented in the imaging report.",
                            based_on=order.id)


# ---------- vitals ----------


def vitals_panel(b: Builder, p: PatientInfo, enc: str, when: datetime, vitals: dict[str, float], rid: str | None = None) -> None:
    components = [(code, value, C.VITALS[code][1]) for code, value in vitals.items()]
    b.observation(p.id, enc, C.VITAL_PANEL, when, components=components, rid=rid)


def ward_vitals(rng: random.Random, base: dict[str, float], hours_in: float) -> dict[str, float]:
    """Vital signs settling towards normal over the stay."""
    recover = min(1.0, hours_in / 72)
    target = {"8867-4": 78, "8480-6": 128, "8462-4": 76, "9279-1": 16, "8310-5": 36.8, "59408-5": 96,
              "72514-3": 2, "9269-2": 15}
    out = {}
    for code, value in base.items():
        v = value + (target[code] - value) * recover + rng.gauss(0, {"8310-5": 0.2, "59408-5": 0.8}.get(code, 3))
        out[code] = round(min(100, v), 1) if code in ("8310-5", "59408-5") else round(v)
    out["59408-5"] = min(100, out["59408-5"])
    out["72514-3"] = max(0, min(10, round(out["72514-3"])))
    out["9269-2"] = max(3, min(15, round(out["9269-2"])))
    return out


# ---------- ED ----------


def write_ed_visit(b: Builder, v: EdVisit, p: PatientInfo, stay: Stay | None, as_of: datetime) -> None:
    history = [("arrived", v.arrival, min(v.triage, as_of) if v.triage <= as_of else None)]
    if v.triage <= as_of:
        history.append(("triaged", v.triage, v.seen if v.seen <= as_of else None))
    if v.seen <= as_of and not v.lwbs:
        history.append(("in-progress", v.seen, v.end if v.end <= as_of else None))
    finished = v.end <= as_of
    status = "finished" if finished else history[-1][0]
    locations = [("ED", v.arrival, (v.seen if v.bay and v.seen <= as_of else None) if not finished else (v.seen if v.bay else v.end))]
    if v.bay and v.seen <= as_of:
        locations.append((v.bay, v.seen, v.end if finished else None))
    disposition = None
    outcome = None
    if finished:
        outcome = "lwbs" if v.lwbs else ("admitted" if v.admit else "home")
        disposition = {"lwbs": "aadvice", "admitted": "oth", "home": "home"}[outcome]
    exts = [ext("arrival-mode", valueCode=v.mode)]
    if outcome:
        exts.append(ext("ed-disposition", valueCode=outcome))
    b.encounter(p.id, "EMER", v.arrival, end=v.end if finished else None, status=status, service="emergency",
                reason=C.concept(C.SNOMED, C.COMPLAINTS[v.complaint][0], v.complaint),
                locations=locations, status_history=history, disposition=disposition, extensions=exts, rid=v.id)
    if v.triage <= as_of:
        b.observation(p.id, v.id, C.CTAS, v.triage, value=v.ctas, category="survey", rid=f"ctas-{v.id}")
        vitals_panel(b, p, v.id, v.triage, v.vitals, rid=f"vit-{v.id}")
    if v.decision <= as_of and not v.lwbs:
        b.condition(p.id, v.dx, v.decision, enc=v.id, category="encounter-diagnosis",
                    clinical="active" if v.admit else "resolved")
    if v.admit and v.bed_request_at and v.bed_request_at <= as_of:
        assigned = stay is not None and stay.admit <= as_of
        b.service_request(p.id, v.id, C.concept(C.SNOMED, "32485007", "Hospital admission"), v.bed_request_at,
                          category="bed-request", status="completed" if assigned else "active", priority="urgent",
                          occurrence=stay.admit if assigned else None,
                          location=unit_of(stay.beds[0][0]) if stay else None, reason=v.dx, rid=f"bedreq-{v.id}")


# ---------- inpatient ----------


def write_stay(b: Builder, s: Stay, p: PatientInfo, surgery: Surgery | None, ed: EdVisit | None, as_of: datetime) -> list[OrderSpec]:
    """Writes the stay as of `as_of`; returns its orders that come after (for the plan)."""
    rng = _rng(b, "stay", s.id)
    discharged = s.discharge <= as_of
    segments = [(bed, since, until if until <= as_of else None) for bed, since, until in s.beds if since <= as_of]
    admit_source = "emd" if s.source == "ed" else "outp"
    exts = [ext("expected-los-days", valueDecimal=s.expected_los)]
    b.encounter(p.id, "IMP", s.admit, end=s.discharge if discharged else None, service=UNIT_BY_ID[s.service_unit].name,
                reason=C.snomed(s.dx), locations=segments, admit_source=admit_source,
                disposition=s.disposition if discharged else None,
                based_on=f"bedreq-{ed.id}" if ed else None, practitioner=s.attending, extensions=exts, rid=s.id)
    b.condition(p.id, s.dx, s.admit, enc=s.id, category="encounter-diagnosis",
                clinical="resolved" if discharged else "active")
    # Medications: most home medications continued, plus diagnosis-specific ones
    status = "completed" if discharged else "active"
    for rxcui in p.home_meds:
        if rng.random() < 0.8:
            b.medication_request(p.id, s.id, rxcui, s.admit + timedelta(hours=rng.uniform(1, 4)),
                                 status=status, requester=s.attending)
    for rxcui in INPATIENT_MEDS.get(s.dx, []):
        if rxcui in p.home_meds:
            continue
        b.medication_request(p.id, s.id, rxcui, s.admit + timedelta(hours=rng.uniform(0.5, 3)), status=status,
                             requester=s.attending, reason=s.dx, prn=rxcui in ("3423", "7052", "26225"))
    if surgery and surgery.end <= as_of:
        for rxcui in POSTOP_MEDS:
            if rxcui not in p.home_meds and rxcui not in INPATIENT_MEDS.get(s.dx, []):
                b.medication_request(p.id, s.id, rxcui, surgery.end + timedelta(minutes=30), status=status,
                                     requester=surgery.surgeon, prn=rxcui in ("3423", "26225"))
    # Orders and results
    orders = stay_orders(b, s, p)
    future = []
    for order in orders:
        if order.authored <= as_of:
            write_order(b, order, as_of)
            if order.completed > as_of:
                future.append(order)
        else:
            future.append(order)
    # Vital signs: on admission, then every 6 hours for current patients (last 48 h), the day before discharge otherwise
    base = ed.vitals if ed else {"8867-4": 76, "8480-6": 130, "8462-4": 78, "9279-1": 15, "8310-5": 36.7,
                                 "59408-5": 97, "72514-3": 1, "9269-2": 15}
    vitals_panel(b, p, s.id, s.admit + timedelta(minutes=30), ward_vitals(rng, base, 0), rid=f"vit-{s.id}-adm")
    if not discharged:
        t = max(s.admit + timedelta(hours=6), as_of - timedelta(hours=48))
        t = t.replace(minute=0, second=0, microsecond=0)
        t += timedelta(hours=(6 - t.hour % 6) % 6)
        while t <= as_of:
            vitals_panel(b, p, s.id, t, ward_vitals(rng, base, (t - s.admit).total_seconds() / 3600))
            t += timedelta(hours=6)
    else:
        when = s.discharge - timedelta(hours=18)
        vitals_panel(b, p, s.id, when, ward_vitals(rng, base, (when - s.admit).total_seconds() / 3600))
    # Flags
    if s.alc_from and s.alc_from <= as_of:
        b.flag(p.id, ("alc", "Alternate level of care"), s.alc_from, enc=s.id,
               status="inactive" if discharged else "active", end=s.discharge if discharged else None)
    if p.age >= 80:
        b.flag(p.id, ("fall-risk", "Fall risk"), s.admit + timedelta(hours=2), enc=s.id,
               status="inactive" if discharged else "active", end=s.discharge if discharged else None)
    return future


# ---------- surgery ----------


def write_surgery(b: Builder, sg: Surgery, p: PatientInfo, as_of: datetime, horizon: datetime,
                  stay: Stay | None = None) -> None:
    rng = _rng(b, "surgery", sg.id)
    if sg.booked_at > as_of:
        return
    name = C.OR_PROCEDURES[sg.code][0]
    cancelled = sg.status == "cancelled" and sg.booked_start - timedelta(hours=1) <= as_of
    done = not cancelled and sg.end <= as_of
    started = not cancelled and sg.start <= as_of
    # Booked at a clinic visit (elective) or from the ED (emergent); the admission is referenced once it exists.
    booking_encounter = (stay.ed_visit if stay and stay.ed_visit else None) or p.clinic_encounter
    b.service_request(p.id, booking_encounter, C.snomed(sg.code), sg.booked_at,
                      category="surgery", priority="urgent" if sg.urgency == "emergent" else "routine",
                      status="revoked" if cancelled else ("completed" if done else "active"),
                      occurrence=sg.booked_start, requester=sg.surgeon, rid=f"orreq-{sg.id}",
                      note=sg.cancel_reason if cancelled else None)
    if stay is not None:
        encounter = stay.id if stay.admit <= as_of else booking_encounter
    else:
        arrival = sg.booked_start - timedelta(hours=1, minutes=30)
        encounter = f"amb-{sg.id}" if sg.day_surgery and arrival <= as_of else booking_encounter
    status = "cancelled" if cancelled else ("fulfilled" if done else ("arrived" if started else "booked"))
    appointment = b.appointment(p.id, sg.booked_start, sg.booked_start + timedelta(minutes=sg.booked_minutes),
                                status=status, service=C.snomed(sg.code), location=sg.room, practitioner=sg.surgeon,
                                based_on=f"orreq-{sg.id}", encounter=encounter, minutes=sg.booked_minutes,
                                description=name, extensions=[ext("asa-class", valueInteger=sg.asa),
                                                              ext("surgical-urgency", valueCode=sg.urgency)])
    if sg.day_surgery and not cancelled:
        arrival = sg.booked_start - timedelta(hours=1, minutes=30)
        if arrival <= as_of:
            leave = sg.end + timedelta(hours=4)
            b.encounter(p.id, "AMB", arrival, end=leave if leave <= as_of else None, service="day-surgery",
                        reason=C.snomed(sg.code), locations=[("SURG", arrival, leave if leave <= as_of else None)],
                        rid=f"amb-{sg.id}")
    if started:
        b.procedure(p.id, encounter, sg.code, sg.start, sg.end if done else None,
                    status="completed" if done else "in-progress", performer=sg.surgeon, location=sg.room,
                    asa=sg.asa, urgency=sg.urgency, based_on=f"orreq-{sg.id}", rid=f"proc-{sg.id}")
    # Pre-operative checklist for cases not yet started within the planning horizon
    if not cancelled and not started and sg.booked_start <= horizon:
        hours_to_go = (sg.booked_start - as_of).total_seconds() / 3600
        for code, label in C.PREOP_CHECKS.items():
            miss = {"preop-consent": 0.08, "preop-npo": 0.0, "preop-labs": 0.14, "preop-blood-type": 0.07}[code]
            done_check = rng.random() >= miss
            if code == "preop-npo":
                done_check = hours_to_go < 10
            b.task(p.id, (code, label), min(as_of, sg.booked_at + timedelta(days=1)),
                   status="completed" if done_check else "requested", enc=encounter,
                   focus=f"Appointment/{appointment['id']}", owner_role="nurse",
                   description=f"{label} before {name}", due=sg.booked_start - timedelta(hours=1),
                   priority="urgent" if sg.urgency == "emergent" else "routine")


def _jsonable(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    return value


def plan_dict(obj) -> dict:
    """A timeline object as JSON-ready data for the simulator's plan."""
    return _jsonable(asdict(obj))
