"""The storyline's EHR script (spec 8.2 "simulator script", WP9): what the simulated EHR and the rest of the
hospital do in the twelve steps.

The preset (app/ehr/seed/journey.py) plants the patient and the hospital at 07:00. This module plans her course in
the day simulator's plan as the story moves, so the story runs on the same event stream as the rest of the
hospital (ADT, ORM, ORU, SIU -> domain events the modules subscribe to). People's own EHR actions (registration,
triage, imaging orders) are app/ehr/accepted.py; this is everything around them:

| trigger | the EHR (planned or applied now) | for step |
| --- | --- | --- |
| the triage is recorded (ADT^A08) | the ED physician sees her now (bay); INR and blood count ordered at +4 min (INR 3.8 at +45 min); the decision to admit at +95 min (bed request for Orthopedics); the stay: admission when a bed is free, admission orders as planned (naproxen new, warfarin held, home amlodipine left out), the OR case booked 10 min after the decision for 14:30 in a free room (90 min booked, 115 min in fact), spot vital signs at 02:00 with NEWS2 7, discharge prescriptions the next afternoon but one, discharge at 11:00 on post-operative day 3 | 3-11 |
| the bed manager approves the early rounds on Surgery (exception.decided) | the Surgery team discharges its two discharge-ready patients now + 20 and + 35 min | 5 |
| one of them leaves (ADT^A03) | her admission is retried one minute after that bed's housekeeping, with that bed held for her | 5 |
| she is admitted (ADT^A01 IMP) | the admitting note on her hypertension (the planted source of step 9's untraceable medicine) | 9 |

Everything is idempotent (marks in the storyline's state, plan entries keyed by id), since bus handlers may run
again. Her events after the admission are `fixed` in time: while she boards, the simulator's retries move only the
retries (spec 8.2's times: the first night at 02:00, the discharge at 11:00). The times are the story's choices; they compress nothing except the 48-hour follow-up, which the journey's
timer (WORKFLOW_FOLLOWUP_DELAY_S) or the storyline's "advance 48 hours" covers.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.core.store import Store
from app.ehr import codes as C
from app.ehr import simulator
from app.ehr.reference import OR_ROOMS, TURNOVER_MIN
from app.ehr.seed import materialize as M
from app.ehr.seed import plan_order
from app.ehr.seed.materialize import OrderSpec
from app.ehr.seed.simulate import Stay, Surgery
from app.fhir.dt import fhir_datetime, parse, ref
from app.modules.storyline import script as S

STATE = "storyline"
SEEN_AFTER_TRIAGE = timedelta(minutes=0)
LABS_AFTER_SEEN = timedelta(minutes=4)
INR_AFTER_ORDER = timedelta(minutes=41)
CBC_AFTER_ORDER = timedelta(minutes=33)
DECISION_AFTER_SEEN = timedelta(minutes=95)
ADMIT_AFTER_DECISION = timedelta(minutes=15)
BOOKED_AFTER_DECISION = timedelta(minutes=10)
OR_START = (14, 30)
BOOKED_MINUTES = 90
ACTUAL_MINUTES = 115  # what the case takes (the model predicts about the same)
ROUNDS = (timedelta(minutes=20), timedelta(minutes=35))  # the early discharges after the approval
MED_PLAN = [  # her admission orders (authored at the admission: the journey's order review reads them at once)
    {"rxcui": "161", "hours": 0, "dose": 1000, "per_day": 4, "reason": S.DIAGNOSIS},
    {"rxcui": "3423", "hours": 0, "dose": 1, "per_day": 4, "prn": True, "reason": S.DIAGNOSIS},
    {"rxcui": "7258", "hours": 0, "dose": 500, "per_day": 2, "note": "For hip pain."},
    {"rxcui": "6918", "hours": 0, "dose": 25, "per_day": 2},
    {"rxcui": "83367", "hours": 0, "dose": 20, "per_day": 1},
]  # warfarin held (INR 3.8 before surgery); home amlodipine not ordered (the omission MedRec finds)
DISCHARGE_RX = [
    {"rxcui": "11289", "dose": 3, "per_day": 1, "reason": "49436004",
     "note": "Restart at 3 mg daily; INR at the anticoagulation clinic in 3 days."},
    {"rxcui": "67108", "dose": 40, "per_day": 1, "reason": S.DIAGNOSIS,
     "note": "Clot prevention after hip surgery until the INR is in range."},
    {"rxcui": "161", "dose": 1000, "per_day": 4, "prn": True, "reason": S.DIAGNOSIS},
    {"rxcui": "17767", "dose": 5, "per_day": 1, "reason": "59621000"},
    {"rxcui": "6918", "dose": 25, "per_day": 2, "reason": "49436004"},
    {"rxcui": "83367", "dose": 20, "per_day": 1},
]  # naproxen stopped
HYPERTENSION_NOTE = ("Blood pressure controlled on amlodipine at home. Lisinopril was discussed with the patient and "
                     "her daughter as an alternative but not started.")


def state(store: Store) -> dict | None:
    return store.modules.get(STATE)


def _save(store: Store, value: dict) -> None:
    store.modules[STATE] = value


def _event(plan: dict, at: datetime, kind: str, **refs) -> None:
    plan["events"].append({"at": at.isoformat(), "kind": kind, **refs})


# ---------- after the triage ----------


def after_triage(store: Store) -> dict | None:
    """The ED course after the triage nurse's acceptance (idempotent: once per story)."""
    st = state(store)
    if st is None or st.get("marks", {}).get("course_planned"):
        return None
    plan = store.modules["hospital_plan"]
    entry = plan["visits"].get(S.VISIT)
    enc = store.fhir.read("Encounter", S.VISIT)
    if entry is None or enc is None or enc.get("status") != "triaged":
        return None
    now = parse(simulator.clock(store)["now"])
    seen = now + SEEN_AFTER_TRIAGE
    decision = seen + DECISION_AFTER_SEEN
    entry.update(seen=seen.isoformat(), decision=decision.isoformat(), bed_request_at=decision.isoformat(),
                 end=(decision + ADMIT_AFTER_DECISION).isoformat())
    ctx = st["ctx"]
    # the ED physician's lab orders (INR before surgery; a blood count)
    labs = [OrderSpec(f"ord-{S.VISIT}-inr", S.PATIENT, S.VISIT, "lab", "6301-6", seen + LABS_AFTER_SEEN,
                      seen + LABS_AFTER_SEEN + INR_AFTER_ORDER, S.INR_CRITICAL, requester=_practitioner(store, "ed_physician")),
            OrderSpec(f"ord-{S.VISIT}-cbc", S.PATIENT, S.VISIT, "lab", "718-7", seen + LABS_AFTER_SEEN,
                      seen + LABS_AFTER_SEEN + CBC_AFTER_ORDER, 11.4, requester=_practitioner(store, "ed_physician"))]
    for order in labs:
        plan["orders"][order.id] = {**M.plan_dict(order), "scenario": ctx}
        _event(plan, order.authored, "order", order=order.id)
        _event(plan, order.completed, "result", order=order.id)
    _event(plan, decision, "ed_decision", visit=S.VISIT)
    # the stay and its course
    day0 = parse(plan["generated_for"]).replace(hour=0, minute=0)
    admit = decision + ADMIT_AFTER_DECISION
    discharge = (day0 + timedelta(days=3)).replace(hour=11, minute=0)
    stay = Stay(id=S.STAY, patient=S.PATIENT, admit=admit, discharge=discharge, dx=S.DIAGNOSIS, service_unit="ORTH",
                beds=[], source="ed", expected_los=7.0, ed_visit=S.VISIT, surgery=S.SURGERY, attending=S.SURGEON,
                med_plan=[dict(m) for m in MED_PLAN])
    plan["stays"][S.STAY] = {**M.plan_dict(stay), "first_unit": "ORTH", "bed_units": ["ORTH", "SURG"],
                             "scenario": ctx, "story": True}
    _event(plan, admit, "admit", stay=S.STAY)
    _event(plan, discharge, "discharge", stay=S.STAY, fixed=True)
    start = day0.replace(hour=OR_START[0], minute=OR_START[1])
    room = _room(plan, start, timedelta(minutes=BOOKED_MINUTES))
    surgery = Surgery(id=S.SURGERY, patient=S.PATIENT, code=S.PROCEDURE, surgeon=S.SURGEON, room=room,
                      booked_start=start, booked_minutes=BOOKED_MINUTES, start=start,
                      end=start + timedelta(minutes=ACTUAL_MINUTES), asa=2, urgency="urgent",
                      booked_at=decision + BOOKED_AFTER_DECISION, stay=S.STAY)
    plan["surgeries"][S.SURGERY] = {**M.plan_dict(surgery), "scenario": ctx, "story": True}
    _event(plan, surgery.booked_at, "surgery_booked", surgery=S.SURGERY)
    _event(plan, surgery.start, "surgery_start", surgery=S.SURGERY)
    _event(plan, surgery.end, "surgery_end", surgery=S.SURGERY)
    night = (day0 + timedelta(days=1)).replace(hour=2, minute=0)
    _event(plan, night, "vitals", stay=S.STAY, tag="night1", values=dict(S.NIGHT_VITALS), news2=S.NEWS2_SCORE,
           fixed=True)
    _event(plan, (day0 + timedelta(days=2)).replace(hour=17, minute=0), "prescriptions", stay=S.STAY,
           meds=[dict(m) for m in DISCHARGE_RX], fixed=True)
    plan["events"].sort(key=plan_order)
    store.modules["hospital_plan"] = plan
    # the ED physician sees her now (CTAS 2: at once)
    simulator.apply_now(store, {"kind": "ed_seen", "visit": S.VISIT}, operator=S.CAST["ed_physician"],
                        correlation_id=ctx["correlation_id"])
    st = state(store)
    st.setdefault("marks", {})["course_planned"] = now.isoformat()
    st["beats"] = {"inr": (labs[0].completed + timedelta(minutes=1)).isoformat(),
                   "decision": (decision + timedelta(minutes=2)).isoformat(),
                   "night": (night + timedelta(minutes=1)).isoformat(),
                   "discharge": (discharge + timedelta(minutes=1)).isoformat(),
                   "followup": (discharge + timedelta(hours=48, minutes=6)).isoformat()}
    st["surgery_room"] = room
    _save(store, st)
    return st


