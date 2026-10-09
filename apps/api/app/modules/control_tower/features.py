"""Features of the four flow models (spec 7.1), shared by training and live scoring.

The same functions turn FHIR resources into a feature row whether they run over
60 days of history (`flowdata.py`, the training set) or over the hospital as it
is now (`snapshot.py`), so the models never see features computed two ways.

| Model | Unit of prediction | Features | Label |
| --- | --- | --- | --- |
| admission | an ED visit at triage | age, CTAS, complaint, arrival mode, first vital signs, admissions in the last 12 months | admitted (ED disposition) |
| discharge | an inpatient at a moment | admission reason, days in hospital, expected LOS and their ratio, open orders, pending results, orders in the last 24 h, ALC flag | discharged within 24 h |
| or_duration | an OR case | procedure, surgeon, ASA class, age, elective / emergent, booked start hour | minutes in the OR |
| ed_wait | the ED at a moment | patients in the ED, waiting to be seen, boarders, CTAS mix, hour, weekday, physicians on duty | mean wait (triage -> physician) of the patients triaged in the next hour |

Categorical features are integer codes over fixed vocabularies (unknown: NaN,
which the gradient-boosted trees treat as missing).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.ehr import codes as C
from app.ehr.reference import EXPECTED_LOS, SURGEON_BY_ID, SURGEONS, ed_physicians_on_duty
from app.fhir.dt import parse
from app.modules.control_tower import fhirview as V

NAN = float("nan")

COMPLAINTS = list(C.COMPLAINTS)
COMPLAINT_BY_CODE = {code: name for name, (code, _w, _o) in C.COMPLAINTS.items()}
ARRIVAL_MODES = ["ambulance", "walk-in", "police", "transfer"]
PROCEDURES = list(C.OR_PROCEDURES)
SURGEON_IDS = [s.id for s in SURGEONS]
ADMIT_REASONS = sorted(set(EXPECTED_LOS) | set(C.OR_PROCEDURES))
VITAL_CODES = {"heart_rate": "8867-4", "systolic_bp": "8480-6", "resp_rate": "9279-1", "temperature": "8310-5",
               "spo2": "59408-5", "pain": "72514-3", "gcs": "9269-2"}
CTAS_CODE = C.CTAS[0]
VITAL_PANEL = C.VITAL_PANEL[0]


def _index(vocab: list[str], value: str | None) -> float:
    return float(vocab.index(value)) if value in vocab else NAN


def _num(value) -> float:
    return NAN if value is None else float(value)


def _int(v: float) -> str:
    return str(int(round(v)))


@dataclass(frozen=True)
class Feature:
    name: str
    label: str  # the factor's name in an explanation ("CTAS", "age")
    fmt: Callable[[float], str]  # the factor with its value ("CTAS 2", "age 72")
    vocab: tuple[str, ...] = ()  # categorical: the codes' names
    clinical: bool = False  # the value is clinical content (hidden from non-clinical roles)

    def describe(self, value: float, *, values: bool = True) -> str:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return f"{self.label} unknown" if values else self.label
        return self.fmt(value) if values else self.label


def _cat(vocab: list[str], names: dict[str, str] | None = None) -> Callable[[float], str]:
    return lambda v: (names or {}).get(vocab[int(v)], vocab[int(v)]) if 0 <= int(v) < len(vocab) else "other"


SURGEON_NAMES = {s.id: s.name for s in SURGEONS}
REASON_NAMES = {c: (C.CONDITIONS.get(c, (None,))[0] or C.OR_PROCEDURES.get(c, (c,))[0]) for c in ADMIT_REASONS}

ADMISSION = [
    Feature("age", "age", lambda v: f"age {_int(v)}"),
    Feature("ctas", "CTAS", lambda v: f"CTAS {_int(v)}", clinical=True),
    Feature("complaint", "complaint", lambda v: _cat(COMPLAINTS)(v), tuple(COMPLAINTS), clinical=True),
    Feature("arrival_mode", "arrival mode", lambda v: f"arrived by {_cat(ARRIVAL_MODES)(v)}", tuple(ARRIVAL_MODES)),
    Feature("heart_rate", "heart rate", lambda v: f"heart rate {_int(v)}", clinical=True),
    Feature("systolic_bp", "blood pressure", lambda v: f"systolic BP {_int(v)}", clinical=True),
    Feature("resp_rate", "respiratory rate", lambda v: f"respiratory rate {_int(v)}", clinical=True),
    Feature("temperature", "temperature", lambda v: f"temperature {v:.1f}°C", clinical=True),
    Feature("spo2", "oxygen saturation", lambda v: f"SpO₂ {_int(v)}%", clinical=True),
    Feature("pain", "pain", lambda v: f"pain {_int(v)}/10", clinical=True),
    Feature("gcs", "GCS", lambda v: f"GCS {_int(v)}", clinical=True),
    Feature("admissions_12m", "recent admissions",
            lambda v: f"{_int(v)} admission{'s' if round(v) != 1 else ''} in 12 months"),
]
DISCHARGE = [
    Feature("reason", "admission reason", lambda v: _cat(ADMIT_REASONS, REASON_NAMES)(v), tuple(ADMIT_REASONS),
            clinical=True),
    Feature("days_in", "days in hospital", lambda v: f"day {v:.1f} in hospital"),
    Feature("expected_los", "expected stay", lambda v: f"{v:g} days expected"),
    Feature("los_ratio", "stay vs expected", lambda v: f"{round(100 * v)}% of the expected stay"),
    Feature("open_orders", "open orders", lambda v: f"{_int(v)} open order{'s' if round(v) != 1 else ''}"),
    Feature("pending_results", "pending results",
            lambda v: f"{_int(v)} pending result{'s' if round(v) != 1 else ''}"),
    Feature("orders_24h", "new orders", lambda v: f"{_int(v)} new order{'s' if round(v) != 1 else ''} in 24 h"),
    Feature("alc", "ALC", lambda v: "ALC" if v >= 0.5 else "not ALC"),
]
OR_DURATION = [
    Feature("procedure", "procedure", lambda v: _cat(PROCEDURES, {c: d[0] for c, d in C.OR_PROCEDURES.items()})(v),
            tuple(PROCEDURES)),
    Feature("surgeon", "surgeon", lambda v: _cat(SURGEON_IDS, SURGEON_NAMES)(v), tuple(SURGEON_IDS)),
    Feature("asa", "ASA class", lambda v: f"ASA {_int(v)}", clinical=True),
    Feature("age", "age", lambda v: f"age {_int(v)}"),
    Feature("emergent", "urgency", lambda v: "emergent" if v >= 0.5 else "elective"),
    Feature("start_hour", "start time", lambda v: f"booked for {int(v):02d}:00"),
]
ED_WAIT = [
    Feature("census", "patients in the ED", lambda v: f"{_int(v)} in the ED"),
    Feature("waiting", "waiting to be seen", lambda v: f"{_int(v)} waiting to be seen"),
    Feature("boarders", "boarders", lambda v: f"{_int(v)} boarding"),
    Feature("ctas_1_2", "CTAS 1-2 patients", lambda v: f"{_int(v)} at CTAS 1-2"),
    Feature("ctas_3", "CTAS 3 patients", lambda v: f"{_int(v)} at CTAS 3"),
    Feature("ctas_4_5", "CTAS 4-5 patients", lambda v: f"{_int(v)} at CTAS 4-5"),
    Feature("hour", "time of day", lambda v: f"{int(v):02d}:00"),
    Feature("weekday", "weekday", lambda v: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][int(v) % 7]),
    Feature("physicians", "physicians on duty", lambda v: f"{_int(v)} physician{'s' if round(v) != 1 else ''} on duty"),
]
FEATURES: dict[str, list[Feature]] = {"admission": ADMISSION, "discharge": DISCHARGE, "or_duration": OR_DURATION,
                                      "ed_wait": ED_WAIT}


# ---------- admission (an ED visit at triage) ----------


def admission_row(encounter: dict, patient: dict | None, ctas: dict | None, vitals_panel: dict | None,
                  prior_admissions: int) -> dict[str, float]:
    arrival = V.start(encounter)
    vit = V.vitals(vitals_panel)
    complaint = COMPLAINT_BY_CODE.get(V.code(encounter.get("reasonCode")))
    row = {"age": _num(V.age(patient, arrival)) if arrival else NAN, "ctas": _num(V.value(ctas)),
           "complaint": _index(COMPLAINTS, complaint), "arrival_mode": _index(ARRIVAL_MODES, V.ext(encounter, "arrival-mode")),
           "admissions_12m": float(prior_admissions)}
    for name, loinc in VITAL_CODES.items():
        row[name] = _num(vit.get(loinc))
    return row


def admitted(encounter: dict) -> bool | None:
    """The label: None while the visit is still open."""
    outcome = V.ext(encounter, "ed-disposition")
    return None if outcome is None else outcome == "admitted"


# ---------- discharge readiness (an inpatient at time t) ----------


@dataclass
class OrderTimes:
    """One order: when it was placed and when it was completed (None: still open)."""
    authored: datetime
    completed: datetime | None
    kind: str  # lab, imaging, consult


def order_times(service_request: dict) -> OrderTimes | None:
    kind = V.category(service_request)
    if kind not in ("lab", "imaging", "consult"):
        return None  # bed and surgery requests are not ward orders
    authored = parse(service_request.get("authoredOn"))
    if authored is None or service_request.get("status") in ("revoked", "entered-in-error"):
        return None
    completed = parse(service_request.get("occurrenceDateTime")) if service_request.get("status") == "completed" \
        else None
    return OrderTimes(authored, completed, kind)


def discharge_row(stay: dict, at: datetime, orders: list[OrderTimes], alc_since: datetime | None) -> dict[str, float]:
    admit = V.start(stay)
    days_in = max(0.0, (at - admit).total_seconds() / 86400) if admit else NAN
    expected = _num(V.ext(stay, "expected-los-days"))
    reason = V.code(stay.get("reasonCode"))
    open_orders = [o for o in orders if o.authored <= at and (o.completed is None or o.completed > at)]
    return {"reason": _index(ADMIT_REASONS, reason), "days_in": days_in, "expected_los": expected,
            "los_ratio": days_in / expected if expected and not math.isnan(expected) and expected > 0 else NAN,
            "open_orders": float(len(open_orders)),
            "pending_results": float(sum(o.kind in ("lab", "imaging") for o in open_orders)),
            "orders_24h": float(sum(at - timedelta(hours=24) < o.authored <= at for o in orders)),
            "alc": 1.0 if alc_since is not None and alc_since <= at else 0.0}


def discharged_within(stay: dict, at: datetime, hours: int = 24) -> bool | None:
    """The label: discharged within `hours` after `at` (None when the outcome is not known yet)."""
    left = V.end(stay)
    if left is not None:
        return left <= at + timedelta(hours=hours)
    return None


# ---------- surgical duration (an OR case) ----------


def or_row(appointment: dict, patient: dict | None) -> dict[str, float]:
    booked = parse(appointment.get("start"))
    return {"procedure": _index(PROCEDURES, V.code(appointment.get("serviceType"))),
            "surgeon": _index(SURGEON_IDS, V.participant(appointment, "Practitioner")),
            "asa": _num(V.ext(appointment, "asa-class")),
            "age": _num(V.age(patient, booked)) if booked else NAN,
            "emergent": 1.0 if V.ext(appointment, "surgical-urgency") == "emergent" else 0.0,
            "start_hour": float(booked.hour) if booked else NAN}


def booked_baseline_minutes(appointment: dict) -> float | None:
    minutes = appointment.get("minutesDuration")
    return float(minutes) if minutes else None


def surgeon_name(practitioner_id: str | None) -> str | None:
    s = SURGEON_BY_ID.get(practitioner_id or "")
    return s.name if s else None


# ---------- ED wait (the department at time t) ----------


@dataclass
class EdVisitTimes:
    """One ED visit's milestones (from the Encounter, its CTAS Observation and its bed request)."""
    encounter_id: str
    arrival: datetime
    end: datetime | None  # None: still in the ED
    triaged: datetime | None
    seen: datetime | None  # None: not seen (yet, or left without being seen)
    ctas: int | None
    bed_requested: datetime | None
    left_unseen: bool = False
    extra: dict = field(default_factory=dict)

    def in_ed(self, t: datetime) -> bool:
        return self.arrival <= t and (self.end is None or self.end > t)

    def wait_minutes(self) -> float | None:
        if self.triaged is None or self.seen is None:
            return None
        return (self.seen - self.triaged).total_seconds() / 60


