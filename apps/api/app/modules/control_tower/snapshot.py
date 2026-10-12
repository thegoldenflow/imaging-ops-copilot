"""The Control Tower's view of the hospital: ED, bed and OR boards from FHIR (spec 7.1).

| Board | Rows | Aggregates | FHIR |
| --- | --- | --- | --- |
| ED | arrival, CTAS, waiting, admission probability, orders (open / total), target unit | in the ED, waiting for a bed, predicted wait in the next hour, at risk of leaving unseen | Encounter (EMER), Observation (CTAS, first vital signs), ServiceRequest |
| Beds | unit, occupied / total, cleaning, ALC, expected discharges (24 h), ED boarders | hospital occupancy, net gap = expected admissions - expected discharges - free beds | Location, Encounter (IMP), Flag |
| OR | case, surgeon, booked and predicted minutes, overrun risk, pre-op readiness (consent, fasting, labs, blood group) | block utilisation, cases at risk of cancellation, predicted end of the day | Appointment, Procedure, Task, Patient |

Everything is read through FhirGateway as the Control Tower itself (`tower_fhir`:
the `control_tower` registry entry's hospital-wide data scope), ten audited
searches per build. The snapshot holds MRNs and clinical values; what a user
sees is shaped per role in service.py. A built snapshot is reused while nothing
changed: the key is the store's change counter (every commit bumps it, and the
day simulator commits its FHIR writes and their domain events together), the
hospital clock and the last domain event, so the boards follow the simulator
within one refresh.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta

from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.agents import registry
from app.agents.gateway import agent_actor
from app.core.store import Store
from app.ehr import codes as C
from app.ehr.access import ACTIVE_ENCOUNTER
from app.ehr.clock import hospital_now
from app.ehr.events import domain_events
from app.ehr.gateway import FhirGateway
from app.ehr.reference import ADMIT_DX, DAY_SURGERY, OR_BLOCKS, OR_DAY, OR_ROOMS, OVERFLOW, TURNOVER_MIN, UNITS
from app.fhir.dt import parse, ref_id
from app.modules.control_tower import features as F
from app.modules.control_tower import fhirview as V
from app.modules.control_tower.flowmodels import Prediction, models
from app.modules.control_tower.fhirview import unit_of

MODULE = "control_tower"
INPATIENT_UNITS = [u for u in UNITS if u.kind != "ed"]
DISCHARGE_READY = 0.7  # "discharge-ready": P(discharge within 24 h) above this
LWBS_RISK_MINUTES = 120  # CTAS 4-5 waiting this long to be seen: at risk of leaving unseen
PREOP_ITEMS = list(C.PREOP_CHECKS)  # preop-consent, preop-npo, preop-labs, preop-blood-type
BLOCK_MINUTES = (OR_DAY[1] - OR_DAY[0]) * 60


def tower_fhir() -> FhirGateway:
    """FhirGateway for the Control Tower itself: the registry entry's data scope (hospital-wide, the resource types
    it lists), every read audited under the agent's actor with this purpose module."""
    return FhirGateway(agent_actor(registry.require_agent(MODULE), None), MODULE)


# ---------- the boards ----------


class Explained(BaseModel):
    value: float
    factors: list[tuple[str, float | None, float]] = Field(default_factory=list)  # (feature, value, contribution)
    sentence: str  # with values (clinical roles)
    sentence_names: str  # factor names only (non-clinical roles)

    @classmethod
    def of(cls, p: Prediction, model: str, booked: float | None = None) -> Explained:
        return cls(value=round(p.value, 4) if model in ("admission", "discharge") else round(p.value, 1),
                   factors=[(n, None if v != v else v, round(c, 4)) for n, v, c in p.factors],
                   sentence=p.sentence(model, booked=booked), sentence_names=p.sentence(model, values=False,
                                                                                      booked=booked))


class EdRow(BaseModel):
    encounter_id: str
    patient_id: str | None
    mrn: str | None
    location: str | None  # bay, or "ED" while waiting
    status: str  # arrived, triaged, in-progress
    arrival: datetime
    triaged_at: datetime | None
    seen_at: datetime | None
    ctas: int | None
    minutes_in_ed: int
    waiting_to_be_seen: bool
    wait_minutes: int | None  # triage (or arrival) to now while waiting to be seen
    admit: Explained | None  # admission probability from triage
    orders_total: int
    orders_open: int
    bed_request_id: str | None = None
    bed_requested_at: datetime | None
    boarding_minutes: int | None
    target_unit: str | None
    target_unit_source: str | None  # bed request, or likely (from the complaint)
    lwbs_risk: bool
    attending: str | None = None


