"""The storyline's preset data (spec 8.2 `demo/seed_journey.py`; applied by the storyline's reset, WP9).

What the twelve steps need before the story starts, written as the hospital generator writes its data: the
generator's builders and timeline types (`app/ehr/seed`), explicit ids (`*-j0001`, fill stays `stay-j1NN`), and
its own random stream (`hospital:<seed>:storyline:*`). Nothing here runs at the ordinary demo reset, so every other
demo, test and eval keeps the data it was built on; the storyline's reset (app/modules/storyline/service.py) is
the demo reset followed by `plant`.

1. The patient `pat-j0001`, in the language the presenter chose at the reset (app/modules/storyline/script.py
   `PATIENTS`): Margaret Ellis, English (the default), or Wang Xiuying, Mandarin (her usual name in Chinese too).
   Either way 72 (born on 8 March, so she is 72 on any hospital day), atrial fibrillation on warfarin, hypertension
   on amlodipine, metoprolol, atorvastatin, osteoporosis; one admission seven months ago; her daughter as contact;
   consents to AI processing, follow-up calls and SMS; a phone number. Her record at the imaging centre
   (`PT-J0001`, the imaging system's own patient table) and the ED physician as an imaging referrer (`R-ED01`).
   Linking the two records is the MPI roadmap card (8.1): the preset writes the link (matched on the health card
   number) instead of any matching logic.
2. The hospital state step 5 needs, whatever the weekday's data: Orthopedics full, its overflow unit Surgery full
   with exactly two discharge-ready patients (the Control Tower's discharge model, P >= 0.7, at 07:00 and two hours
   later). Free and housekeeping beds on both units are filled with planted stays (recent admissions, so not ready
   to leave); when Surgery has fewer than two ready patients, planted stays past their expected length make up the
   number; other patients near the threshold get a pending lab test (an open result keeps them in hospital).
   Discharges and transfers off both units before noon are held until noon, and the two ready patients are
   planned to leave this afternoon (the bed manager's approval brings that forward: app/ehr/storyline.py).
3. Her expected ED visit `ed-j0001` (the ambulance pre-notification): planned, not yet arrived; it starts when the
   clerk confirms the registration (step 1).
4. The plan's horizon moves to cover the stay and the 48-hour follow-up (the rest of the hospital is planned for
   36 hours, so after that only the storyline's events, the ward rounds and housekeeping happen).
"""

from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta

from app.core.models import Patient, Referrer
from app.core.store import Store
from app.ehr import codes as C
from app.ehr.reference import OVERFLOW, UNIT_BY_ID
from app.ehr.seed import materialize as M
from app.ehr.seed import plan_order
from app.ehr.seed.builder import Builder, ext
from app.ehr.seed.materialize import OrderSpec
from app.ehr.seed.simulate import EdVisit, Stay
from app.fhir.dt import fhir_date, fhir_datetime, parse, ref
from app.modules.storyline import script as S

STATE = "storyline"
UNITS = ("ORTH", "SURG")
READY = 0.7  # the Control Tower's "discharge-ready" (snapshot.DISCHARGE_READY)
NEAR = 0.62  # other patients at or above this get a pending result, so the count stays two
HORIZON = timedelta(days=5, hours=6)  # 07:00 on day 0 to 13:00 on day 5: discharge 11:00 on day 3, call 48 h later
HOLD_UNTIL = timedelta(hours=5)  # discharges and transfers off Orthopedics and Surgery wait until noon
READY_LEAVE = (timedelta(hours=8, minutes=30), timedelta(hours=9, minutes=10))  # 15:30 and 16:10 today
FILL_DX = {"ORTH": [("125605004", 4.0), ("609588000", 3.0)], "SURG": [("65275009", 3.0), ("23968004", 6.0)]}
READY_DX = ("74400008", 2.0)  # appendicitis, past its expected two days


def ctx(operator: str | None) -> dict:
    return {"name": "hospital_journey", "correlation_id": uuid.uuid4().hex, "operator": operator}


class _PresetBuilder(Builder):
    """The generator's builders with the preset's own id scope: ids the generator numbers from a counter
    (`cond-000123`) would repeat the day's data, so the preset's are `cond-jp0001` (as the simulator scopes its
    own, SimBuilder)."""

    def next_id(self, prefix: str, width: int = 6) -> str:
        self._counters[prefix] += 1
        return f"{prefix}-jp{self._counters[prefix]:04d}"


