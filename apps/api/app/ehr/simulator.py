"""DaySimulator (spec 6.2): plays the hospital's EHR through the planned day.

The generator (app/ehr/seed) wrote the hospital as of 07:00 into the FHIR store
and the next 36 hours as a plan (`hospital_plan`: ED visits, admissions,
transfers, discharges, OR cases, orders and results, housekeeping). The
simulator moves the hospital clock (`hospital_clock`) forward, either on request
(`advance`, `fast_forward` to 08:00 tomorrow) or continuously at a demo rate
(`run`; rate 60 = one hospital hour per wall minute, ticked by the API's
background loop). For every planned event that comes due it

1. writes the change into the FHIR store as the EHR would (Encounter status and
   location, bed status, new Observations, orders, results, OR appointments),
   reusing the generator's resource builders so the data looks the same; and
2. sends the HL7 v2 message an EHR would send (ADT, ORM, ORU, SIU). The HL7
   adapter (app/ehr/hl7.py) turns each into a domain event, published on the
   EventBus (app/ehr/events.py) in the same transaction as the FHIR writes.

The plan is the intent, the FHIR store is the truth. Injected scenarios
(app/ehr/scenarios.py) and bed managers (FhirGateway.append_encounter_location)
change the hospital in ways the plan did not foresee, so before using a bed the
simulator checks it: a taken bed means another free bed in the unit or its
overflow units, and if there is none the patient keeps boarding in the ED and
the admission (with the rest of that stay) is retried 30 minutes later. Beds
left for housekeeping without a planned cleaning get one. Ward vital signs are
recorded every six hours, as the generator does.

In a real deployment this module disappears: the interface engine's HL7 feed
goes into the same adapter, and the EventBus and its subscribers do not change.
The simulator writes the local FHIR store (FHIR_BACKEND=local); it does not
drive a HAPI server.
"""

from __future__ import annotations

import bisect
import dataclasses
import hashlib
import logging
import random
import types
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import select

from app.core.store import Store
from app.ehr import codes as C
from app.ehr.events import DomainEvent, bus, platform_event
from app.ehr.fhirstore import fhir_resources, unit_of
from app.ehr.hl7 import CLASS_CODE, SERVICE_SECTION, Hl7EventAdapter, Hl7Message
from app.ehr.reference import INPATIENT_MEDS, OVERFLOW, POSTOP_MEDS
from app.ehr.seed import materialize as M
from app.ehr.seed import plan_order
from app.ehr.seed.builder import Builder, ext
from app.ehr.seed.materialize import OrderSpec
from app.ehr.seed.people import PatientInfo, bed_ids
from app.ehr.seed.simulate import EdVisit, Stay, Surgery
from app.fhir.dt import fhir_datetime, parse, ref, ref_id

log = logging.getLogger("app.simulator")

SIM_LOCK = 0x51_D4_7A  # pg advisory lock: one simulator step at a time
DEFAULT_RATE = 60.0  # hospital seconds per wall second: one hospital hour per demo minute
MAX_TICK = timedelta(minutes=30)  # a background tick never jumps further than this
RETRY = timedelta(minutes=30)  # a patient without a free bed tries again after this
CLEAN_MINUTES = (45, 100)  # housekeeping after a discharge or transfer (as the generator)
VITALS_HOURS = 6  # ward vital-sign rounds at 00, 06, 12 and 18
BED_DISPLAY = {"O": "Occupied", "U": "Unoccupied", "K": "Contaminated", "C": "Closed"}
ACTIVE = ("arrived", "triaged", "in-progress", "onleave")
SIM_ACTOR = "system:day-simulator"


class SimulatorBusy(RuntimeError):
    """Another simulator step holds the lock (e.g. the background tick)."""


# ---------- clock ----------


def clock(store: Store) -> dict:
    return dict(store.modules.get("hospital_clock") or {})


def _save_clock(store: Store, value: dict) -> None:
    store.modules["hospital_clock"] = value


def _plan(store: Store) -> dict:
    plan = store.modules.get("hospital_plan")
    if not plan:
        raise LookupError("No hospital plan: generate the demo data first")
    return plan


def status(store: Store, upcoming: int = 8) -> dict:
    """The clock and what comes next, for the control bar."""
    c, plan = clock(store), _plan(store)
    now = parse(c["now"])
    events = plan["events"]
    i = _first_after(events, now)
    return {
        "now": now, "rate": c.get("rate") or 0, "running": bool(c.get("running")),
        "day_start": parse(plan["generated_for"]), "horizon": parse(plan["horizon"]),
        "applied": c.get("applied", 0), "remaining": len(events) - i, "stopped": c.get("stopped"),
        "upcoming": [{"at": parse(e["at"]), "kind": e["kind"],
                      "ref": next((e[k] for k in ("visit", "stay", "surgery", "order", "bed", "patient") if k in e), None)}
                     for e in events[i:i + upcoming]],
    }


def _first_after(events: list[dict], t: datetime) -> int:
    """Index of the first plan event strictly after t."""
    return bisect.bisect_right(events, (t.isoformat(), 99), key=plan_order)


def next_morning(now: datetime, hour: int = 8) -> datetime:
    """"Fast-forward to 8 tomorrow morning": `hour`:00 on the next day, or the coming one in the small hours."""
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    return target if now.hour < 5 and target > now else target + timedelta(days=1)


def _locked(store: Store) -> None:
    if not store.try_lock(SIM_LOCK):
        raise SimulatorBusy("The simulator is busy; try again in a moment")