class BedCell(BaseModel):
    id: str
    room: str
    unit: str
    status: str  # O occupied, U free, K housekeeping, C closed
    encounter_id: str | None = None
    patient_id: str | None = None
    mrn: str | None = None
    since: datetime | None = None
    admitted_at: datetime | None = None
    expected_discharge: datetime | None = None
    days_in: float | None = None
    alc: bool = False
    fall_risk: bool = False
    discharge: Explained | None = None
    attending: str | None = None


class UnitRow(BaseModel):
    id: str
    name: str
    kind: str
    beds: int
    occupied: int
    free: int
    cleaning: int
    closed: int
    occupancy: float  # occupied / beds
    alc: int
    expected_discharges: int  # sum of P(discharge within 24 h), rounded
    discharge_ready: int  # P >= 0.7
    ed_boarders: int  # ED patients with a bed request for this unit
    expected_admissions: int  # boarders + ED admission probabilities + electives in 24 h, rounded
    electives_24h: int
    net_gap: int  # expected admissions - expected discharges - free beds


class OrCase(BaseModel):
    appointment_id: str
    room: str
    procedure_code: str | None
    procedure: str | None
    surgeon_id: str | None
    surgeon: str | None
    patient_id: str | None
    mrn: str | None
    encounter_id: str | None
    status: str  # booked, arrived (in the OR), fulfilled, cancelled
    urgency: str | None
    asa: int | None
    booked_start: datetime
    booked_end: datetime
    booked_minutes: int
    actual_start: datetime | None = None
    actual_end: datetime | None = None
    procedure_id: str | None = None  # the Procedure once the case started
    predicted: Explained | None = None
    predicted_minutes: int | None = None
    predicted_start: datetime | None = None
    predicted_end: datetime | None = None
    overrun_minutes: int | None = None  # predicted minutes - booked minutes
    overrun_risk: str | None = None  # high, med, low or None
    preop: dict[str, str] = Field(default_factory=dict)  # item -> done / open
    preop_tasks: dict[str, str] = Field(default_factory=dict)  # item -> Task id
    preop_open: list[str] = Field(default_factory=list)
    needs_bed: bool = False
    post_op_unit: str | None = None
    cancellation_risk: bool = False
    cancellation_reasons: list[str] = Field(default_factory=list)


class OrRoom(BaseModel):
    id: str
    block: bool  # an elective block today
    surgeon_id: str | None
    booked_minutes: int
    predicted_minutes: int
    utilization: float | None  # predicted minutes / block minutes
    predicted_end: datetime | None


class Kpis(BaseModel):
    ed_census: int
    ed_waiting: int
    ed_boarders: int
    ed_boarders_over_2h: int
    ed_predicted_wait: Explained | None
    lwbs_risk: int
    occupancy: float
    occupied: int
    beds: int
    free: int
    cleaning: int
    expected_discharges: int
    expected_admissions: int
    net_gap: int
    or_cases: int
    or_done: int
    or_in_progress: int
    or_utilization: float | None
    or_booked_utilization: float | None
    or_cancellation_risk: int
    or_predicted_end: datetime | None


class Snapshot(BaseModel):
    now: datetime
    key: str
    built_at: datetime
    build_ms: int
    models: dict[str, bool]
    kpis: Kpis
    ed: list[EdRow]
    units: list[UnitRow]
    beds: dict[str, list[BedCell]]
    or_cases: list[OrCase]
    or_rooms: list[OrRoom]


# ---------- building ----------