def plant(store: Store, operator: str | None, language: str | None = None) -> dict:
    """Write the preset for the patient who speaks `language` (en, zh; none: the default, English); returns the
    storyline's state (also stored as the `storyline` module blob)."""
    who = S.patient(language)
    plan = store.modules["hospital_plan"]
    now = parse(plan["generated_for"])
    seed = plan.get("seed", 0)
    b = _PresetBuilder(store, seed, now)
    story_ctx = ctx(operator)
    birth = S.birth_date(now.date())
    _patient(b, now, birth, who)
    _imaging_records(store, birth, who)
    capacity = _Capacity(store, b, plan, now, random.Random(f"hospital:{seed}:storyline:capacity"), story_ctx).run()
    _expected_visit(plan, now, story_ctx)
    plan["horizon"] = max(parse(plan["horizon"]), now + HORIZON).isoformat()
    plan["events"].sort(key=plan_order)
    store.modules["hospital_plan"] = plan
    state = {"version": 1, "prepared_at": now.isoformat(), "operator": operator, "ctx": story_ctx,
             "patient": S.PATIENT, "mrn": S.MRN, "visit": S.VISIT, "stay": S.STAY, "surgery": S.SURGERY,
             "birth_date": birth.isoformat(), "language": who.language,
             "mpi": {"hospital": f"Patient/{S.PATIENT}", "imaging": S.IMAGING_PATIENT, "matched_on": "health card",
                     "note": "Preset link: master patient index is a roadmap card (8.1); no matching logic"},
             "capacity": capacity, "beats": {}, "marks": {}}
    store.modules[STATE] = state
    return state


# ---------- 1. the patient ----------


def _patient(b: Builder, now: datetime, birth, who: S.Patient) -> None:
    pid = S.PATIENT
    d_given, d_family = who.daughter
    names = [{"use": "official", "family": who.family, "given": [who.given], "text": who.name}]
    if who.native:  # her name in Chinese characters, as she and her family say it
        given_native, family_native = who.native
        names.append({"use": "usual", "family": family_native, "given": [given_native], "text": who.native_name,
                      "extension": [ext("name-language", valueCode=who.code)]})
    b.add({
        "resourceType": "Patient", "id": pid, "active": True,
        "identifier": [
            {"use": "usual", "type": {"text": "Medical record number"}, "system": C.MRN_SYSTEM, "value": S.MRN},
            {"use": "official", "type": {"text": "Ontario health card (synthetic)"}, "system": C.HCN_SYSTEM,
             "value": S.HEALTH_CARD, "extension": [ext("synthetic", valueBoolean=True),
                                                   ext("hcn-version", valueString=S.HEALTH_CARD_VERSION)]}],
        "name": names,
        "gender": "female", "birthDate": fhir_date(birth),
        "address": [{"use": "home", "line": ["88 Demo Cres"], "city": "Markham", "state": "ON",
                     "postalCode": "L3R 5X1", "country": "CA"}],
        "telecom": [{"system": "phone", "value": S.PHONE, "use": "home"}],
        "contact": [{"relationship": [{"text": "daughter"}], "name": {"text": f"{d_given} {d_family}"},
                     "telecom": [{"system": "phone", "value": S.DAUGHTER_PHONE}]}],
        "communication": [{"language": {"coding": [{"system": "urn:ietf:bcp:47", "code": who.code,
                                                    "display": who.display}]}, "preferred": True}],
        "managingOrganization": ref("Organization", "org-demo-hospital")})
    visit = (now - timedelta(days=21)).replace(hour=10, minute=30)
    clinic = b.encounter(pid, "AMB", visit, end=visit + timedelta(minutes=30), service="outpatient-clinic",
                         rid="enc-j0001-clinic")["id"]
    prior = (now - timedelta(days=210)).replace(hour=14, minute=10)
    b.encounter(pid, "IMP", prior, end=prior + timedelta(days=3, hours=2), service="inpatient",
                reason=C.snomed("49436004"), admit_source="emd", disposition="home", rid="enc-j0001-prior")
    for code, years, rid in (("49436004", 6, "cond-j0001-afib"), ("59621000", 15, "cond-j0001-htn"),
                             ("64859006", 4, "cond-j0001-osteo")):
        b.condition(pid, code, (now - timedelta(days=365 * years)).date(), enc=clinic, rid=rid)
    for rxcui, dose, per_day, reason, days in (("11289", 5, 1, "49436004", 2100), ("17767", 5, 1, "59621000", 3000),
                                               ("6918", 25, 2, "49436004", 2100), ("83367", 20, 1, None, 1500)):
        b.medication_statement(pid, rxcui, (now - timedelta(days=days)).date(), enc=clinic, dose=dose,
                               per_day=per_day, reason=reason, rid=f"medst-j0001-{C.MEDICATIONS[rxcui][0]}")
    b.observation(pid, clinic, (C.WEIGHT[0], C.WEIGHT[1]), visit, value=58.0, unit="kg", rid="obs-j0001-weight")
    b.observation(pid, clinic, ("33914-3", C.LABS["33914-3"][0]), visit, value=64, unit="mL/min/{1.73_m2}",
                  category="laboratory", rid="obs-j0001-egfr")
    b.observation(pid, clinic, ("2160-0", C.LABS["2160-0"][0]), visit, value=0.9, unit="mg/dL",
                  category="laboratory", rid="obs-j0001-creatinine")
    b.observation(pid, clinic, ("6301-6", C.LABS["6301-6"][0]), visit, value=2.6, unit="{INR}",
                  category="laboratory", interpretation="H", rid="obs-j0001-inr-clinic")
    for category in ("ai_processing", "followup_call", "sms"):
        b.add({"resourceType": "Consent", "id": f"consent-j0001-{category.replace('_', '-')}", "status": "active",
               "scope": C.concept(C.CONSENT_SCOPE, "patient-privacy"),
               "category": [C.concept(C.CONSENT_CATEGORY, category, category)],
               "patient": ref("Patient", pid), "dateTime": fhir_datetime(now - timedelta(days=400)),
               "provision": {"type": "permit", "purpose": [C.coding(C.CONSENT_CATEGORY, category)]}})