def advance(store: Store, until: datetime, *, last_wall: datetime | None = None) -> AdvanceResult:
    """Apply every planned event up to `until` (capped at the plan horizon) and move the clock there."""
    _locked(store)  # before reading the clock, so two steps never apply the same events
    return _advance(store, until, last_wall=last_wall)


def _advance(store: Store, until: datetime, *, last_wall: datetime | None = None) -> AdvanceResult:
    c, plan = clock(store), _plan(store)
    now, horizon = parse(c["now"]), parse(plan["horizon"])
    until = min(until, horizon)
    run = _Run(store, plan, now, max(until, now))
    if until > now:
        run.execute()
    c["now"] = run.until.isoformat()
    c["applied"] = c.get("applied", 0) + sum(run.applied.values())
    if last_wall is not None:
        c["last_wall"] = last_wall.isoformat()
    if run.until >= horizon:
        c["running"], c["stopped"] = False, "end of the planned day: reset the demo data to replay it"
    _save_clock(store, c)
    if run.plan_changed:
        store.modules["hospital_plan"] = plan
    return run.result()


def advance_by(store: Store, minutes: float) -> AdvanceResult:
    _locked(store)
    now = parse(clock(store)["now"])
    return _advance(store, now + timedelta(minutes=minutes))


def fast_forward(store: Store, hour: int = 8) -> AdvanceResult:
    _locked(store)
    return _advance(store, next_morning(parse(clock(store)["now"]), hour))


def run(store: Store, rate: float = DEFAULT_RATE, *, wall_now: datetime | None = None) -> dict:
    _locked(store)
    if not 0 < rate <= 3600:
        raise ValueError("rate must be between 0 and 3600 hospital seconds per second")
    c = clock(store)
    if parse(c["now"]) >= parse(_plan(store)["horizon"]):
        raise ValueError("The planned day is over; reset the demo data to replay it")
    c.update(running=True, rate=rate, last_wall=(wall_now or datetime.now()).isoformat(), stopped=None)
    _save_clock(store, c)
    return c


def pause(store: Store) -> dict:
    _locked(store)
    c = clock(store)
    c.update(running=False, last_wall=None)
    _save_clock(store, c)
    return c


def tick(store: Store, wall_now: datetime | None = None) -> AdvanceResult | None:
    """Background step: when the clock runs, move it by the wall time elapsed × rate."""
    if not store.try_lock(SIM_LOCK):
        return None
    c = clock(store)
    if not c.get("running"):
        return None
    wall_now = wall_now or datetime.now()
    last = parse(c.get("last_wall")) or wall_now
    step = timedelta(seconds=max(0.0, (wall_now - last).total_seconds()) * float(c.get("rate") or 0))
    return _advance(store, parse(c["now"]) + min(step, MAX_TICK), last_wall=wall_now)


@dataclass
class AdvanceResult:
    start: datetime
    end: datetime
    applied: dict[str, int]  # plan events applied, by kind
    events: dict[str, int]  # domain events published, by type
    deferred: int  # admissions or transfers postponed for want of a free bed
    vitals_panels: int
    published: list[DomainEvent] = field(default_factory=list, repr=False)
    written: set[tuple[str, str]] = field(default_factory=set, repr=False)  # resources created or changed

    def summary(self) -> dict:
        return {"start": self.start, "end": self.end, "applied": self.applied, "events": self.events,
                "deferred": self.deferred, "vitals_panels": self.vitals_panels}


# ---------- the run: applying one window of the plan ----------

_DATETIME_FIELDS = {"arrival", "triage", "seen", "decision", "end", "bed_request_at", "admit", "discharge", "alc_from",
                    "booked_start", "start", "booked_at", "authored", "completed"}


def _load(cls, d: dict):
    """A plan entry as the generator's dataclass (extra keys such as `scenario` are ignored)."""
    names = {f.name for f in dataclasses.fields(cls)}
    kwargs = {}
    for k, v in d.items():
        if k not in names:
            continue
        if k in _DATETIME_FIELDS and isinstance(v, str):
            v = datetime.fromisoformat(v)
        elif k == "beds":
            v = [(bed, datetime.fromisoformat(a), datetime.fromisoformat(b)) for bed, a, b in v]
        kwargs[k] = v
    return cls(**kwargs)


def _shift(d: dict, keys, delta: timedelta) -> None:
    for k in keys:
        if d.get(k):
            d[k] = (datetime.fromisoformat(d[k]) + delta).isoformat()


class SimBuilder(Builder):
    """The generator's resource factories, writing into the live store. Ids the generator would number
    from a counter are scoped to the plan event instead (`medrx-stay-00012-adm-1`): the same plan gives
    the same ids, and applying an event twice would fail on the duplicate id rather than double the data."""

    def __init__(self, store: Store, seed: int, now: datetime) -> None:
        super().__init__(store, seed, now)
        self.scope = "sim"
        self.created: list[tuple[str, str]] = []

    def next_id(self, prefix: str, width: int = 6) -> str:
        key = f"{prefix}:{self.scope}"
        self._counters[key] += 1
        return f"{prefix}-{self.scope}-{self._counters[key]}"

    def add(self, resource: dict) -> dict:
        stored = super().add(resource)
        self.created.append((stored["resourceType"], stored["id"]))
        return stored


def _is_ward_bed(location: str | None) -> bool:
    """Inpatient beds have a tracked status; ED bays and unit-level locations do not."""
    return bool(location) and location.count("-") == 2 and not location.startswith("ED-")


def _current(enc: dict) -> str | None:
    loc = next((x for x in reversed(enc.get("location") or []) if x.get("status") == "active"), None)
    return ref_id(loc["location"]) if loc else None