def ed_visit_times(encounter: dict, ctas: dict | None, bed_request: dict | None) -> EdVisitTimes | None:
    arrival = V.start(encounter)
    if arrival is None:
        return None
    triaged = V.status_since(encounter, "triaged")
    seen = V.status_since(encounter, "in-progress")
    value = V.value(ctas)
    return EdVisitTimes(encounter["id"], arrival, V.end(encounter) if encounter.get("status") == "finished" else None,
                        triaged, seen, int(value) if value is not None else None,
                        parse(bed_request.get("authoredOn")) if bed_request else None,
                        left_unseen=V.ext(encounter, "ed-disposition") == "lwbs")


def ed_wait_row(visits: list[EdVisitTimes], t: datetime) -> dict[str, float]:
    inside = [v for v in visits if v.in_ed(t)]
    triaged = [v for v in inside if v.triaged is not None and v.triaged <= t and v.ctas is not None]
    return {"census": float(len(inside)),
            "waiting": float(sum(1 for v in inside if v.seen is None or v.seen > t)),
            "boarders": float(sum(1 for v in inside if v.bed_requested is not None and v.bed_requested <= t)),
            "ctas_1_2": float(sum(1 for v in triaged if v.ctas <= 2)),
            "ctas_3": float(sum(1 for v in triaged if v.ctas == 3)),
            "ctas_4_5": float(sum(1 for v in triaged if v.ctas >= 4)),
            "hour": float(t.hour), "weekday": float(t.weekday()), "physicians": float(ed_physicians_on_duty(t))}


def ed_wait_label(visits: list[EdVisitTimes], t: datetime, minutes: int = 60) -> float | None:
    """Mean wait of the patients triaged in [t, t + minutes) who were seen (None when nobody was)."""
    waits = [v.wait_minutes() for v in visits
             if v.triaged is not None and t <= v.triaged < t + timedelta(minutes=minutes) and not v.left_unseen]
    waits = [w for w in waits if w is not None]
    return sum(waits) / len(waits) if waits else None


def ed_wait_baseline(visits: list[EdVisitTimes], t: datetime, hours: int = 4) -> float | None:
    """The spec's baseline: mean wait of the patients seen in the last `hours` hours (known at t)."""
    waits = [v.wait_minutes() for v in visits
             if v.seen is not None and t - timedelta(hours=hours) < v.seen <= t and v.triaged is not None]
    return sum(waits) / len(waits) if waits else None
