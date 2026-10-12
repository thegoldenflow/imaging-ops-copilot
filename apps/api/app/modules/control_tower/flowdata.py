"""Training data for the flow models, read from FHIR (spec 7.1: `scripts/build_flow_dataset.py`).

Everything comes through FhirGateway as the Control Tower (registry entry
`control_tower`, hospital-wide data scope), one audited search per resource type.
The history is the generated hospital's 60 days up to the hospital clock (and
whatever the day simulator has added since); each row carries the time it was
predicted at, so training can split by date.

Rows hold features, the label, the spec's baseline where it needs history (the
ED wait's rolling 4-hour mean) and an id; no names, MRNs or free text.
"""

from __future__ import annotations

import bisect
import csv
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from app.ehr import codes as C
from app.ehr.gateway import FhirGateway
from app.fhir.dt import parse, ref_id
from app.modules.control_tower import features as F
from app.modules.control_tower import fhirview as V

ORIGIN_STEP = timedelta(minutes=30)  # ED wait: a prediction every 30 minutes
DISCHARGE_HOURS = (7, 19)  # discharge readiness: predicted at 07:00 and 19:00 for every inpatient


@dataclass
class Table:
    name: str
    columns: list[str]
    rows: list[dict] = field(default_factory=list)  # id, time, features..., label (+ baseline)

    def add(self, rid: str, at: datetime, features: dict[str, float], label: float, **extra) -> None:
        self.rows.append({"id": rid, "time": at, **features, "label": label, **extra})

    def write(self, folder: Path) -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{self.name}.csv"
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, ["id", "time", *self.columns, "label", *self.extra_columns])
            writer.writeheader()
            for row in self.rows:
                writer.writerow({k: (v.isoformat() if isinstance(v, datetime) else
                                     "" if isinstance(v, float) and math.isnan(v) else v) for k, v in row.items()})
        return path

    @property
    def extra_columns(self) -> list[str]:
        base = {"id", "time", "label", *self.columns}
        return sorted({k for row in self.rows[:1] for k in row if k not in base})


def read_table(path: Path) -> Table:
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        name = path.stem
        columns = [c.name for c in F.FEATURES[name]]
        table = Table(name, columns)
        for raw in reader:
            row: dict = {"id": raw["id"], "time": datetime.fromisoformat(raw["time"])}
            for key, value in raw.items():
                if key in ("id", "time"):
                    continue
                row[key] = float(value) if value not in ("", None) else float("nan")
            table.rows.append(row)
    return table


class HospitalHistory:
    """The FHIR resources the datasets need, indexed (one gateway search per type)."""

    def __init__(self, fhir: FhirGateway, as_of: datetime) -> None:
        self.as_of = as_of
        encounters = fhir.search("Encounter")
        self.encounters = {e["id"]: e for e in encounters}
        self.ed = [e for e in encounters if (e.get("class") or {}).get("code") == "EMER"]
        self.stays = [e for e in encounters if (e.get("class") or {}).get("code") == "IMP"]
        self.patients = {p["id"]: p for p in fhir.search("Patient")}
        self.ctas: dict[str, dict] = {}
        self.first_vitals: dict[str, dict] = {}
        for obs in fhir.search("Observation", encounter=[e["id"] for e in self.ed], code=[F.CTAS_CODE, F.VITAL_PANEL],
                               order="date"):
            enc = V.encounter_id(obs)
            if V.code(obs.get("code")) == F.CTAS_CODE:
                self.ctas.setdefault(enc, obs)
            else:
                self.first_vitals.setdefault(enc, obs)
        self.orders: dict[str, list[F.OrderTimes]] = defaultdict(list)
        self.bed_requests: dict[str, dict] = {}
        self.surgery_requests: dict[str, dict] = {}
        for sr in fhir.search("ServiceRequest"):
            kind = V.category(sr)
            if kind == "bed-request":
                self.bed_requests[V.encounter_id(sr)] = sr
            elif kind == "surgery":
                self.surgery_requests[sr["id"]] = sr
            elif (times := F.order_times(sr)) is not None:
                self.orders[V.encounter_id(sr)].append(times)
        self.alc: dict[str, datetime] = {}
        for flag in fhir.search("Flag", code="alc"):
            enc = V.encounter_id(flag)
            if enc and (since := V.start(flag)):
                self.alc[enc] = min(since, self.alc.get(enc, since))
        appointments = [a for a in fhir.search("Appointment")
                        if (V.participant(a, "Location") or "").startswith("OR-")]
        self.appointment_by_request = {ref_id((a.get("basedOn") or [{}])[0]): a for a in appointments
                                       if a.get("basedOn")}
        self.procedures = [p for p in fhir.search("Procedure", status="completed")
                           if V.code(p.get("code")) in C.OR_PROCEDURES]
        self.admissions_by_patient: dict[str, list[datetime]] = defaultdict(list)
        for stay in self.stays:
            if (pid := V.patient_id(stay)) and (s := V.start(stay)):
                self.admissions_by_patient[pid].append(s)
        for starts in self.admissions_by_patient.values():
            starts.sort()

    def prior_admissions(self, patient_id: str | None, at: datetime) -> int:
        starts = self.admissions_by_patient.get(patient_id or "", [])
        return bisect.bisect_left(starts, at) - bisect.bisect_left(starts, at - timedelta(days=365))

    def window_start(self) -> datetime:
        firsts = [s for e in self.ed if (s := V.start(e))]
        return min(firsts) if firsts else self.as_of - timedelta(days=60)