def _history(enc: dict, status: str, at: datetime) -> None:
    for h in enc.get("statusHistory") or []:
        period = h.setdefault("period", {})
        period.setdefault("end", fhir_datetime(at))
    enc.setdefault("statusHistory", []).append({"status": status, "period": {"start": fhir_datetime(at)}})
    enc["status"] = status


def _move(enc: dict, location: str | None, at: datetime) -> str | None:
    """Close the current location entry and append `location`; returns the location left."""
    previous = None
    for loc in enc.get("location") or []:
        if loc.get("status") == "active":
            loc["status"] = "completed"
            loc.setdefault("period", {})["end"] = fhir_datetime(at)
            previous = ref_id(loc["location"])
    if location:
        enc.setdefault("location", []).append(
            {"location": ref("Location", location), "status": "active", "period": {"start": fhir_datetime(at)}})
    return previous


def _finish(enc: dict, at: datetime, disposition: str | None = None) -> str | None:
    if enc.get("statusHistory"):
        for h in enc["statusHistory"]:
            h.setdefault("period", {}).setdefault("end", fhir_datetime(at))
    enc["status"] = "finished"
    enc.setdefault("period", {})["end"] = fhir_datetime(at)
    previous = _move(enc, None, at)
    if disposition:
        enc.setdefault("hospitalization", {})["dischargeDisposition"] = C.concept(C.DISCHARGE_DISPOSITION, disposition)
    return previous


def _set_ext(resource: dict, name: str, **value) -> None:
    exts = [e for e in resource.get("extension") or [] if e.get("url") != C.EXT + name]
    resource["extension"] = [*exts, ext(name, **value)]