def _practitioner(store: Store, cast: str) -> str | None:
    user = store.staff.get(S.CAST[cast])
    return user.practitioner_id if user else None


def _room(plan: dict, start: datetime, length: timedelta) -> str:
    """An operating room free for the case (14:30-16:00 today), the emergency room first; else the room whose
    list ends first (the case then starts after it)."""
    turnover = timedelta(minutes=TURNOVER_MIN)
    cases: dict[str, list[tuple[datetime, datetime]]] = {r: [] for r in OR_ROOMS}
    for s in plan["surgeries"].values():
        if s["status"] != "cancelled" and s["room"] in cases and s["booked_start"][:10] == start.date().isoformat():
            cases[s["room"]].append((parse(s["booked_start"]), parse(s["booked_start"]) +
                                     timedelta(minutes=s["booked_minutes"])))
    for room in ("OR-04", "OR-03", "OR-02", "OR-01"):
        if all(b + turnover <= start or a >= start + length + turnover for a, b in cases.get(room, [])):
            return room
    return min(OR_ROOMS, key=lambda r: max((b for _a, b in cases[r]), default=start))


# ---------- step 5: the Surgery team's answer to the approval ----------


def early_rounds(store: Store, approved_at: datetime) -> list[str]:
    """The bed manager approved the early discharge rounds on Surgery while she waits for a bed: the Surgery
    team (played by the simulator) discharges its two discharge-ready patients now + 20 and + 35 minutes."""
    st = state(store)
    if st is None or st.get("marks", {}).get("early_rounds"):
        return []
    if store.fhir.read("Encounter", S.STAY) is not None:
        return []  # she already has a bed
    plan = store.modules["hospital_plan"]
    moved = []
    for i, sid in enumerate((st.get("capacity") or {}).get("surgery_ready") or []):
        enc = store.fhir.read("Encounter", sid)
        entry = plan["stays"].get(sid)
        if enc is None or enc.get("status") != "in-progress" or entry is None:
            continue
        at = approved_at + ROUNDS[min(i, len(ROUNDS) - 1)]
        for e in plan["events"]:
            if e.get("stay") == sid and e["kind"] == "discharge" and parse(e["at"]) > at:
                e["at"] = at.isoformat()
        entry["discharge"] = at.isoformat()
        moved.append(sid)
    plan["events"].sort(key=plan_order)
    store.modules["hospital_plan"] = plan
    st.setdefault("marks", {})["early_rounds"] = {"at": approved_at.isoformat(), "stays": moved}
    _save(store, st)
    return moved