def admission_table(h: HospitalHistory) -> Table:
    table = Table("admission", [f.name for f in F.ADMISSION])
    for enc in h.ed:
        label = F.admitted(enc)
        ctas = h.ctas.get(enc["id"])
        if label is None or ctas is None:
            continue
        arrival = V.start(enc)
        pid = V.patient_id(enc)
        row = F.admission_row(enc, h.patients.get(pid), ctas, h.first_vitals.get(enc["id"]),
                              h.prior_admissions(pid, arrival))
        table.add(enc["id"], arrival, row, float(label))
    return table


def discharge_table(h: HospitalHistory) -> Table:
    table = Table("discharge", [f.name for f in F.DISCHARGE])
    start = h.window_start().replace(minute=0, second=0, microsecond=0)
    day = start.replace(hour=0)
    while day <= h.as_of:
        for hour in DISCHARGE_HOURS:
            t = day.replace(hour=hour)
            if t < start or t > h.as_of:
                continue
            for stay in h.stays:
                admit, left = V.start(stay), V.end(stay)
                if admit is None or admit > t or (left is not None and left <= t):
                    continue
                if left is None and t + timedelta(hours=24) > h.as_of:
                    continue  # outcome not known yet
                label = left is not None and left <= t + timedelta(hours=24)
                row = F.discharge_row(stay, t, h.orders.get(stay["id"], []), h.alc.get(stay["id"]))
                table.add(f"{stay['id']}@{t:%Y%m%d%H}", t, row, float(label))
        day += timedelta(days=1)
    return table


def or_table(h: HospitalHistory) -> Table:
    table = Table("or_duration", [f.name for f in F.OR_DURATION])
    for proc in h.procedures:
        started, ended = parse((proc.get("performedPeriod") or {}).get("start")), \
            parse((proc.get("performedPeriod") or {}).get("end"))
        request = ref_id((proc.get("basedOn") or [{}])[0]) if proc.get("basedOn") else None
        appt = h.appointment_by_request.get(request)
        if started is None or ended is None or appt is None:
            continue
        row = F.or_row(appt, h.patients.get(V.patient_id(appt)))
        table.add(appt["id"], started, row, (ended - started).total_seconds() / 60,
                  booked_minutes=float(appt.get("minutesDuration") or float("nan")))
    return table


def ed_wait_table(h: HospitalHistory) -> Table:
    table = Table("ed_wait", [f.name for f in F.ED_WAIT])
    visits = [v for enc in h.ed
              if (v := F.ed_visit_times(enc, h.ctas.get(enc["id"]), h.bed_requests.get(enc["id"]))) is not None]
    visits.sort(key=lambda v: v.arrival)
    arrivals = [v.arrival for v in visits]
    t = (h.window_start() + timedelta(hours=6)).replace(minute=0, second=0, microsecond=0)
    while t + timedelta(hours=1) <= h.as_of:
        lo = bisect.bisect_left(arrivals, t - timedelta(hours=48))
        hi = bisect.bisect_right(arrivals, t + timedelta(hours=1))
        near = visits[lo:hi]
        label = F.ed_wait_label(near, t)
        baseline = F.ed_wait_baseline(near, t)
        if label is not None and baseline is not None:
            table.add(f"ed@{t:%Y%m%d%H%M}", t, F.ed_wait_row(near, t), label, baseline=baseline)
        t += ORIGIN_STEP
    return table


def build(fhir: FhirGateway, as_of: datetime) -> dict[str, Table]:
    h = HospitalHistory(fhir, as_of)
    return {"admission": admission_table(h), "discharge": discharge_table(h), "or_duration": or_table(h),
            "ed_wait": ed_wait_table(h)}