class _Run:
    def __init__(self, store: Store, plan: dict, start: datetime, until: datetime) -> None:
        from app.core.config import settings

        self.store, self.fhir, self.plan = store, store.fhir, plan
        self.events: list[dict] = plan["events"]
        self.start, self.until, self.t = start, until, start
        self.horizon = parse(plan["horizon"])
        self.seed = plan.get("seed", settings.seed)
        self.b = SimBuilder(store, self.seed, start)
        self.adapter = Hl7EventAdapter()
        self.out: list[DomainEvent] = []
        self.published: list[DomainEvent] = []
        self.applied: Counter = Counter()
        self.deferred = 0
        self.vitals = 0
        self.plan_changed = False
        self.written: set[tuple[str, str]] = set()
        self.i = 0
        self.lo = 0  # inserted events go at or after this index (after the event being applied)
        self.ctx: dict = {}  # the current plan entry's scenario (operator, correlation id) if injected
        self._patients: dict[str, PatientInfo] = {}
        self._bases: dict[str, dict[str, float] | None] = {}

    # ----- driver -----

    def execute(self) -> None:
        self.i = self.lo = _first_after(self.events, self.start)
        marks = self._vitals_marks()
        self._schedule_cleaning(self.start)
        while self.i < len(self.events):
            e = self.events[self.i]
            at = parse(e["at"])
            if at > self.until:
                break
            while marks and marks[0] <= at:
                self._vitals_round(marks.pop(0))
            self.t = at
            self.lo = self.i + 1
            self._apply(e)
            self.i += 1
            if len(self.out) >= 500:
                self._flush()
        for mark in marks:
            self._vitals_round(mark)
        self.t = self.until
        self._flush()

    def _apply(self, e: dict) -> None:
        handler = getattr(self, "_on_" + e["kind"], None)
        if handler is None:
            log.warning("simulator: no handler for plan event kind %s", e["kind"])
            return
        self.ctx = self._scenario(e)
        if handler(e) is not False:
            self.applied[e["kind"]] += 1

    def _flush(self) -> None:
        if self.out:
            self.published += bus.publish_many(self.out)
            self.out = []

    def result(self) -> AdvanceResult:
        written = self.written | set(self.b.created)
        return AdvanceResult(self.start, self.until, dict(self.applied), dict(Counter(e.type for e in self.published)),
                             self.deferred, self.vitals, self.published, written)

    # ----- plan entries -----

    def _scenario(self, e: dict) -> dict:
        for key, table in (("visit", "visits"), ("stay", "stays"), ("surgery", "surgeries"), ("order", "orders")):
            if key in e:
                return (self.plan[table].get(e[key]) or {}).get("scenario") or {}
        return e.get("scenario") or {}

    def _visit(self, vid: str) -> EdVisit:
        return _load(EdVisit, self.plan["visits"][vid])

    def _stay(self, sid: str) -> Stay:
        return _load(Stay, self.plan["stays"][sid])

    def _surgery(self, sid: str) -> Surgery:
        return _load(Surgery, self.plan["surgeries"][sid])

    def _order(self, oid: str) -> OrderSpec:
        return _load(OrderSpec, self.plan["orders"][oid])

    def _insert(self, e: dict) -> None:
        """Add an event to the plan; it is always later than the event being applied."""
        bisect.insort(self.events, e, lo=self.lo, key=plan_order)
        self.plan_changed = True

    def _patient(self, pid: str) -> PatientInfo:
        if pid not in self._patients:
            self._patients[pid] = patient_info(self.fhir, pid, parse(self.plan["generated_for"]).date())
        return self._patients[pid]

    # ----- FHIR helpers -----

    def _save(self, resource: dict) -> None:
        self.fhir.update(resource)
        self.written.add((resource["resourceType"], resource["id"]))

    def _bed_status(self, bed: str) -> str:
        loc = self.fhir.read("Location", bed) or {}
        return (loc.get("operationalStatus") or {}).get("code", "U")

    def _set_bed(self, bed: str, status: str) -> None:
        loc = self.fhir.read("Location", bed)
        if loc is None or (loc.get("operationalStatus") or {}).get("code") == status:
            return
        loc["operationalStatus"] = C.coding(C.BED_STATUS, status, BED_DISPLAY[status])
        self._save(loc)

    def _free_bed(self, units: list[str], prefer: str | None = None) -> str | None:
        if prefer and self._bed_status(prefer) == "U":
            return prefer
        for unit in units:
            free = self.fhir.ids("Location", unit=unit, code="bd", status="U")
            if free:
                return free[0]
        return None

    def _free_bay(self, prefer: str | None) -> str | None:
        taken = {_current(enc) for enc in self.fhir.search("Encounter", unit="ED", status=list(ACTIVE))}
        if prefer and prefer not in taken:
            return prefer
        return next((bay for bay in bed_ids("ED") if bay not in taken), None)

    def _vacate(self, location: str | None) -> None:
        """A bed left by a discharge or transfer waits for housekeeping."""
        if not _is_ward_bed(location):
            return
        self._set_bed(location, "K")
        if not self._cleaning_planned(location, self.t):
            self._plan_cleaning(location, self.t)

    def _cleaning_planned(self, bed: str, after: datetime) -> bool:
        end = (after + timedelta(hours=3)).isoformat()
        lo = _first_after(self.events, after)
        for e in self.events[lo:]:
            if e["at"] > end:
                return False
            if e["kind"] == "bed_clean" and e.get("bed") == bed:
                return True
        return False

    def _plan_cleaning(self, bed: str, at: datetime) -> None:
        rng = random.Random(f"hospital:{self.seed}:clean:{bed}:{at.isoformat()}")
        when = at + timedelta(minutes=rng.randint(*CLEAN_MINUTES))
        self._insert({"at": when.isoformat(), "kind": "bed_clean", "bed": bed, "derived": True})

    def _schedule_cleaning(self, at: datetime) -> None:
        """Beds waiting for housekeeping that nothing will clean (e.g. left by a bed manager's move)."""
        for bed in self.fhir.ids("Location", code="bd", status="K"):
            if _is_ward_bed(bed) and not self._cleaning_planned(bed, at):
                self._plan_cleaning(bed, at)

    # ----- HL7 out -----

    def _msg(self, message_type: str, key: str, *, cls: str | None = None, **fields) -> None:
        digest = hashlib.sha1(f"{self.plan['generated_for']}|{key}|{message_type}".encode()).hexdigest()
        message = Hl7Message(message_type=message_type, control_id="SIM" + digest[:17].upper(), event_at=self.t,
                             operator=self.ctx.get("operator"), correlation_id=self.ctx.get("correlation_id"),
                             patient_class=CLASS_CODE.get(cls) if cls else None, **fields)
        self.out.append(self.adapter.convert(message))

    def _key(self, e: dict) -> str:
        return "|".join(str(e.get(k, "")) for k in ("kind", "visit", "stay", "surgery", "order", "bed", "at"))

    # ----- ED -----

    def _on_ed_arrival(self, e: dict):
        v = self._visit(e["visit"])
        if self.fhir.read("Encounter", v.id):
            return False
        self.b.scope = f"{v.id}-arr"
        M.write_ed_visit(self.b, v, self._patient(v.patient), None, self.t)
        self._msg("ADT^A01", self._key(e), cls="EMER", patient=v.patient, visit=v.id, location="ED")

    def _ed_encounter(self, vid: str) -> dict | None:
        enc = self.fhir.read("Encounter", vid)
        return enc if enc and enc.get("status") in ACTIVE else None

    def _on_ed_triage(self, e: dict):
        v = self._visit(e["visit"])
        enc = self._ed_encounter(v.id)
        if enc is None or enc["status"] != "arrived":
            return False
        _history(enc, "triaged", self.t)
        self._save(enc)
        self.b.scope = f"{v.id}-tri"
        self.b.observation(v.patient, v.id, C.CTAS, self.t, value=v.ctas, category="survey", rid=f"ctas-{v.id}")
        M.vitals_panel(self.b, types.SimpleNamespace(id=v.patient), v.id, self.t, v.vitals, rid=f"vit-{v.id}")
        self._msg("ADT^A08", self._key(e), cls="EMER", patient=v.patient, visit=v.id, location=_current(enc),
                  reason="TRIAGED")

    def _on_ed_seen(self, e: dict):
        v = self._visit(e["visit"])
        enc = self._ed_encounter(v.id)
        if enc is None or enc["status"] == "in-progress":
            return False
        _history(enc, "in-progress", self.t)
        bay = self._free_bay(v.bay)
        if bay:
            _move(enc, bay, self.t)
        if bay != v.bay:
            self.plan["visits"][v.id]["bay"] = bay
            self.plan_changed = True
        self._save(enc)
        self._msg("ADT^A08", self._key(e), cls="EMER", patient=v.patient, visit=v.id, location=bay or "ED",
                  reason="SEEN")

    def _on_ed_decision(self, e: dict):
        v = self._visit(e["visit"])
        enc = self._ed_encounter(v.id)
        if enc is None:
            return False
        self.b.scope = f"{v.id}-dec"
        if not v.lwbs:
            self.b.condition(v.patient, v.dx, self.t, enc=v.id, category="encounter-diagnosis",
                             clinical="active" if v.admit else "resolved")
        if v.admit and v.stay:
            stay = self.plan["stays"][v.stay]
            unit = unit_of(stay["beds"][0][0]) if stay["beds"] else stay.get("first_unit", stay["service_unit"])
            self.b.service_request(v.patient, v.id, C.concept(C.SNOMED, "32485007", "Hospital admission"), self.t,
                                   category="bed-request", status="active", priority="urgent", location=unit,
                                   reason=v.dx, rid=f"bedreq-{v.id}")
        disposition = "lwbs" if v.lwbs else ("admit" if v.admit else "home")
        self._msg("ADT^A08", self._key(e), cls="EMER", patient=v.patient, visit=v.id, location=_current(enc),
                  reason="DECISION", disposition=disposition)

    def _on_ed_depart(self, e: dict):
        v = self._visit(e["visit"])
        enc = self._ed_encounter(v.id)
        if enc is None:
            return False
        outcome = "lwbs" if v.lwbs else "home"
        left = _finish(enc, self.t, {"lwbs": "aadvice", "home": "home"}[outcome])
        _set_ext(enc, "ed-disposition", valueCode=outcome)
        self._save(enc)
        self._msg("ADT^A03", self._key(e), cls="EMER", patient=v.patient, visit=v.id, prior_location=left,
                  disposition=outcome)

    # ----- inpatient -----

    def _first_unit(self, sdict: dict) -> str:
        return unit_of(sdict["beds"][0][0]) if sdict["beds"] else sdict.get("first_unit", sdict["service_unit"])

    def _on_admit(self, e: dict):
        sdict = self.plan["stays"][e["stay"]]
        if self.fhir.read("Encounter", sdict["id"]):
            return False
        unit = self._first_unit(sdict)
        planned = sdict["beds"][0][0] if sdict["beds"] else None
        bed = self._free_bed([unit, *OVERFLOW.get(unit, [])], prefer=planned)
        if bed is None:
            self._defer_stay(sdict["id"])
            return False
        if planned is None:
            first_until = e.get("ward_at") or sdict["discharge"]
            sdict["beds"] = [[bed, self.t.isoformat(), first_until]]
        elif bed != planned:
            sdict["beds"][0][0] = bed
        if bed != planned:
            self.plan_changed = True
        s = self._stay(sdict["id"])
        ed = None
        if s.ed_visit and s.ed_visit in self.plan["visits"]:
            ed = self._visit(s.ed_visit)
            self._close_ed_for_admission(ed, bed)
        surgery = self._surgery(s.surgery) if s.surgery and s.surgery in self.plan["surgeries"] else None
        self.b.scope = f"{s.id}-adm"
        M.write_stay(self.b, s, self._patient(s.patient), surgery, ed, self.t)
        self._set_bed(bed, "O")
        self._msg("ADT^A01", self._key(e), cls="IMP", patient=s.patient, visit=s.id, location=bed)

    def _close_ed_for_admission(self, v: EdVisit, bed: str) -> None:
        enc = self._ed_encounter(v.id)
        if enc is None:
            return
        left = _finish(enc, self.t, "oth")
        _set_ext(enc, "ed-disposition", valueCode="admitted")
        self._save(enc)
        request = self.fhir.read("ServiceRequest", f"bedreq-{v.id}")
        if request is not None:
            request["status"] = "completed"
            request["occurrenceDateTime"] = fhir_datetime(self.t)
            request["locationReference"] = [ref("Location", unit_of(bed))]
            self._save(request)
        self._msg("ADT^A03", f"admit-from-ed|{v.id}|{self.t.isoformat()}", cls="EMER", patient=v.patient, visit=v.id,
                  prior_location=left, disposition="admitted")

    def _defer_stay(self, sid: str) -> None:
        """No free bed: the patient keeps boarding, and the stay's remaining events move 30 minutes later."""
        orders = {oid for oid, o in self.plan["orders"].items() if o["encounter"] == sid}
        tail = self.events[self.i + 1:]
        moved = [x for x in tail if x.get("stay") == sid or x.get("order") in orders]
        moved_ids = {id(x) for x in moved}
        del self.events[self.i + 1:]
        self.events.extend(x for x in tail if id(x) not in moved_ids)
        sdict = self.plan["stays"][sid]
        _shift(sdict, ("admit", "discharge", "alc_from"), RETRY)
        sdict["beds"] = [[bed, (datetime.fromisoformat(a) + RETRY).isoformat(), (datetime.fromisoformat(b) + RETRY).isoformat()]
                         for bed, a, b in sdict["beds"]]
        for oid in orders:
            _shift(self.plan["orders"][oid], ("authored", "completed"), RETRY)
        retry = {"at": (self.t + RETRY).isoformat(), "kind": "admit", "stay": sid}
        if self.events[self.i].get("ward_at"):
            retry["ward_at"] = (datetime.fromisoformat(self.events[self.i]["ward_at"]) + RETRY).isoformat()
        for x in moved:
            x["at"] = (datetime.fromisoformat(x["at"]) + RETRY).isoformat()
            if "ward_at" in x:
                x["ward_at"] = (datetime.fromisoformat(x["ward_at"]) + RETRY).isoformat()
        for x in [retry, *moved]:
            self._insert(x)
        self.deferred += 1

    def _stay_encounter(self, sid: str) -> dict | None:
        enc = self.fhir.read("Encounter", sid)
        return enc if enc and enc.get("status") == "in-progress" else None

    def _on_transfer(self, e: dict):
        sdict = self.plan["stays"][e["stay"]]
        enc = self._stay_encounter(sdict["id"])
        if enc is None:
            return False
        target = e.get("bed")
        unit = unit_of(target) if target else e["unit"]
        bed = self._free_bed([unit, *OVERFLOW.get(unit, [])], prefer=target)
        if bed is None:
            retry_at = self.t + RETRY
            if retry_at < datetime.fromisoformat(sdict["discharge"]):
                self._insert({**e, "at": retry_at.isoformat()})
                self.deferred += 1
            return False
        left = _move(enc, bed, self.t)
        self._save(enc)
        self._set_bed(bed, "O")
        self._vacate(left)
        self._msg("ADT^A02", self._key(e), cls="IMP", patient=sdict["patient"], visit=sdict["id"], location=bed,
                  prior_location=left)

    def _on_discharge(self, e: dict):
        s = self._stay(e["stay"])
        enc = self._stay_encounter(s.id)
        if enc is None:
            return False
        left = _finish(enc, self.t, s.disposition)
        self._save(enc)
        for cond in self.fhir.search("Condition", encounter=s.id, status="active"):
            cond["clinicalStatus"] = C.concept(C.CONDITION_CLINICAL, "resolved")
            self._save(cond)
        for med in self.fhir.search("MedicationRequest", encounter=s.id, status="active"):
            med["status"] = "completed"
            self._save(med)
        for flag in self.fhir.search("Flag", encounter=s.id, status="active"):
            flag["status"] = "inactive"
            flag.setdefault("period", {})["end"] = fhir_datetime(self.t)
            self._save(flag)
        self._vacate(left)
        self._msg("ADT^A03", self._key(e), cls="IMP", patient=s.patient, visit=s.id, prior_location=left,
                  disposition=s.disposition)

    def _on_alc(self, e: dict):
        """The stay is designated alternate level of care: medically done, waiting for a place elsewhere."""
        sdict = self.plan["stays"][e["stay"]]
        enc = self._stay_encounter(sdict["id"])
        if enc is None or self.fhir.search("Flag", encounter=sdict["id"], code="alc", status="active"):
            return False
        self.b.scope = f"{sdict['id']}-alc"
        self.b.flag(sdict["patient"], ("alc", "Alternate level of care"), self.t, enc=sdict["id"])
        self._msg("ADT^A08", self._key(e), cls="IMP", patient=sdict["patient"], visit=sdict["id"],
                  location=_current(enc), reason="ALC")

    def _on_bed_clean(self, e: dict):
        if self._bed_status(e["bed"]) != "K":
            return False
        self._set_bed(e["bed"], "U")
        self._msg("ADT^A20", self._key(e), location=e["bed"], bed_status="U")

    def _vitals_marks(self) -> list[datetime]:
        mark = self.start.replace(minute=0, second=0, microsecond=0)
        mark += timedelta(hours=(VITALS_HOURS - mark.hour % VITALS_HOURS) % VITALS_HOURS)
        if mark <= self.start:
            mark += timedelta(hours=VITALS_HOURS)
        marks = []
        while mark <= self.until:
            marks.append(mark)
            mark += timedelta(hours=VITALS_HOURS)
        return marks

    def _vitals_base(self, enc_id: str) -> dict[str, float] | None:
        """The admission vital signs the ward values settle from (read once per run)."""
        if enc_id not in self._bases:
            admission = self.fhir.read("Observation", f"vit-{enc_id}-adm")
            self._bases[enc_id] = None if admission is None else {
                c["code"]["coding"][0]["code"]: c["valueQuantity"]["value"] for c in admission["component"]}
        return self._bases[enc_id]

    def _vitals_round(self, mark: datetime) -> None:
        """Ward vital signs every six hours for every inpatient in a bed (the generator's ward_vitals)."""
        self.t = mark
        self.ctx = {}
        for enc in self.fhir.search("Encounter", cls="IMP", status="in-progress"):
            admitted = parse(enc["period"]["start"])
            if mark < admitted + timedelta(hours=VITALS_HOURS):
                continue
            rid = f"vit-{enc['id']}-{mark:%Y%m%d%H}"  # each mark is after the run's start, so it is new
            base = self._vitals_base(enc["id"])
            if base is None:
                continue
            rng = random.Random(f"hospital:{self.seed}:vitals:{enc['id']}:{mark:%Y%m%d%H}")
            values = M.ward_vitals(rng, base, (mark - admitted).total_seconds() / 3600)
            pid = ref_id(enc["subject"])
            M.vitals_panel(self.b, types.SimpleNamespace(id=pid), enc["id"], mark, values, rid=rid)
            self.vitals += 1
            self._msg("ORU^R01", f"vitals|{enc['id']}|{mark.isoformat()}", cls="IMP", patient=pid, visit=enc["id"],
                      location=_current(enc), section="VS", results=(f"Observation/{rid}",))

    # ----- orders and results -----

    def _on_order(self, e: dict):
        o = self._order(e["order"])
        if self.fhir.read("ServiceRequest", o.id):
            return False
        enc = self.fhir.read("Encounter", o.encounter)
        if enc is None:  # the stay has not started (its admission is waiting for a bed)
            self._insert({**e, "at": (self.t + RETRY).isoformat()})
            self.deferred += 1
            return False
        self.b.scope = o.id
        M.write_order(self.b, o, self.t)
        self._msg("ORM^O01", self._key(e), cls=enc["class"]["code"], patient=o.patient, visit=o.encounter,
                  order=o.id, section=SERVICE_SECTION[o.kind])

    def _on_result(self, e: dict):
        o = self._order(e["order"])
        request = self.fhir.read("ServiceRequest", o.id)
        enc = self.fhir.read("Encounter", o.encounter)
        if enc is None:
            self._insert({**e, "at": (self.t + RETRY).isoformat()})
            self.deferred += 1
            return False
        if request is not None and request.get("status") == "completed":
            return False
        self.b.scope = f"{o.id}-res"
        before = len(self.b.created)
        if request is None:
            M.write_order(self.b, replace_completed(o, self.t), self.t)
        else:
            request["status"] = "completed"
            request["occurrenceDateTime"] = fhir_datetime(self.t)
            self._save(request)
            M.write_result(self.b, replace_completed(o, self.t))
        results = tuple(f"{rt}/{rid}" for rt, rid in self.b.created[before:] if rt in ("Observation", "DiagnosticReport"))
        self._msg("ORU^R01", self._key(e), cls=enc["class"]["code"], patient=o.patient, visit=o.encounter, order=o.id,
                  section=SERVICE_SECTION[o.kind], results=results)

    # ----- operating rooms -----

    def _appointment(self, sid: str) -> dict | None:
        found = self.fhir.search("Appointment", focus=f"orreq-{sid}", limit=1)
        return found[0] if found else None

    def _surgery_encounter(self, sg: Surgery) -> str:
        """The encounter the case belongs to now: the stay, the day-surgery visit, else where it was booked."""
        if sg.stay and self._stay_encounter(sg.stay):
            return sg.stay
        if sg.day_surgery and self.fhir.read("Encounter", f"amb-{sg.id}"):
            return f"amb-{sg.id}"
        stay = self.plan["stays"].get(sg.stay) if sg.stay else None
        if stay and stay.get("ed_visit") and self.fhir.read("Encounter", stay["ed_visit"]):
            return stay["ed_visit"]
        return self._patient(sg.patient).clinic_encounter

    def _on_surgery_booked(self, e: dict):
        sg = self._surgery(e["surgery"])
        if self.fhir.read("ServiceRequest", f"orreq-{sg.id}"):
            return False
        stay = self._stay(sg.stay) if sg.stay and sg.stay in self.plan["stays"] else None
        self.b.scope = f"{sg.id}-book"
        M.write_surgery(self.b, sg, self._patient(sg.patient), self.t, self.horizon, stay)
        appt = self._appointment(sg.id)
        self._msg("SIU^S12", self._key(e), patient=sg.patient, visit=self._surgery_encounter(sg),
                  appointment=appt["id"], appointment_status="Booked", location=sg.room)

    def _on_surgery_cancel(self, e: dict):
        sg = self._surgery(e["surgery"])
        appt = self._appointment(sg.id)
        if appt is None or appt["status"] != "booked":
            return False
        appt["status"] = "cancelled"
        self._save(appt)
        request = self.fhir.read("ServiceRequest", f"orreq-{sg.id}")
        if request is not None:
            request["status"] = "revoked"
            request["note"] = [{"text": sg.cancel_reason or "Cancelled"}]
            self._save(request)
        for task in self.fhir.search("Task", focus=f"Appointment/{appt['id']}", status="requested"):
            task["status"] = "cancelled"
            task["lastModified"] = fhir_datetime(self.t)
            self._save(task)
        self._msg("SIU^S15", self._key(e), patient=sg.patient, visit=self._surgery_encounter(sg),
                  appointment=appt["id"], appointment_status="Cancelled", location=sg.room)

    def _on_surgery_start(self, e: dict):
        sg = self._surgery(e["surgery"])
        appt = self._appointment(sg.id)
        if appt is None or appt["status"] != "booked":
            return False
        encounter = self._surgery_encounter(sg)
        appt["status"] = "arrived"
        _set_ext(appt, "encounter", valueReference=ref("Encounter", encounter))
        self._save(appt)
        for task in self.fhir.search("Task", focus=f"Appointment/{appt['id']}", status="requested"):
            task["status"] = "completed"  # the team closes open pre-op checks before the start
            task["lastModified"] = fhir_datetime(self.t)
            self._save(task)
        self.b.scope = f"{sg.id}-start"
        self.b.procedure(sg.patient, encounter, sg.code, self.t, None, status="in-progress", performer=sg.surgeon,
                         location=sg.room, asa=sg.asa, urgency=sg.urgency, based_on=f"orreq-{sg.id}", rid=f"proc-{sg.id}")
        self._msg("SIU^S14", self._key(e), patient=sg.patient, visit=encounter, appointment=appt["id"],
                  appointment_status="Arrived", location=sg.room)

    def _on_surgery_end(self, e: dict):
        sg = self._surgery(e["surgery"])
        appt = self._appointment(sg.id)
        procedure = self.fhir.read("Procedure", f"proc-{sg.id}")
        if appt is None or appt["status"] != "arrived" or procedure is None:
            return False
        appt["status"] = "fulfilled"
        self._save(appt)
        procedure["status"] = "completed"
        procedure["performedPeriod"]["end"] = fhir_datetime(self.t)
        self._save(procedure)
        request = self.fhir.read("ServiceRequest", f"orreq-{sg.id}")
        if request is not None:
            request["status"] = "completed"
            self._save(request)
        if sg.stay and self._stay_encounter(sg.stay):
            stay = self.plan["stays"].get(sg.stay) or {}
            home = self._patient(sg.patient).home_meds
            self.b.scope = f"{sg.id}-postop"
            for rxcui in POSTOP_MEDS:
                if rxcui not in home and rxcui not in INPATIENT_MEDS.get(stay.get("dx", ""), []):
                    self.b.medication_request(sg.patient, sg.stay, rxcui, self.t + timedelta(minutes=30),
                                              requester=sg.surgeon, prn=rxcui in ("3423", "26225"))
        self._msg("SIU^S14", self._key(e), patient=sg.patient, visit=self._surgery_encounter(sg),
                  appointment=appt["id"], appointment_status="Complete", location=sg.room)

    def _on_day_surgery_arrival(self, e: dict):
        sg = self._surgery(e["surgery"])
        appt = self._appointment(sg.id)
        if self.fhir.read("Encounter", f"amb-{sg.id}") or appt is None or appt["status"] != "booked":
            return False
        self.b.scope = f"{sg.id}-arr"
        self.b.encounter(sg.patient, "AMB", self.t, service="day-surgery", reason=C.snomed(sg.code),
                         locations=[("SURG", self.t, None)], rid=f"amb-{sg.id}")
        _set_ext(appt, "encounter", valueReference=ref("Encounter", f"amb-{sg.id}"))
        self._save(appt)
        self._msg("ADT^A01", self._key(e), cls="AMB", patient=sg.patient, visit=f"amb-{sg.id}", location="SURG")

    def _on_day_surgery_leave(self, e: dict):
        sg = self._surgery(e["surgery"])
        enc = self.fhir.read("Encounter", f"amb-{sg.id}")
        if enc is None or enc["status"] == "finished":
            return False
        left = _finish(enc, self.t)
        self._save(enc)
        self._msg("ADT^A03", self._key(e), cls="AMB", patient=sg.patient, visit=enc["id"], prior_location=left,
                  disposition="home")

    # ----- platform events from scripted scenarios -----

    def _on_consent_revoke(self, e: dict):
        consent = self.fhir.read("Consent", e["consent"])
        if consent is None or (consent.get("provision") or {}).get("type") == "deny":
            return False
        consent["provision"]["type"] = "deny"
        consent["dateTime"] = fhir_datetime(self.t)
        self._save(consent)
        category = consent["category"][0]["coding"][0]["code"]
        self.out.append(platform_event(
            "consent.revoked", at=self.t, actor=f"user:{self.ctx['operator']}" if self.ctx.get("operator") else SIM_ACTOR,
            refs={"patient": consent["patient"]["reference"], "consent": f"Consent/{consent['id']}"},
            attrs={"category": category}, correlation_id=self.ctx.get("correlation_id"),
            key=f"{self.plan['generated_for']}|{self._key(e)}|consent"))