def _imaging_records(store: Store, birth, who: S.Patient) -> None:
    """Her record at the imaging centre (its own patient table) and the ED physician as a referrer there."""
    store.patients[S.IMAGING_PATIENT] = Patient(
        id=S.IMAGING_PATIENT, given_name=who.given, family_name=who.family, dob=birth, sex="F", phone=S.PHONE,
        email=who.email, address="88 Demo Cres, Markham, ON", health_card=S.HEALTH_CARD,
        health_card_version=S.HEALTH_CARD_VERSION, preferred_language=who.language)
    ed_doctor = store.staff.get(S.CAST["ed_physician"])
    store.referrers[S.IMAGING_REFERRER] = Referrer(
        id=S.IMAGING_REFERRER, name=ed_doctor.name if ed_doctor else "Dr. ED Physician",
        specialty="Emergency medicine", clinic="Emergency Department, Lakeside General Hospital (demo)",
        phone="+1-416-555-0150", fax="+1-416-555-0151", is_key=False)


# ---------- 2. Orthopedics and Surgery ----------


class _Capacity:
    def __init__(self, store: Store, b: Builder, plan: dict, now: datetime, rng: random.Random, story_ctx: dict) -> None:
        self.store, self.b, self.plan, self.now, self.rng, self.ctx = store, b, plan, now, rng, story_ctx
        self.n = 100
        self.pool = self._pool()
        self.doctors = self._doctors()
        self.out: dict = {"filled": [], "ready_planted": [], "held": [], "pending_results": [], "warnings": []}

    def run(self) -> dict:
        ready = self._ready(self._snapshots(), "SURG")
        chosen = [enc for enc, _p in ready[:2]]
        for bed in self._free("SURG"):  # fewer than two ready: planted stays past their expected length
            if len(chosen) >= 2:
                break
            chosen.append(self._stay("SURG", bed, *READY_DX, days_in=4.2, ready=True))
            self.out["ready_planted"].append(chosen[-1])
        for unit in UNITS:
            for bed in self._free(unit):
                dx, los = self.rng.choice(FILL_DX[unit])
                self.out["filled"].append(self._stay(unit, bed, dx, los, days_in=self.rng.uniform(0.4, 1.2)))
        for _round in range(4):
            snaps = self._snapshots()
            near = [(enc, p) for unit in UNITS for enc, p in self._ready(snaps, unit, NEAR) if enc not in chosen]
            if not near:
                break
            for enc, _p in near:
                self._pending_result(enc)
        self._hold(chosen)
        snaps = self._snapshots()
        final = self._ready(snaps, "SURG")
        self.out["surgery_ready_values"] = {enc: round(p, 3) for enc, p in final}
        if sorted(e for e, _ in final) != sorted(chosen) or self._ready(snaps, "ORTH")                 or any(self._free(u) for u in UNITS):
            self.out["warnings"].append(f"Surgery ready {final}, chosen {chosen}")
        self.out["surgery_ready"] = chosen
        return self.out

    # ----- reading the hospital as the Control Tower does -----

    def _snapshots(self):
        from app.modules.control_tower.snapshot import build, tower_fhir

        return [build(tower_fhir(), self.now), build(tower_fhir(), self.now + timedelta(hours=2))]

    @staticmethod
    def _ready(snaps: list, unit: str, threshold: float = READY) -> list[tuple[str, float]]:
        """Patients on the unit at or above `threshold` at 07:00 or two hours later, most likely first (for the
        default threshold: at or above it at both times, so "ready" holds until the decision)."""
        values: dict[str, list[float]] = {}
        for snap in snaps:
            for cell in snap.beds.get(unit, []):
                if cell.encounter_id and cell.discharge:
                    values.setdefault(cell.encounter_id, []).append(cell.discharge.value)
        pick = (min if threshold == READY else max)
        out = [(enc, pick(v)) for enc, v in values.items() if pick(v) >= threshold]
        return sorted(out, key=lambda x: -x[1])

    def _free(self, unit: str) -> list[str]:
        return sorted(bed for status in ("U", "K") for bed in self.store.fhir.ids("Location", unit=unit, code="bd",
                                                                                  status=status))

    def _doctors(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for role in sorted(self.store.fhir.search("PractitionerRole"), key=lambda r: r["id"]):
            pid = role["practitioner"]["reference"].split("/")[1]
            unit = ((role.get("location") or [{}])[0].get("reference") or "/").split("/")[1]
            if pid.startswith("prac-doc") and unit:
                out.setdefault(unit, pid)
        return out

    def _pool(self):
        from app.ehr.simulator import patient_info

        busy = {r["patient"] for table in ("visits", "stays", "surgeries") for r in self.plan[table].values()}
        busy |= {e["subject"]["reference"].split("/")[1] for e in self.store.fhir.search(
            "Encounter", status=["arrived", "triaged", "in-progress", "planned", "onleave"])}
        ids = sorted(pid for pid in self.store.fhir.ids("Patient") if pid not in busy and pid != S.PATIENT)
        self.rng.shuffle(ids)
        day = self.now.date()
        for pid in ids:
            p = patient_info(self.store.fhir, pid, day)
            if 45 <= p.age <= 90:
                yield p

    # ----- writing -----

    def _stay(self, unit: str, bed: str, dx: str, los: float, *, days_in: float, ready: bool = False) -> str:
        p = next(self.pool)
        self.n += 1
        sid = f"stay-j{self.n:03d}"
        admit = (self.now - timedelta(days=days_in)).replace(second=0, microsecond=0)
        if ready:
            discharge = self.now + READY_LEAVE[len(self.out["ready_planted"]) % 2]
        else:
            discharge = max(self.now + timedelta(days=1),
                            (admit + timedelta(days=los)).replace(hour=11, minute=self.rng.randint(0, 50)))
        stay = Stay(id=sid, patient=p.id, admit=admit, discharge=discharge, dx=dx, service_unit=unit,
                    beds=[(bed, admit, discharge)], source="ed", expected_los=los,
                    attending=self.doctors.get(unit))
        future = M.write_stay(self.b, stay, p, None, None, self.now)
        loc = self.store.fhir.read("Location", bed)
        loc["operationalStatus"] = C.coding(C.BED_STATUS, "O", "Occupied")
        self.store.fhir.update(loc)
        self.plan["stays"][sid] = {**M.plan_dict(stay), "scenario": self.ctx, "story_fill": True}
        self._event(discharge, "discharge", stay=sid)
        for order in future:
            self.plan["orders"][order.id] = {**M.plan_dict(order), "scenario": self.ctx}
            self._event(order.authored, "order", order=order.id)
            self._event(order.completed, "result", order=order.id)
        return sid

    def _pending_result(self, encounter_id: str) -> None:
        """A lab test sent this morning and not back yet: the patient is not ready to leave before it is."""
        enc = self.store.fhir.read("Encounter", encounter_id)
        k = sum(1 for oid in self.plan["orders"] if oid.startswith(f"ord-{encounter_id}-jh")) + 1
        code = ("2823-3", "718-7", "2160-0")[(k - 1) % 3]
        lo, hi = C.LABS[code][2]
        order = OrderSpec(f"ord-{encounter_id}-jh{k}", enc["subject"]["reference"].split("/")[1], encounter_id, "lab",
                          code, self.now - timedelta(minutes=40 + 10 * k), self.now + timedelta(hours=7),
                          round((lo + hi) / 2, 1))
        M.write_order(self.b, order, self.now)
        self.plan["orders"][order.id] = {**M.plan_dict(order), "scenario": self.ctx}
        self._event(order.completed, "result", order=order.id)
        self.out["pending_results"].append(order.id)

    def _hold(self, chosen: list[str]) -> None:
        noon = self.now + HOLD_UNTIL
        k = 0
        for e in self.plan["events"]:
            if e["kind"] not in ("discharge", "transfer") or parse(e["at"]) >= noon:
                continue
            entry = self.plan["stays"].get(e["stay"]) or {}
            enc = self.store.fhir.read("Encounter", e["stay"])
            if enc is None or enc.get("status") != "in-progress" or e["stay"] in chosen:
                continue
            bed = next((x["location"]["reference"].split("/")[1] for x in enc.get("location") or []
                        if x.get("status") == "active"), "")
            if bed.split("-")[0] not in UNITS:
                continue
            k += 1
            at = noon + timedelta(minutes=7 * k)
            if e["kind"] == "discharge":
                entry["discharge"] = at.isoformat()
                entry["beds"] = [[b_, since, at.isoformat() if i == len(entry["beds"]) - 1 else until]
                                 for i, (b_, since, until) in enumerate(entry.get("beds") or [])]
            e["at"] = at.isoformat()
            self.out["held"].append(e["stay"])
        for i, sid in enumerate(chosen):  # the two ready patients leave this afternoon unless someone rounds early
            at = self.now + READY_LEAVE[i]
            entry = self.plan["stays"].get(sid)
            if entry is None:
                continue
            events = [e for e in self.plan["events"] if e.get("stay") == sid and e["kind"] == "discharge"]
            if events:
                events[0]["at"] = at.isoformat()
            else:
                self._event(at, "discharge", stay=sid)
            entry["discharge"] = at.isoformat()

    def _event(self, at: datetime, kind: str, **refs) -> None:
        if self.now < at <= self.now + HORIZON:
            self.plan["events"].append({"at": at.isoformat(), "kind": kind, **refs})


# ---------- 3. her expected ED visit ----------


def _expected_visit(plan: dict, now: datetime, story_ctx: dict) -> None:
    """The ambulance pre-notified the ED: the visit is in the plan, waiting for the registration (step 1). Its
    times are placeholders until then (accepted.register moves them to the registration)."""
    arrival = now + timedelta(minutes=5)
    visit = EdVisit(id=S.VISIT, patient=S.PATIENT, arrival=arrival, complaint=S.COMPLAINT, ctas=S.SUGGESTED_CTAS,
                    mode="ambulance", vitals=dict(S.TRIAGE_VITALS), triage=arrival + timedelta(minutes=5),
                    seen=arrival + timedelta(minutes=10), decision=arrival + timedelta(minutes=110),
                    end=arrival + timedelta(minutes=130), census=0, docs=0, admit=True, dx=S.DIAGNOSIS, stay=S.STAY)
    plan["visits"][S.VISIT] = {**M.plan_dict(visit), "expected": True, "story": True, "manual_triage": True,
                               "scenario": story_ctx}


def units_full(store: Store) -> dict:
    """For the storyline's status: free beds on the two units now."""
    return {u: len(store.fhir.ids("Location", unit=u, code="bd", status="U")) for u in UNITS} | {
        "overflow": OVERFLOW.get("ORTH"), "names": {u: UNIT_BY_ID[u].name for u in UNITS}}
