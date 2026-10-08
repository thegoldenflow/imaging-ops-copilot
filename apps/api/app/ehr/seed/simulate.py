"""The hospital timeline: 60 days of ED arrivals, admissions, transfers, surgeries
and discharges up to now, plus the next 36 hours as a plan for the day simulator.

Pure data, no FHIR: `materialize.py` writes the resources as of a point in time.
The ground-truth rules here are what the 7.1 flow models have to learn back:
admission depends on age, CTAS, complaint, arrival mode, first vital signs and
recent admissions; the ED wait on CTAS, crowding against the physician roster
and the hour; surgical duration on procedure, surgeon, ASA class, age and
urgency; discharge timing on the expected length of stay, outstanding orders
and ALC status. Beds are a real constraint: admitted patients board in the ED
until a bed in their unit (or an overflow unit) is free.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.ehr import codes as C
from app.ehr.reference import (
    ADMIT_DX,
    ASA_FACTOR,
    DAY_SURGERY,
    EMERGENCY_OR,
    EXPECTED_LOS,
    OR_BLOCKS,
    OR_DAY,
    OVERFLOW,
    SURGEON_BY_ID,
    SURGEONS,
    TURNOVER_MIN,
    UNITS,
    ed_physicians_on_duty,
)
from app.ehr.seed.people import PatientInfo, bed_ids

WINDOW_DAYS = 60
PLAN_HOURS = 36
DAILY_ED_ARRIVALS = 52
HOUR_WEIGHTS = [0.025, 0.02, 0.018, 0.015, 0.015, 0.018, 0.025, 0.035, 0.05, 0.06, 0.065, 0.065, 0.062, 0.06,
                0.058, 0.056, 0.055, 0.055, 0.052, 0.05, 0.045, 0.04, 0.035, 0.03]
WEEKDAY_FACTOR = [1.12, 1.04, 1.0, 1.0, 1.02, 0.92, 0.9]
COMPLAINT_WEIGHTS = {"chest pain": 0.14, "shortness of breath": 0.12, "abdominal pain": 0.15, "fall": 0.10,
                     "fever": 0.10, "headache": 0.07, "back pain": 0.08, "injury": 0.12, "confusion": 0.04, "rash": 0.05}
CTAS_ADMIT = {1: 2.6, 2: 1.3, 3: 0.0, 4: -1.3, 5: -2.4}
WAIT_BASE = {1: 2, 2: 14, 3: 40, 4: 62, 5: 75}
TREATMENT_BASE = {1: 200, 2: 210, 3: 170, 4: 95, 5: 65}
CLEANING = (45, 100)  # minutes a bed waits for housekeeping after a discharge


def sigmoid(z: float) -> float:
    return 1 / (1 + math.exp(-z))


@dataclass
class EdVisit:
    id: str
    patient: str
    arrival: datetime
    complaint: str
    ctas: int
    mode: str
    vitals: dict[str, float]
    triage: datetime
    seen: datetime
    decision: datetime
    end: datetime
    census: int
    docs: int
    admit: bool = False
    dx: str = ""  # SNOMED code of the ED diagnosis (admission diagnosis when admitted)
    lwbs: bool = False
    bay: str | None = None
    bed_request_at: datetime | None = None
    stay: str | None = None  # Stay id


@dataclass
class Stay:
    id: str
    patient: str
    admit: datetime
    discharge: datetime
    dx: str
    service_unit: str
    beds: list[tuple[str, datetime, datetime]]  # bed, from, to
    source: str  # ed or elective
    expected_los: float
    ed_visit: str | None = None
    surgery: str | None = None
    alc_from: datetime | None = None
    disposition: str = "home"
    attending: str | None = None


@dataclass
class Surgery:
    id: str
    patient: str
    code: str
    surgeon: str
    room: str
    booked_start: datetime
    booked_minutes: int
    start: datetime
    end: datetime
    asa: int
    urgency: str  # elective, emergent
    booked_at: datetime
    status: str = "planned"  # completed / cancelled decided at materialization; cancelled set here
    cancel_reason: str | None = None
    stay: str | None = None
    day_surgery: bool = False


@dataclass
class Timeline:
    start: datetime
    now: datetime
    horizon: datetime
    ed_visits: list[EdVisit] = field(default_factory=list)
    stays: list[Stay] = field(default_factory=list)
    surgeries: list[Surgery] = field(default_factory=list)
    bed_free_at: dict[str, list[tuple[datetime, datetime]]] = field(default_factory=dict)  # cleaning windows

    def stay(self, sid: str) -> Stay:
        return self._stays[sid]

    def index(self) -> None:
        self._stays = {s.id: s for s in self.stays}


class Beds:
    """Interval bookings per bed (a bed is busy from admission to discharge plus cleaning)."""

    def __init__(self, unit_ids: list[str]) -> None:
        self.units = {u: bed_ids(u) for u in unit_ids}
        self.busy: dict[str, list[tuple[datetime, datetime]]] = {b: [] for beds in self.units.values() for b in beds}

    def free(self, bed: str, start: datetime, end: datetime) -> bool:
        return all(end <= a or start >= b for a, b in self.busy[bed])

    def find(self, units: list[str], start: datetime, end: datetime) -> str | None:
        for unit in units:
            for bed in self.units[unit]:
                if self.free(bed, start, end):
                    return bed
        return None

    def earliest(self, units: list[str], start: datetime, duration: timedelta, limit: datetime) -> tuple[str, datetime] | None:
        candidates = {start} | {b for unit in units for bed in self.units[unit] for _, b in self.busy[bed] if b > start}
        for t in sorted(candidates):
            if t > limit:
                break
            bed = self.find(units, t, t + duration)
            if bed:
                return bed, t
        return None

    def book(self, bed: str, start: datetime, end: datetime) -> None:
        self.busy[bed].append((start, end))


def _poisson(rng: random.Random, lam: float) -> int:
    threshold, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= threshold:
            return k
        k += 1


def surgical_minutes(rng: random.Random, code: str, surgeon_id: str, asa: int, age: int, emergent: bool,
                     start: datetime) -> int:
    """Ground truth for the surgical duration model."""
    base = C.OR_PROCEDURES[code][1]
    factor = SURGEON_BY_ID[surgeon_id].speed * ASA_FACTOR[asa] * (1 + 0.004 * (age - 60))
    factor *= 1.12 if emergent else 1.0
    factor *= 1.05 if start.hour >= 13 else 1.0
    return max(20, int(base * factor * math.exp(rng.gauss(0, 0.11))))


def _asa(rng: random.Random, p: PatientInfo) -> int:
    score = 1 + (p.age >= 40) + (len(p.conditions) >= 2) + (len(p.conditions) >= 4 or p.age >= 85)
    if rng.random() < 0.15:
        score += rng.choice([-1, 1])
    return max(1, min(4, score))


def _vitals(rng: random.Random, p: PatientInfo, complaint: str, dx: str, ctas: int) -> dict[str, float]:
    sick = (5 - ctas) / 4  # 0 (CTAS 5) .. 1 (CTAS 1)
    v = {"8867-4": rng.gauss(80, 10) + 25 * sick, "8480-6": rng.gauss(132, 16) - 10 * sick,
         "8462-4": rng.gauss(78, 9), "9279-1": rng.gauss(16, 2) + 6 * sick, "8310-5": rng.gauss(36.8, 0.3),
         "59408-5": rng.gauss(97, 1.2) - 3 * sick, "72514-3": rng.randint(0, 4), "9269-2": 15}
    if complaint in ("shortness of breath",) or dx in ("233604007", "42343007", "195951007"):
        v["59408-5"] -= rng.uniform(3, 8)
        v["9279-1"] += rng.uniform(3, 8)
    if complaint == "fever" or dx in ("91302008", "233604007", "68566005"):
        v["8310-5"] += rng.uniform(1.0, 2.4)
        v["8867-4"] += 12
    if dx == "91302008":
        v["8480-6"] -= 25
    if complaint in ("fall", "injury", "abdominal pain", "back pain", "chest pain"):
        v["72514-3"] = rng.randint(5, 9)
    if complaint == "confusion" or dx == "230690007":
        v["9269-2"] = rng.choice([12, 13, 14])
    v["59408-5"] = min(100, v["59408-5"])
    return {k: round(val, 1) if k == "8310-5" else round(val) for k, val in v.items()}


def simulate(rng_for, patients: list[PatientInfo], doctors: dict[str, list[str]], now: datetime) -> Timeline:
    """rng_for(name) gives a dedicated random stream; doctors maps unit -> physician ids."""
    rng = rng_for("timeline")
    start = (now - timedelta(days=WINDOW_DAYS)).replace(hour=0, minute=0, second=0, microsecond=0)
    horizon = now + timedelta(hours=PLAN_HOURS)
    tl = Timeline(start=start, now=now, horizon=horizon)
    beds = Beds([u.id for u in UNITS if u.kind != "ed"])
    bays = Beds(["ED"])
    by_id = {p.id: p for p in patients}
    busy_until: dict[str, datetime] = {}
    admissions: dict[str, list[datetime]] = {p.id: list(p.prior_admissions) for p in patients}
    emergency_or_free = start
    counters = {"ed": 0, "stay": 0, "surg": 0}

    def next_id(kind: str) -> str:
        counters[kind] += 1
        return f"{kind}-{counters[kind]:05d}"

    # ---- elective surgical schedule (booked in advance) ----
    electives: list[Surgery] = []
    erng = rng_for("electives")
    day = start
    while day < horizon:
        blocks = OR_BLOCKS.get(day.weekday(), {})
        for room, surgeon_id in blocks.items():
            surgeon = SURGEON_BY_ID[surgeon_id]
            booked = day.replace(hour=OR_DAY[0])
            actual_free = booked
            block_end = day.replace(hour=OR_DAY[1])
            while True:
                code = erng.choice(surgeon.procedures)
                minutes = int(math.ceil(C.OR_PROCEDURES[code][1] / 15) * 15)
                if booked + timedelta(minutes=minutes) > block_end:
                    break
                candidates = [p for p in erng.sample(patients, 40) if 30 <= p.age <= 88]
                if code in ("52734007", "609588000"):
                    candidates.sort(key=lambda p: "396275006" not in p.conditions)
                patient = candidates[0]
                asa = _asa(erng, patient)
                actual_start = max(booked, actual_free)
                dur = surgical_minutes(erng, code, surgeon_id, asa, patient.age, False, actual_start)
                s = Surgery(id=next_id("surg"), patient=patient.id, code=code, surgeon=surgeon_id, room=room,
                            booked_start=booked, booked_minutes=minutes, start=actual_start,
                            end=actual_start + timedelta(minutes=dur), asa=asa, urgency="elective",
                            booked_at=booked - timedelta(days=erng.randint(7, 45)),
                            day_surgery=code in DAY_SURGERY and erng.random() < 0.9)
                electives.append(s)
                actual_free = s.end + timedelta(minutes=TURNOVER_MIN)
                booked = booked + timedelta(minutes=minutes + TURNOVER_MIN)
        day += timedelta(days=1)

    # ---- ED arrivals ----
    arrivals: list[datetime] = []
    arng = rng_for("arrivals")
    t = start
    while t < horizon:
        lam = DAILY_ED_ARRIVALS * HOUR_WEIGHTS[t.hour] * WEEKDAY_FACTOR[t.weekday()]
        for _ in range(_poisson(arng, lam)):
            arrivals.append(t + timedelta(minutes=arng.randint(0, 59), seconds=arng.randint(0, 59)))
        t += timedelta(hours=1)

    events = [(a, "ed", None) for a in arrivals] + [
        (s.booked_start.replace(hour=6, minute=0) if not s.day_surgery else s.booked_start - timedelta(hours=1, minutes=30),
         "elective", s) for s in electives]
    events.sort(key=lambda e: (e[0], e[1]))

    ed_active: list[datetime] = []  # ED end times of patients still in the ED
    weights = [1 + p.burden ** 1.3 for p in patients]
    vrng = rng_for("visits")

    def pick_patient(at: datetime) -> PatientInfo:
        for _ in range(50):
            p = vrng.choices(patients, weights=weights)[0]
            if busy_until.get(p.id, start) <= at:
                return p
        return p

    def admit(p: PatientInfo, at: datetime, dx: str, unit: str, icu: bool, source: str, ed_visit: EdVisit | None,
              expected: float, surgery: Surgery | None) -> Stay | None:
        los_days = expected * math.exp(vrng.gauss(0, 0.33))
        alc_from = None
        if p.age >= 75 and unit not in ("ICU",) and vrng.random() < 0.06:
            alc_from = at + timedelta(days=expected)
            los_days = expected + vrng.uniform(5, 16)
        if surgery is not None:
            los_days = max(los_days, (surgery.end - at).total_seconds() / 86400 + C.OR_PROCEDURES[surgery.code][3] * 0.8)
        discharge = at + timedelta(days=los_days)
        discharge = discharge.replace(hour=vrng.choice([10, 11, 11, 12, 13, 14, 15, 16]), minute=vrng.randint(0, 59))
        if discharge < at + timedelta(hours=18):
            discharge = at + timedelta(hours=18 + vrng.randint(0, 6))
        segments: list[tuple[str, datetime, datetime]] = []
        clean = timedelta(minutes=vrng.randint(*CLEANING))
        if icu:
            transfer = at + timedelta(days=max(1.5, los_days * vrng.uniform(0.45, 0.65)))
            icu_bed = beds.find(["ICU"], at, transfer + clean)
            if icu_bed:
                beds.book(icu_bed, at, transfer + clean)
                segments.append((icu_bed, at, transfer))
                at_ward = transfer
            else:
                at_ward = at
        else:
            at_ward = at
        ward_unit = "SURG" if unit == "ICU" else unit  # step-down after cardiac surgery
        units = [ward_unit] + OVERFLOW.get(ward_unit, [])
        ward_bed = beds.find(units, at_ward, discharge + clean)
        if ward_bed is None and not segments:
            return None
        if ward_bed is None:  # stays in ICU
            bed, since, _ = segments.pop()
            beds.busy[bed][-1] = (since, discharge + clean)
            segments.append((bed, since, discharge))
        else:
            beds.book(ward_bed, at_ward, discharge + clean)
            segments.append((ward_bed, at_ward, discharge))
        disposition = "home"
        if alc_from:
            disposition = vrng.choice(["long", "snf"])
        elif p.age >= 80 and vrng.random() < 0.25:
            disposition = vrng.choice(["rehab", "snf", "home"])
        stay = Stay(id=next_id("stay"), patient=p.id, admit=at, discharge=discharge, dx=dx, service_unit=unit,
                    beds=segments, source=source, expected_los=expected, ed_visit=ed_visit.id if ed_visit else None,
                    surgery=surgery.id if surgery else None, alc_from=alc_from, disposition=disposition,
                    attending=vrng.choice(doctors.get(unit) or doctors["MEDA"]))
        tl.stays.append(stay)
        busy_until[p.id] = discharge
        admissions[p.id].append(at)
        return stay

    for at, kind, surgery in events:
        if kind == "elective":
            s: Surgery = surgery  # type: ignore[assignment]
            p = by_id[s.patient]
            if busy_until.get(p.id, start) > at:
                if vrng.random() < 0.2:
                    s.status, s.cancel_reason = "cancelled", "Patient admitted elsewhere"
                    tl.surgeries.append(s)
                    continue
                # The slot went to another patient from the waiting list.
                p = next(c for c in vrng.sample(patients, 60) if 30 <= c.age <= 88 and busy_until.get(c.id, start) <= at)
                s.patient = p.id
            if s.day_surgery:
                busy_until[p.id] = s.end + timedelta(hours=4)
                tl.surgeries.append(s)
                continue
            unit = C.OR_PROCEDURES[s.code][2]
            stay = admit(p, at, s.code, unit, unit == "ICU", "elective", None, C.OR_PROCEDURES[s.code][3] + 0.5, s)
            if stay is None:
                s.status, s.cancel_reason = "cancelled", "No inpatient bed available"
            else:
                s.stay = stay.id
            tl.surgeries.append(s)
            continue

        # ED arrival
        p = pick_patient(at)
        while ed_active and min(ed_active) <= at:
            ed_active.remove(min(ed_active))
        census = len(ed_active)
        docs = ed_physicians_on_duty(at)
        cw = dict(COMPLAINT_WEIGHTS)
        if p.age >= 75:
            cw["fall"] *= 2.5
            cw["confusion"] *= 3
            cw["shortness of breath"] *= 1.6
            cw["injury"] *= 0.5
        complaint = vrng.choices(list(cw), weights=list(cw.values()))[0]
        code, ctas_weights, offset = C.COMPLAINTS[complaint]
        ctas = vrng.choices([1, 2, 3, 4, 5], weights=ctas_weights)[0]
        if p.age >= 80 and ctas > 1 and vrng.random() < 0.2:
            ctas -= 1
        dx_options = ADMIT_DX[complaint]
        dx_code, _w, unit, icu_share, surgery_code = vrng.choices(dx_options, weights=[o[1] for o in dx_options])[0]
        vitals = _vitals(vrng, p, complaint, dx_code, ctas)
        p_amb = 0.15 + 0.12 * (5 - ctas) + (0.2 if p.age >= 75 else 0)
        mode = "ambulance" if vrng.random() < p_amb else vrng.choices(["walk-in", "police", "transfer"], weights=[0.94, 0.02, 0.04])[0]
        recent = sum(1 for a in admissions[p.id] if timedelta(0) < at - a <= timedelta(days=365))
        z = -3.5 + offset + 0.03 * (p.age - 55) + CTAS_ADMIT[ctas] + (1.3 if vitals["59408-5"] < 92 else 0)
        z += (0.6 if vitals["8867-4"] > 110 else 0) + 0.45 * min(recent, 3) + (0.5 if mode == "ambulance" else 0)
        z += vrng.gauss(0, 0.6)
        will_admit = vrng.random() < sigmoid(z)

        triage = at + timedelta(minutes=1 if ctas == 1 else vrng.randint(3, 12))
        crowd = max(0.0, census - 2.5 * docs)
        wait = WAIT_BASE[ctas] * (1 + 0.09 * crowd) * math.exp(vrng.gauss(0, 0.35))
        if ctas >= 3:
            wait += max(0.0, vrng.gauss(5, 3))
        if at.hour < 7:
            wait *= 1.15
        seen = triage + timedelta(minutes=wait)
        visit = EdVisit(id=next_id("ed"), patient=p.id, arrival=at, complaint=complaint, ctas=ctas, mode=mode,
                        vitals=vitals, triage=triage, seen=seen, decision=seen, end=seen, census=census, docs=docs)
        if ctas >= 4 and wait > 150 and vrng.random() < 0.35:
            visit.lwbs = True
            visit.end = visit.decision = at + timedelta(minutes=wait * 0.8)
            visit.dx = code
        else:
            treatment = TREATMENT_BASE[ctas] * math.exp(vrng.gauss(0, 0.3))
            visit.decision = seen + timedelta(minutes=treatment)
            if will_admit:
                visit.admit = True
                visit.dx = dx_code
                visit.bed_request_at = visit.decision
                icu = vrng.random() < icu_share
                expected = EXPECTED_LOS[dx_code]
                target_unit = "ICU" if icu else unit
                # Emergency surgery for surgical diagnoses
                surg = None
                if surgery_code:
                    delay_h = {"52734007": vrng.uniform(18, 36), "80146002": vrng.uniform(4, 10),
                               "45595009": vrng.uniform(24, 48)}[surgery_code]
                    target = visit.decision + timedelta(hours=delay_h)
                    if target.weekday() < 5 and OR_DAY[0] <= target.hour < OR_DAY[1]:
                        target = target.replace(hour=OR_DAY[1], minute=0)
                    s_start = max(target, emergency_or_free)
                    surgeon = vrng.choice([s for s in SURGEONS if surgery_code in s.procedures])
                    asa = _asa(vrng, p)
                    dur = surgical_minutes(vrng, surgery_code, surgeon.id, asa, p.age, True, s_start)
                    surg = Surgery(id=next_id("surg"), patient=p.id, code=surgery_code, surgeon=surgeon.id,
                                   room=EMERGENCY_OR, booked_start=s_start,
                                   booked_minutes=int(math.ceil(C.OR_PROCEDURES[surgery_code][1] / 15) * 15),
                                   start=s_start, end=s_start + timedelta(minutes=dur), asa=asa, urgency="emergent",
                                   booked_at=visit.decision)
                    emergency_or_free = surg.end + timedelta(minutes=TURNOVER_MIN)
                units = [target_unit] + OVERFLOW.get(target_unit, [])
                need = timedelta(days=expected)
                found = beds.earliest(units, visit.decision + timedelta(minutes=20), need, visit.decision + timedelta(days=3))
                bed_time = found[1] if found else visit.decision + timedelta(hours=vrng.randint(6, 30))
                stay = admit(p, bed_time, dx_code, target_unit if not icu else unit, icu, "ed", visit, expected, surg)
                if stay is None:  # no bed even after waiting: admitted to the ED overflow, treated as an ED stay
                    visit.admit = False
                    visit.end = visit.decision + timedelta(hours=vrng.randint(8, 20))
                else:
                    visit.stay = stay.id
                    visit.end = stay.admit
                    if surg:
                        surg.stay = stay.id
                        tl.surgeries.append(surg)
            else:
                visit.dx = code
                visit.end = visit.decision + timedelta(minutes=vrng.randint(10, 40))
        bay_found = bays.find(["ED"], visit.seen, visit.end)
        if bay_found:
            bays.book(bay_found, visit.seen, visit.end)
            visit.bay = bay_found
        ed_active.append(visit.end)
        busy_until[p.id] = max(busy_until.get(p.id, start), visit.end)
        tl.ed_visits.append(visit)

    tl.surgeries.sort(key=lambda s: s.booked_start)
    # Housekeeping windows: a bed vacated at a discharge or transfer is "to be cleaned" until the next booking starts.
    for bed, slots in beds.busy.items():
        tl.bed_free_at[bed] = sorted(slots)
    tl.index()
    return tl