def patient_info(fhir, pid: str, day: date) -> PatientInfo:
    """What the generator's builders need to know about a patient, read back from the FHIR store."""
    p = fhir.read("Patient", pid)
    birth = date.fromisoformat(p["birthDate"])
    age = day.year - birth.year - ((day.month, day.day) < (birth.month, birth.day))
    meds = [s["medicationCodeableConcept"]["coding"][0]["code"]
            for s in fhir.search("MedicationStatement", patient=pid, status="active")]
    clinic = fhir.search("Encounter", patient=pid, cls="AMB", code="outpatient-clinic", order="-date", limit=1)
    egfr = fhir.search("Observation", patient=pid, code="33914-3", order="-date", limit=1)
    return PatientInfo(id=pid, mrn="", age=age, sex=p.get("gender", ""), lang="", birth=birth, home_meds=meds,
                       egfr=egfr[0]["valueQuantity"]["value"] if egfr else 90.0,
                       clinic_encounter=clinic[0]["id"] if clinic else "")


def replace_completed(order: OrderSpec, at: datetime) -> OrderSpec:
    """The order as resulted now (a result retried after a wait keeps its own time)."""
    return order if order.completed == at else dataclasses.replace(order, completed=at)


# ---------- consistency check (tests, demo script and the WP3 report) ----------