def admit_after_cleaning(store: Store, stay_id: str, bed: str | None) -> str | None:
    """One of the two left: her admission is retried a minute after that bed is cleaned, with the bed held for her
    (the bed manager gives the first freed overflow bed to the patient who has waited longest): an admission or
    transfer of someone else that comes due in that minute takes another bed or waits (casebook 5.17)."""
    st = state(store)
    marks = (st or {}).get("marks", {})
    if st is None or stay_id not in (marks.get("early_rounds") or {}).get("stays", []) or marks.get("bed_offered"):
        return None
    if not bed or store.fhir.read("Encounter", S.STAY) is not None:
        return None
    plan = store.modules["hospital_plan"]
    now = parse(simulator.clock(store)["now"])
    clean = next((parse(e["at"]) for e in plan["events"] if e["kind"] == "bed_clean" and e.get("bed") == bed
                  and parse(e["at"]) >= now), None)
    if clean is None:
        return None
    at = clean + timedelta(minutes=1)
    entry = plan["stays"].get(S.STAY)
    if entry is None:
        return None
    entry["beds"] = [[bed, at.isoformat(), entry["discharge"]]]
    plan.setdefault("bed_holds", {})[bed] = {"stay": S.STAY, "until": at.isoformat()}
    _event(plan, at, "admit", stay=S.STAY, fixed=True)  # not moved by the retries still waiting for a bed
    plan["events"].sort(key=plan_order)
    store.modules["hospital_plan"] = plan
    marks["bed_offered"] = {"bed": bed, "at": at.isoformat(), "freed_by": stay_id}
    st["beats"]["admitted"] = (at + timedelta(minutes=1)).isoformat()
    _save(store, st)
    return bed


# ---------- the admission ----------


def admission_note(store: Store) -> str | None:
    """The admitting physician's problem note on her hypertension (the EHR's record; step 9's discharge summary
    draft picks the lisinopril up from it, and the validator finds no order behind it)."""
    rid = "cond-j0001-htn-note"
    stay = store.fhir.read("Encounter", S.STAY)
    if stay is None or store.fhir.read("Condition", rid) is not None:
        return None
    at = parse(stay["period"]["start"]) + timedelta(minutes=20)
    store.fhir.create({
        "resourceType": "Condition", "id": rid,
        "clinicalStatus": C.concept(C.CONDITION_CLINICAL, "active"),
        "category": [C.concept(C.CONDITION_CATEGORY, "encounter-diagnosis")],
        "code": C.snomed("59621000"), "subject": ref("Patient", S.PATIENT), "encounter": ref("Encounter", S.STAY),
        "onsetDateTime": fhir_datetime(at), "recordedDate": fhir_datetime(at),
        "note": [{"text": HYPERTENSION_NOTE}]})
    return rid
