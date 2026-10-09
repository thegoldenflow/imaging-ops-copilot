"""Scripted scenarios for the day simulator (spec 6.2: "ICU receives 3 emergency patients").

A scenario only adds to the plan: new ED visits with their stays and orders, or
changed times for planned OR cases. The simulator then applies them like any
planned event, as the clock reaches them, with the same bed checks: if ICU is
full the new patients board in the ED and planned ICU admissions wait, which is
the exception flow the Control Tower (WP5) has to show.

Every entry a scenario adds carries `scenario` = {name, correlation_id,
operator}: the HL7 messages name the user who injected it (EVN-5), and the
domain events share the scenario's correlation id, so one id traces the whole
injection through the event log and the audit record.

Injected patients are existing synthetic patients who are not in hospital and
have nothing planned; ids are `ed-x0001`, `stay-x0001`, `surg-x0001`.
"""

from __future__ import annotations

import math
import random
import uuid
from datetime import datetime, timedelta

from app.core.store import Store
from app.ehr import codes as C
from app.ehr.reference import ADMIT_DX, EMERGENCY_OR, EXPECTED_LOS, SURGEONS, TURNOVER_MIN
from app.ehr.seed import materialize as M
from app.ehr.seed import plan_order
from app.ehr.seed.builder import Builder
from app.ehr.seed.simulate import EdVisit, Stay, Surgery, _vitals, surgical_minutes
from app.ehr.simulator import _locked, clock, patient_info
from app.fhir.dt import parse

SCENARIOS = {
    "icu_surge": "Critically ill ED arrivals who need ICU, 3 minutes apart: by default enough to fill ICU plus one "
                 "(at least 3). ICU has no overflow unit, so the last of them and planned ICU admissions board in the ED",
    "ed_surge": "8 ED arrivals in 40 minutes; about half need a bed, one is a hip fracture booked for emergency surgery",
    "or_overrun": "The next elective OR case runs 50 minutes over and pushes the rest of that room's list",
    "consent_revoked": "An inpatient withdraws consent to SMS messages (platform event consent.revoked, no HL7)",
}

# (complaint, CTAS, admission diagnosis) for the ICU surge
_ICU_CASES = [("shortness of breath", 2, "233604007"), ("fever", 1, "91302008"), ("chest pain", 2, "22298006")]
_ED_COMPLAINTS = ["chest pain", "abdominal pain", "shortness of breath", "fever", "injury", "back pain", "headache"]


class ScenarioError(ValueError):
    pass


def inject(store: Store, name: str, *, operator: str, count: int | None = None) -> dict:
    """Add a scenario to the plan, starting a minute from the hospital clock. Returns what was added."""
    if name not in SCENARIOS:
        raise ScenarioError(f"Unknown scenario {name!r}; one of {sorted(SCENARIOS)}")
    _locked(store)
    plan = store.modules["hospital_plan"]
    now = parse(clock(store)["now"])
    horizon = parse(plan["horizon"])
    if now + timedelta(hours=2) > horizon:
        raise ScenarioError("Too close to the end of the planned day; reset the demo data first")
    ctx = {"name": name, "correlation_id": uuid.uuid4().hex, "operator": operator}
    n = plan.setdefault("injections", 0) + 1
    plan["injections"] = n
    rng = random.Random(f"hospital:{plan.get('seed')}:scenario:{name}:{n}")
    added = _Injector(store, plan, now, rng, ctx).run(name, count)
    plan["events"].sort(key=plan_order)
    store.modules["hospital_plan"] = plan
    return {"scenario": name, "description": SCENARIOS[name], "correlation_id": ctx["correlation_id"],
            "starts_at": now + timedelta(minutes=1), **added}