def consistency_problems(store: Store) -> list[str]:
    """Invariants the simulated hospital must keep after any number of steps."""
    fhir = store.fhir
    problems: list[str] = []
    occupants: dict[str, list[str]] = {}
    for enc in fhir.search("Encounter", status=list(ACTIVE)):
        where = _current(enc)
        if where and where.count("-") == 2:
            occupants.setdefault(where, []).append(enc["id"])
    for bed, encs in occupants.items():
        if len(encs) > 1:
            problems.append(f"{bed} holds {len(encs)} active encounters: {encs}")
    for loc in fhir.search("Location", code="bd"):
        if not _is_ward_bed(loc["id"]):
            continue
        status_code = (loc.get("operationalStatus") or {}).get("code", "U")
        if (status_code == "O") != (loc["id"] in occupants):
            problems.append(f"{loc['id']} is {status_code} but has {len(occupants.get(loc['id'], []))} occupant(s)")
    for enc in fhir.search("Encounter", status="finished", date_from=parse(clock(store)["now"]) - timedelta(days=3)):
        if not enc.get("period", {}).get("end") or _current(enc):
            problems.append(f"finished Encounter/{enc['id']} has no end or still an active location")
    t = fhir_resources
    clinical = ["Observation", "Condition", "MedicationRequest", "MedicationStatement", "AllergyIntolerance",
                "Procedure", "DiagnosticReport", "ServiceRequest"]
    conn = store.conn()
    encounters = set(conn.execute(select(t.c.id).where(t.c.resource_type == "Encounter")).scalars())
    for rtype, rid, enc in conn.execute(select(t.c.resource_type, t.c.id, t.c.encounter)
                                        .where(t.c.resource_type.in_(clinical))):
        if enc is None or enc not in encounters:
            problems.append(f"{rtype}/{rid} points to a missing encounter {enc}")
            if len(problems) > 50:
                break
    return problems