def _minutes(a: datetime | None, b: datetime | None) -> int | None:
    return None if a is None or b is None else max(0, int((b - a).total_seconds() // 60))


def _unit_shares(complaint: str | None) -> dict[str, float]:
    """Where an admitted patient with this complaint goes (the admission diagnoses' units and ICU shares)."""
    options = ADMIT_DX.get(complaint or "")
    if not options:
        return {}
    total = sum(w for _, w, *_ in options)
    out: dict[str, float] = {}
    for _dx, w, unit, icu, _surgery in options:
        out[unit] = out.get(unit, 0.0) + w * (1 - icu) / total
        if icu:
            out["ICU"] = out.get("ICU", 0.0) + w * icu / total
    return out


class _Reads:
    """The ten gateway searches of one build."""

    def __init__(self, fhir: FhirGateway, now: datetime) -> None:
        self.locations = fhir.search("Location")
        active = fhir.search("Encounter", status=list(ACTIVE_ENCOUNTER))
        self.ed = [e for e in active if (e.get("class") or {}).get("code") == "EMER"]
        self.stays = [e for e in active if (e.get("class") or {}).get("code") == "IMP"]
        self.active = {e["id"]: e for e in active}
        ed_ids = [e["id"] for e in self.ed]
        self.ctas: dict[str, dict] = {}
        self.vitals: dict[str, dict] = {}
        if ed_ids:
            for obs in fhir.search("Observation", encounter=ed_ids, code=[F.CTAS_CODE, F.VITAL_PANEL], order="date"):
                target = self.ctas if V.code(obs.get("code")) == F.CTAS_CODE else self.vitals
                target.setdefault(V.encounter_id(obs), obs)
        orders: dict[str, dict] = {}
        if self.active:
            for sr in fhir.search("ServiceRequest", encounter=list(self.active), status="active"):
                orders[sr["id"]] = sr
            for sr in fhir.search("ServiceRequest", encounter=list(self.active), date_from=now - timedelta(hours=24)):
                orders[sr["id"]] = sr
        self.orders = list(orders.values())
        self.appointments = [a for a in fhir.search("Appointment", date_from=now - timedelta(hours=24),
                                                    date_to=now + timedelta(hours=30))
                             if (V.participant(a, "Location") or "").startswith("OR-")]
        self.procedures = {ref_id((p.get("basedOn") or [{}])[0]): p
                           for p in fhir.search("Procedure", date_from=now - timedelta(hours=36)) if p.get("basedOn")}
        focus = [f"Appointment/{a['id']}" for a in self.appointments]
        self.tasks = fhir.search("Task", focus=focus) if focus else []
        self.flags = fhir.search("Flag", status="active")
        pids = {V.patient_id(e) for e in active} | {V.patient_id(a) for a in self.appointments}
        pids.discard(None)
        self.patients = {p["id"]: p for p in fhir.search("Patient", ids=sorted(pids))} if pids else {}
        ed_patients = sorted({V.patient_id(e) for e in self.ed} - {None})
        self.prior_stays: dict[str, list[datetime]] = {}
        if ed_patients:
            for stay in fhir.search("Encounter", patient=ed_patients, cls="IMP",
                                    date_from=now - timedelta(days=366)):
                if (s := V.start(stay)) is not None:
                    self.prior_stays.setdefault(V.patient_id(stay), []).append(s)


def build(fhir: FhirGateway, now: datetime, *, key: str = "") -> Snapshot:
    started = time.monotonic()
    with fhir.batch("control-tower-board"):
        r = _Reads(fhir, now)
    flow = models()
    mrn = {pid: V.mrn(p) for pid, p in r.patients.items()}

    # --- orders per encounter ---
    orders_by_enc: dict[str, list[dict]] = {}
    bed_request: dict[str, dict] = {}
    for sr in r.orders:
        enc = V.encounter_id(sr)
        kind = V.category(sr)
        if kind == "bed-request":
            if sr.get("status") == "active":
                bed_request[enc] = sr
        elif kind in ("lab", "imaging", "consult"):
            orders_by_enc.setdefault(enc, []).append(sr)

    # --- ED ---
    ed_rows: list[EdRow] = []
    visits: list[F.EdVisitTimes] = []
    admission_inputs: list[tuple[int, dict]] = []
    for enc in sorted(r.ed, key=lambda e: V.start(e) or now):
        arrival = V.start(enc) or now
        pid = V.patient_id(enc)
        ctas_obs = r.ctas.get(enc["id"])
        times = F.ed_visit_times(enc, ctas_obs, bed_request.get(enc["id"]))
        if times is not None:
            visits.append(times)
        triaged, seen = V.status_since(enc, "triaged"), V.status_since(enc, "in-progress")
        ctas = int(V.value(ctas_obs)) if ctas_obs is not None and V.value(ctas_obs) is not None else None
        waiting = seen is None
        request = bed_request.get(enc["id"])
        requested_at = parse(request.get("authoredOn")) if request else None
        target, source = None, None
        if request is not None:
            target = unit_of(ref_id((request.get("locationReference") or [{}])[0])) if request.get(
                "locationReference") else None
            source = "bed request"
        complaint = F.COMPLAINT_BY_CODE.get(V.code(enc.get("reasonCode")))
        if target is None:
            shares = _unit_shares(complaint)
            if shares:
                target, source = max(shares.items(), key=lambda kv: kv[1])[0], "likely"
        enc_orders = orders_by_enc.get(enc["id"], [])
        wait = _minutes(triaged or arrival, now) if waiting else None
        row = EdRow(
            encounter_id=enc["id"], patient_id=pid, mrn=mrn.get(pid), location=V.current_location(enc),
            status=enc.get("status", "arrived"), arrival=arrival, triaged_at=triaged, seen_at=seen, ctas=ctas,
            minutes_in_ed=_minutes(arrival, now) or 0, waiting_to_be_seen=waiting, wait_minutes=wait, admit=None,
            orders_total=len(enc_orders), orders_open=sum(1 for o in enc_orders if o.get("status") == "active"),
            bed_request_id=request["id"] if request else None, bed_requested_at=requested_at, boarding_minutes=_minutes(requested_at, now), target_unit=target,
            target_unit_source=source,
            lwbs_risk=bool(waiting and ctas is not None and ctas >= 4 and (wait or 0) >= LWBS_RISK_MINUTES),
            attending=V.participant(enc, "Practitioner"))
        ed_rows.append(row)
        if ctas_obs is not None:
            prior = sum(1 for s in r.prior_stays.get(pid, []) if arrival - timedelta(days=365) <= s < arrival)
            admission_inputs.append((len(ed_rows) - 1, F.admission_row(enc, r.patients.get(pid), ctas_obs,
                                                                         r.vitals.get(enc["id"]), prior)))
    preds = flow.predict("admission", [x for _, x in admission_inputs])
    for (i, _), p in zip(admission_inputs, preds or [], strict=False):
        ed_rows[i].admit = Explained.of(p, "admission")
    wait_pred = flow.predict("ed_wait", [F.ed_wait_row(visits, now)])
    ed_wait = Explained.of(wait_pred[0], "ed_wait") if wait_pred else None

    # --- beds ---
    flags: dict[str, set[str]] = {}
    for flag in r.flags:
        if (enc := V.encounter_id(flag)) is not None:
            flags.setdefault(enc, set()).add(V.code(flag.get("code")))
    occupant: dict[str, dict] = {}
    for stay in r.stays + r.ed:
        loc = V.current_location(stay)
        if loc and loc.count("-") == 2:
            occupant[loc] = stay
    orders_times = {enc: [t for o in items if (t := F.order_times(o)) is not None]
                    for enc, items in orders_by_enc.items()}
    discharge_inputs: list[tuple[BedCell, dict]] = []
    beds: dict[str, list[BedCell]] = {u.id: [] for u in UNITS}
    for loc in sorted(r.locations, key=lambda x: x["id"]):
        if V.code(loc.get("physicalType")) != "bd":
            continue
        unit = unit_of(loc["id"])
        cell = BedCell(id=loc["id"], room=ref_id(loc.get("partOf")) or unit, unit=unit,
                       status=(loc.get("operationalStatus") or {}).get("code", "U"))
        stay = occupant.get(loc["id"])
        if stay is not None and unit != "ED":
            pid = V.patient_id(stay)
            admitted = V.start(stay)
            expected = V.ext(stay, "expected-los-days")
            since = next((parse((x.get("period") or {}).get("start")) for x in reversed(stay.get("location") or [])
                          if x.get("status") == "active"), None)
            cell = cell.model_copy(update=dict(
                encounter_id=stay["id"], patient_id=pid, mrn=mrn.get(pid), since=since, admitted_at=admitted,
                expected_discharge=admitted + timedelta(days=float(expected)) if admitted and expected else None,
                days_in=round((now - admitted).total_seconds() / 86400, 1) if admitted else None,
                alc="alc" in flags.get(stay["id"], set()), fall_risk="fall-risk" in flags.get(stay["id"], set()),
                attending=V.participant(stay, "Practitioner")))
            alc_since = now if cell.alc else None
            discharge_inputs.append((cell, F.discharge_row(stay, now, orders_times.get(stay["id"], []), alc_since)))
        elif stay is not None:
            cell = cell.model_copy(update=dict(encounter_id=stay["id"], patient_id=V.patient_id(stay),
                                               mrn=mrn.get(V.patient_id(stay))))
        beds[unit].append(cell)
    preds = flow.predict("discharge", [x for _, x in discharge_inputs])
    for (cell, _), p in zip(discharge_inputs, preds or [], strict=False):
        cell.discharge = Explained.of(p, "discharge")

    # --- OR ---
    tasks_by_appt: dict[str, list[dict]] = {}
    for task in r.tasks:
        if (focus := (task.get("focus") or {}).get("reference", "")).startswith("Appointment/"):
            tasks_by_appt.setdefault(focus.split("/", 1)[1], []).append(task)
    today = now.date()
    cases: list[OrCase] = []
    or_inputs: list[tuple[OrCase, dict]] = []
    electives_24h: dict[str, int] = {}
    admitted_patients = {V.patient_id(s) for s in r.stays}
    for appt in sorted(r.appointments, key=lambda a: parse(a.get("start")) or now):
        start, end_ = parse(appt.get("start")), parse(appt.get("end"))
        status = appt.get("status")
        code = V.code(appt.get("serviceType"))
        pid = V.patient_id(appt)
        # an inpatient bed after surgery, for a patient who is not in one yet
        needs_bed = code not in DAY_SURGERY and code in C.OR_PROCEDURES and pid not in admitted_patients
        post_unit = C.OR_PROCEDURES[code][2] if code in C.OR_PROCEDURES else None
        if status == "booked" and start and now < start <= now + timedelta(hours=24) and needs_bed:
            electives_24h[post_unit] = electives_24h.get(post_unit, 0) + 1
        if start is None or (start.date() != today and status != "arrived"):
            continue
        proc = r.procedures.get(ref_id((appt.get("basedOn") or [{}])[0]) if appt.get("basedOn") else None)
        actual_start = parse((proc.get("performedPeriod") or {}).get("start")) if proc else None
        actual_end = parse((proc.get("performedPeriod") or {}).get("end")) if proc else None
        preop, preop_tasks = {}, {}
        for task in tasks_by_appt.get(appt["id"], []):
            item = V.code(task.get("code"))
            if item in PREOP_ITEMS:
                preop[item] = "done" if task.get("status") == "completed" else "open"
                preop_tasks[item] = task["id"]
        surgeon = V.participant(appt, "Practitioner")
        case = OrCase(
            appointment_id=appt["id"], room=V.participant(appt, "Location") or "", procedure_code=code,
            procedure=C.OR_PROCEDURES.get(code, (appt.get("description"),))[0], surgeon_id=surgeon,
            surgeon=F.surgeon_name(surgeon), patient_id=pid, mrn=mrn.get(pid),
            encounter_id=ref_id(V.ext(appt, "encounter")), status=status, urgency=V.ext(appt, "surgical-urgency"),
            asa=V.ext(appt, "asa-class"), booked_start=start, booked_end=end_ or start,
            booked_minutes=int(appt.get("minutesDuration") or _minutes(start, end_) or 0),
            actual_start=actual_start, actual_end=actual_end, procedure_id=proc["id"] if proc else None,
            preop=preop, preop_tasks=preop_tasks,
            preop_open=[i for i in PREOP_ITEMS if preop.get(i) == "open"] if status == "booked" else [],
            needs_bed=needs_bed, post_op_unit=post_unit)
        cases.append(case)
        if status != "cancelled":
            or_inputs.append((case, F.or_row(appt, r.patients.get(pid))))
    preds = flow.predict("or_duration", [x for _, x in or_inputs])
    for (case, _), p in zip(or_inputs, preds or [], strict=False):
        case.predicted = Explained.of(p, "or_duration", booked=case.booked_minutes)
    _schedule_rooms(cases, now)

    # --- units and aggregates ---
    ed_by_unit: dict[str, int] = {}
    expected_adm: dict[str, float] = {}
    for row in ed_rows:
        if row.bed_requested_at is not None:
            if row.target_unit:
                ed_by_unit[row.target_unit] = ed_by_unit.get(row.target_unit, 0) + 1
                expected_adm[row.target_unit] = expected_adm.get(row.target_unit, 0.0) + 1
        elif row.admit is not None:
            enc = r.active.get(row.encounter_id) or {}
            shares = _unit_shares(F.COMPLAINT_BY_CODE.get(V.code(enc.get("reasonCode"))))
            for unit, share in shares.items():
                expected_adm[unit] = expected_adm.get(unit, 0.0) + row.admit.value * share
    units: list[UnitRow] = []
    for u in INPATIENT_UNITS:
        cells = beds[u.id]
        counts = {s: sum(1 for c in cells if c.status == s) for s in ("O", "U", "K", "C")}
        exp_dis = round(sum(c.discharge.value for c in cells if c.discharge))
        exp_adm = round(expected_adm.get(u.id, 0.0) + electives_24h.get(u.id, 0))
        units.append(UnitRow(
            id=u.id, name=u.name, kind=u.kind, beds=len(cells), occupied=counts["O"], free=counts["U"],
            cleaning=counts["K"], closed=counts["C"], occupancy=round(counts["O"] / len(cells), 4) if cells else 0,
            alc=sum(1 for c in cells if c.alc), expected_discharges=exp_dis,
            discharge_ready=sum(1 for c in cells if c.discharge and c.discharge.value >= DISCHARGE_READY),
            ed_boarders=ed_by_unit.get(u.id, 0), expected_admissions=exp_adm,
            electives_24h=electives_24h.get(u.id, 0), net_gap=exp_adm - exp_dis - counts["U"]))
    _cancellation_risk(cases, units, now)
    rooms = _rooms(cases, now)
    total_beds = sum(u.beds for u in units)
    occupied = sum(u.occupied for u in units)
    block_rooms = [room for room in rooms if room.block]
    kpis = Kpis(
        ed_census=len(ed_rows), ed_waiting=sum(1 for e in ed_rows if e.waiting_to_be_seen),
        ed_boarders=sum(1 for e in ed_rows if e.bed_requested_at is not None),
        ed_boarders_over_2h=sum(1 for e in ed_rows if (e.boarding_minutes or 0) > 120),
        ed_predicted_wait=ed_wait, lwbs_risk=sum(1 for e in ed_rows if e.lwbs_risk),
        occupancy=round(occupied / total_beds, 4) if total_beds else 0, occupied=occupied, beds=total_beds,
        free=sum(u.free for u in units), cleaning=sum(u.cleaning for u in units),
        expected_discharges=sum(u.expected_discharges for u in units),
        expected_admissions=sum(u.expected_admissions for u in units),
        net_gap=sum(u.expected_admissions for u in units) - sum(u.expected_discharges for u in units)
        - sum(u.free for u in units),
        or_cases=sum(1 for c in cases if c.status != "cancelled"),
        or_done=sum(1 for c in cases if c.status == "fulfilled"),
        or_in_progress=sum(1 for c in cases if c.status == "arrived"),
        or_utilization=round(sum(r_.predicted_minutes for r_ in block_rooms) / (BLOCK_MINUTES * len(block_rooms)), 4)
        if block_rooms else None,
        or_booked_utilization=round(sum(r_.booked_minutes for r_ in block_rooms) / (BLOCK_MINUTES * len(block_rooms)),
                                    4) if block_rooms else None,
        or_cancellation_risk=sum(1 for c in cases if c.cancellation_risk),
        or_predicted_end=max((r_.predicted_end for r_ in rooms if r_.predicted_end), default=None))
    return Snapshot(now=now, key=key, built_at=datetime.now(), build_ms=int((time.monotonic() - started) * 1000),
                    models=flow.available(), kpis=kpis, ed=ed_rows, units=units, beds=beds, or_cases=cases,
                    or_rooms=rooms)


def _schedule_rooms(cases: list[OrCase], now: datetime) -> None:
    """Predicted minutes, start and end per case: each room's list in booked order, a case starting when the one
    before it is predicted to end (plus turnover). A running case lasts at least 10 more minutes."""
    turnover = timedelta(minutes=TURNOVER_MIN)
    for room in {c.room for c in cases}:
        free_at: datetime | None = None
        for case in sorted((c for c in cases if c.room == room and c.status != "cancelled"),
                           key=lambda c: c.booked_start):
            model_minutes = case.predicted.value if case.predicted else float(case.booked_minutes)
            if case.status == "fulfilled" and case.actual_start and case.actual_end:
                case.predicted_start, case.predicted_end = case.actual_start, case.actual_end
                minutes = (case.actual_end - case.actual_start).total_seconds() / 60
            elif case.actual_start is not None:  # in the OR now
                elapsed = (now - case.actual_start).total_seconds() / 60
                minutes = max(model_minutes, elapsed + 10)
                case.predicted_start = case.actual_start
                case.predicted_end = case.actual_start + timedelta(minutes=minutes)
            else:
                start = max(case.booked_start, (free_at + turnover) if free_at else case.booked_start)
                start = max(start, now) if start < now else start
                minutes = model_minutes
                case.predicted_start, case.predicted_end = start, start + timedelta(minutes=minutes)
            case.predicted_minutes = round(minutes)
            if case.status != "fulfilled":
                case.overrun_minutes = case.predicted_minutes - case.booked_minutes
                over = case.overrun_minutes
                case.overrun_risk = "high" if over >= 60 else "med" if over >= 30 else "low" if over >= 15 else None
            free_at = case.predicted_end


def _cancellation_risk(cases: list[OrCase], units: list[UnitRow], now: datetime) -> None:
    free = {u.id: u.free for u in units}
    for case in cases:
        if case.status != "booked":
            continue
        reasons = []
        if case.preop_open and case.booked_start - now < timedelta(hours=4):
            reasons.append("pre-op checklist incomplete")
        if case.needs_bed and case.post_op_unit and \
                sum(free.get(u, 0) for u in [case.post_op_unit, *OVERFLOW.get(case.post_op_unit, [])]) == 0:
            reasons.append(f"no free bed on {case.post_op_unit} or its overflow units")
        case.cancellation_reasons = reasons
        case.cancellation_risk = bool(reasons)


def _rooms(cases: list[OrCase], now: datetime) -> list[OrRoom]:
    blocks = OR_BLOCKS.get(now.weekday(), {})
    out = []
    for room in OR_ROOMS:
        own = [c for c in cases if c.room == room and c.status != "cancelled"]
        out.append(OrRoom(
            id=room, block=room in blocks, surgeon_id=blocks.get(room),
            booked_minutes=sum(c.booked_minutes for c in own if c.urgency != "emergent"),
            predicted_minutes=sum(c.predicted_minutes or c.booked_minutes for c in own if c.urgency != "emergent"),
            utilization=round(sum(c.predicted_minutes or c.booked_minutes for c in own if c.urgency != "emergent")
                              / BLOCK_MINUTES, 4) if room in blocks else None,
            predicted_end=max((c.predicted_end for c in own if c.predicted_end), default=None)))
    return out


# ---------- cache ----------


def cache_key(store: Store) -> str:
    """Changes whenever the boards could have changed: any commit, the hospital clock, a new domain event."""
    last_event = store.conn().execute(select(func.max(domain_events.c.seq))).scalar() or 0
    return f"{store.version}:{hospital_now(store).isoformat()}:{last_event}"


class _Cache:
    """The last snapshot. One build at a time: requests that arrive while it runs wait and share it (the API
    runs one process, so parallel builds would only compete for the interpreter)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._build = threading.Lock()
        self._value: Snapshot | None = None

    def get(self, store: Store) -> Snapshot:
        key = cache_key(store)
        with self._lock:
            if self._value is not None and self._value.key == key:
                return self._value
        with self._build:
            with self._lock:
                if self._value is not None and self._value.key == key:
                    return self._value
            snap = build(tower_fhir(), hospital_now(store), key=key)
            with self._lock:
                self._value = snap
        return snap

    def clear(self) -> None:
        with self._lock:
            self._value = None


cache = _Cache()