class _Injector:
    def __init__(self, store: Store, plan: dict, now: datetime, rng: random.Random, ctx: dict) -> None:
        self.store, self.plan, self.now, self.rng, self.ctx = store, plan, now, rng, ctx
        self.b = Builder(store, plan.get("seed", 0), now)  # only for its seeded random streams (ed/stay orders)
        self.day = parse(plan["generated_for"]).date()

    def run(self, name: str, count: int | None) -> dict:
        return self.icu_surge(count) if name == "icu_surge" else getattr(self, name)()

    # ----- helpers -----

    def _next(self, prefix: str) -> str:
        counters = self.plan.setdefault("injected_ids", {})
        counters[prefix] = counters.get(prefix, 0) + 1
        return f"{prefix}-x{counters[prefix]:04d}"

    def _event(self, at: datetime, kind: str, **refs) -> None:
        self.plan["events"].append({"at": at.isoformat(), "kind": kind, **refs})

    def _busy_patients(self) -> set[str]:
        busy = {r["patient"] for table in ("visits", "stays", "surgeries") for r in self.plan[table].values()}
        busy |= {e["subject"]["reference"].split("/")[1]
                 for e in self.store.fhir.search("Encounter", status=["arrived", "triaged", "in-progress", "planned"])}
        return busy

    def _patients(self, k: int, min_age: int = 18, max_age: int = 95) -> list:
        busy = self._busy_patients()
        ids = [pid for pid in self.store.fhir.ids("Patient") if pid not in busy]
        self.rng.shuffle(ids)
        out = []
        for pid in ids:
            p = patient_info(self.store.fhir, pid, self.day)
            if min_age <= p.age <= max_age:
                out.append(p)
                if len(out) == k:
                    break
        if len(out) < k:
            raise ScenarioError("Not enough patients outside hospital for this scenario")
        return out

    def _ed_visit(self, p, arrival: datetime, complaint: str, ctas: int, *, admit_dx: str | None,
                  treatment_min: float) -> EdVisit:
        rng = self.rng
        dx = admit_dx or C.COMPLAINTS[complaint][0]
        triage = arrival + timedelta(minutes=1 if ctas == 1 else rng.randint(2, 6))
        seen = triage + timedelta(minutes=rng.randint(2, 6) if ctas <= 2 else rng.randint(20, 60))
        decision = seen + timedelta(minutes=treatment_min)
        mode = "ambulance" if ctas <= 2 or rng.random() < 0.3 else "walk-in"
        visit = EdVisit(id=self._next("ed"), patient=p.id, arrival=arrival, complaint=complaint, ctas=ctas, mode=mode,
                        vitals=_vitals(rng, p, complaint, dx, ctas), triage=triage, seen=seen, decision=decision,
                        end=decision + timedelta(minutes=rng.randint(15, 40)), census=0, docs=0,
                        admit=admit_dx is not None, dx=dx)
        if visit.admit:
            visit.bed_request_at = decision
        return visit

    def _add_visit(self, v: EdVisit, p) -> None:
        self.plan["visits"][v.id] = {**M.plan_dict(v), "scenario": self.ctx}
        self._event(v.arrival, "ed_arrival", visit=v.id)
        self._event(v.triage, "ed_triage", visit=v.id)
        self._event(v.seen, "ed_seen", visit=v.id)
        self._event(v.decision, "ed_decision", visit=v.id)
        if not v.admit:
            self._event(v.end, "ed_depart", visit=v.id)
        for order in M.ed_orders(self.b, v, p):
            self._add_order(order)

    def _add_order(self, order) -> None:
        self.plan["orders"][order.id] = {**M.plan_dict(order), "scenario": self.ctx}
        self._event(order.authored, "order", order=order.id)
        self._event(order.completed, "result", order=order.id)

    def _add_stay(self, v: EdVisit, p, *, unit: str, icu: bool, surgery: Surgery | None = None) -> Stay:
        """An admission from the ED; the bed is chosen when it happens (ICU first when `icu`)."""
        rng = self.rng
        expected = EXPECTED_LOS[v.dx]
        admit = v.decision + timedelta(minutes=rng.randint(15, 30))
        los = expected * math.exp(rng.gauss(0, 0.25))
        if surgery is not None:
            los = max(los, (surgery.end - admit).total_seconds() / 86400 + C.OR_PROCEDURES[surgery.code][3] * 0.8)
        discharge = (admit + timedelta(days=los)).replace(hour=rng.choice([10, 11, 13, 15]), minute=rng.randint(0, 59))
        discharge = max(discharge, admit + timedelta(hours=20))
        attendings = sorted({s["attending"] for s in self.plan["stays"].values() if s["service_unit"] == unit and s["attending"]})
        stay = Stay(id=self._next("stay"), patient=p.id, admit=admit, discharge=discharge, dx=v.dx, service_unit=unit,
                    beds=[], source="ed", expected_los=expected, ed_visit=v.id,
                    surgery=surgery.id if surgery else None, disposition="home",
                    attending=rng.choice(attendings) if attendings else None)
        v.stay, v.end = stay.id, admit
        entry = {**M.plan_dict(stay), "scenario": self.ctx}
        admit_event = {"stay": stay.id}
        if icu:
            entry["first_unit"] = "ICU"
            ward_at = admit + timedelta(days=max(1.5, los * rng.uniform(0.45, 0.65)))
            if ward_at < discharge:
                admit_event["ward_at"] = ward_at.isoformat()
                self._event(ward_at, "transfer", stay=stay.id, unit=unit)
        self.plan["stays"][stay.id] = entry
        self._event(admit, "admit", **admit_event)
        self._event(discharge, "discharge", stay=stay.id)
        for order in M.stay_orders(self.b, stay, p):
            self._add_order(order)
        return stay

    # ----- scenarios -----

    def icu_surge(self, count: int | None = None) -> dict:
        # beds free or in housekeeping: a bed being cleaned is free again within the hour
        free_icu = sum(len(self.store.fhir.ids("Location", unit="ICU", code="bd", status=s)) for s in ("U", "K"))
        n = count or max(3, free_icu + 1)
        patients = self._patients(n, 55, 88)
        added = []
        for i, p in enumerate(patients):
            complaint, ctas, dx = _ICU_CASES[i % len(_ICU_CASES)]
            arrival = self.now + timedelta(minutes=1 + 3 * i, seconds=self.rng.randint(0, 50))
            v = self._ed_visit(p, arrival, complaint, ctas, admit_dx=dx, treatment_min=self.rng.randint(35, 60))
            unit = next(o[2] for o in ADMIT_DX[complaint] if o[0] == dx)
            stay = self._add_stay(v, p, unit=unit, icu=True)
            self._add_visit(v, p)
            added.append({"visit": v.id, "stay": stay.id, "arrival": v.arrival, "ctas": ctas})
        return {"patients": added, "icu_free_beds_now": free_icu}

    def ed_surge(self) -> dict:
        patients = self._patients(8, 25, 92)
        added = []
        for i, p in enumerate(patients):
            arrival = self.now + timedelta(minutes=1 + 5 * i, seconds=self.rng.randint(0, 50))
            if i == 2:  # the hip fracture
                complaint, ctas, dx = "fall", 3, "5913000"
            else:
                complaint = self.rng.choice(_ED_COMPLAINTS)
                ctas = self.rng.choices([2, 3, 4], weights=[0.3, 0.5, 0.2])[0]
                dx = None
                if ctas <= 3 and self.rng.random() < 0.5:
                    options = [o for o in ADMIT_DX[complaint] if not o[4]]
                    dx = self.rng.choice(options)[0] if options else None
            treatment = self.rng.randint(90, 180) if dx else self.rng.randint(60, 150)
            v = self._ed_visit(p, arrival, complaint, ctas, admit_dx=dx, treatment_min=treatment)
            entry = {"visit": v.id, "arrival": v.arrival, "ctas": ctas, "admit": v.admit}
            if dx:
                unit = next(o[2] for o in ADMIT_DX[complaint] if o[0] == dx)
                surgery = self._emergency_surgery(v, p) if dx == "5913000" else None
                stay = self._add_stay(v, p, unit=unit, icu=False, surgery=surgery)
                entry["stay"] = stay.id
                if surgery:
                    surgery.stay = stay.id
                    self.plan["surgeries"][surgery.id] = {**M.plan_dict(surgery), "scenario": self.ctx}
                    self._event(surgery.booked_at, "surgery_booked", surgery=surgery.id)
                    self._event(surgery.start, "surgery_start", surgery=surgery.id)
                    self._event(surgery.end, "surgery_end", surgery=surgery.id)
                    entry["surgery"] = surgery.id
            self._add_visit(v, p)
            added.append(entry)
        return {"patients": added}

    def _emergency_surgery(self, v: EdVisit, p) -> Surgery:
        """A hip replacement for the fracture, in the emergency room OR-04 after the elective list."""
        code = "52734007"
        surgeon = self.rng.choice([s for s in SURGEONS if code in s.procedures])
        booked = int(math.ceil(C.OR_PROCEDURES[code][1] / 15) * 15)
        start = self._or_gap(EMERGENCY_OR, v.decision + timedelta(hours=self.rng.uniform(5, 9)), timedelta(minutes=booked))
        minutes = surgical_minutes(self.rng, code, surgeon.id, 3, p.age, True, start)
        return Surgery(id=self._next("surg"), patient=p.id, code=code, surgeon=surgeon.id, room=EMERGENCY_OR,
                       booked_start=start, booked_minutes=booked, start=start, end=start + timedelta(minutes=minutes),
                       asa=3, urgency="emergent", booked_at=v.decision + timedelta(minutes=10))

    def _or_gap(self, room: str, earliest: datetime, length: timedelta) -> datetime:
        """The first start at or after `earliest` that fits between the room's planned cases."""
        turnover = timedelta(minutes=TURNOVER_MIN)
        cases = sorted((parse(s["start"]), parse(s["end"])) for s in self.plan["surgeries"].values()
                       if s["room"] == room and s["status"] != "cancelled")
        start = earliest
        for a, b in cases:
            if start + length + turnover <= a:
                break
            if b + turnover > start:
                start = b + turnover
        return start

    def or_overrun(self) -> dict:
        overrun = timedelta(minutes=50)
        cases = sorted((s for s in self.plan["surgeries"].values()
                        if s["status"] != "cancelled" and parse(s["end"]) > self.now and s["urgency"] == "elective"),
                       key=lambda s: s["start"])
        if not cases:
            raise ScenarioError("No elective OR case left in the planned day")
        first = cases[0]
        day = first["booked_start"][:10]
        later = [s for s in cases if s["room"] == first["room"] and s["booked_start"][:10] == day and s["start"] > first["start"]]
        moved = {first["id"]: ("end",)} | {s["id"]: ("start", "end") for s in later}
        for e in self.plan["events"]:
            sid = e.get("surgery")
            if sid in moved and parse(e["at"]) > self.now:
                kind = e["kind"]
                if (kind == "surgery_end" and "end" in moved[sid]) or (kind == "surgery_start" and "start" in moved[sid]) \
                        or (kind == "day_surgery_leave" and sid != first["id"]) or (kind == "day_surgery_leave" and sid == first["id"]):
                    e["at"] = (parse(e["at"]) + overrun).isoformat()
                    e["scenario"] = self.ctx
        for sid, fields in moved.items():
            entry = self.plan["surgeries"][sid]
            for f in fields:
                entry[f] = (parse(entry[f]) + overrun).isoformat()
            entry["scenario"] = self.ctx
        return {"case": first["id"], "room": first["room"], "overrun_minutes": 50, "pushed": [s["id"] for s in later]}

    def consent_revoked(self) -> dict:
        inpatients = [e["subject"]["reference"].split("/")[1]
                      for e in self.store.fhir.search("Encounter", cls="IMP", status="in-progress")]
        self.rng.shuffle(inpatients)
        for pid in inpatients:
            sms = [c for c in self.store.fhir.search("Consent", patient=pid, code="sms")
                   if (c.get("provision") or {}).get("type") == "permit"]
            if sms:
                self._event(self.now + timedelta(minutes=1), "consent_revoke", consent=sms[0]["id"], patient=pid,
                            scenario=self.ctx)
                return {"patient": pid, "consent": sms[0]["id"]}
        raise ScenarioError("No inpatient with an SMS consent on file")

