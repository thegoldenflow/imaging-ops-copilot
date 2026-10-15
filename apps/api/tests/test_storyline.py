"""The hospital storyline (spec 8.2, WP9): the preset, the people's EHR actions on accepted cards, the call
centre's ED registration, the Control Tower's overflow rounds and OR booking adjustment, and the whole twelve-step
walk through the HTTP API (offline: no Temporal; the journey from the ED is in tests/test_workflows.py)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.core.store import Store, use_store
from app.ehr import accepted, simulator
from app.ehr.events import bus
from app.ehr.gateway import Actor, FhirAccessDenied, FhirConflict, FhirGateway
from app.ehr.seed import journey
from app.fhir.dt import parse
from app.modules.control_tower import rules, triage
from app.modules.control_tower.snapshot import build, tower_fhir
from app.modules.documentation import document as documents
from app.modules.storyline import script as S
from app.modules.storyline import service, stubs
from app.modules.storyline.walk import SUMMARY_CALLS, Walk, WalkFailed, summary_draft_s
from app.modules.voice_services import identity
from tests import storyline_harness as H


@pytest.fixture
def story(fresh_state):
    accepted.install()
    service.install()
    journey.plant(fresh_state, "U-ADMIN")
    return fresh_state


def _ready(snap, unit: str) -> list[str]:
    return [c.encounter_id for c in snap.beds.get(unit, []) if c.discharge and c.discharge.value >= 0.7]


def test_the_preset_plants_the_patient_and_fills_orthopedics_and_surgery(story):
    store = story
    patient = store.fhir.read("Patient", S.PATIENT)
    assert patient["birthDate"] == S.birth_date(parse(store.modules["hospital_plan"]["generated_for"]).date()).isoformat()
    assert {n["text"] for n in patient["name"]} == {"Xiuying Wang", "王秀英"}
    assert patient["communication"][0]["language"]["coding"][0]["code"] == "zh-CN"
    home = {m["medicationCodeableConcept"]["coding"][0]["code"] for m in store.fhir.search("MedicationStatement",
                                                                                          patient=S.PATIENT)}
    assert home == {"11289", "17767", "6918", "83367"}  # warfarin, amlodipine, metoprolol, atorvastatin
    assert {c["provision"]["type"] for c in store.fhir.search("Consent", patient=S.PATIENT)} == {"permit"}
    assert store.patients[S.IMAGING_PATIENT].health_card == S.HEALTH_CARD  # the imaging centre's record of her
    state = store.modules["storyline"]
    assert state["mpi"]["imaging"] == S.IMAGING_PATIENT and not state["capacity"]["warnings"]
    assert store.modules["hospital_plan"]["visits"][S.VISIT]["expected"] is True
    assert store.fhir.read("Encounter", S.VISIT) is None  # she has not arrived
    now = simulator.clock(store)["now"]
    snap = build(tower_fhir(), parse(now))
    units = {u.id: u for u in snap.units}
    assert units["ORTH"].free == units["ORTH"].cleaning == 0 and units["SURG"].free == units["SURG"].cleaning == 0
    assert sorted(_ready(snap, "SURG")) == sorted(state["capacity"]["surgery_ready"]) and not _ready(snap, "ORTH")
    assert parse(store.modules["hospital_plan"]["horizon"]) >= parse(now) + journey.HORIZON
    day0 = parse(now).replace(hour=0, minute=0)  # the follow-up call: 48 h after the discharge at 11:00 on day 3
    assert parse(store.modules["hospital_plan"]["horizon"]) > day0 + timedelta(days=5, hours=11, minutes=6)


def test_the_preset_balances_surgery_on_a_monday(fresh_state):
    """Monday's data has free beds on both units and one discharge-ready Surgery patient: the preset fills the beds
    and plants the second ready patient (a detached generator run, as the hospital's calibration does)."""
    from app.ehr.seed import generate

    store = Store()
    with use_store(store):
        info = generate(store, 7, datetime(2026, 10, 5, 10, 0))
        state = journey.plant(store, "U-ADMIN")
        snap = build(tower_fhir(), info["now"])
    units = {u.id: u for u in snap.units}
    assert units["ORTH"].free == 0 and units["SURG"].free == 0
    assert sorted(_ready(snap, "SURG")) == sorted(state["capacity"]["surgery_ready"]) and len(_ready(snap, "SURG")) == 2
    assert state["capacity"]["filled"] and not state["capacity"]["warnings"]


def test_triage_assist_suggests_ctas_2_and_the_admission_probability():
    s = triage.suggest(age=72, complaint="fall", arrival_mode="ambulance", vitals=dict(S.TRIAGE_VITALS),
                       home_meds=["11289", "17767"], prior_admissions=1)
    assert s["ctas"] == 2 and any("warfarin" in r for r in s["reasons"])
    assert s["admission"]["sentence"] == "Admission probability 0.82: CTAS 2, SpO₂ 91%, heart rate 112"
    calm = triage.suggest(age=40, complaint="injury", arrival_mode="walk-in",
                          vitals={"8867-4": 80, "59408-5": 98, "72514-3": 3}, home_meds=[], prior_admissions=0)
    assert calm["ctas"] == 3


def test_the_callers_relation_is_who_they_are_to_the_patient():
    assert identity.parse("我是她女儿。她叫王秀英").relation == "daughter"
    assert identity.parse("你好，我妈妈在家摔倒了").relation == "child"
    assert identity.parse("I'm calling about my husband").relation == "spouse"
    assert identity.parse("I'm her son, she is Mary Smith").relation == "son"


def test_the_call_centre_proposes_the_registration_and_the_clerk_registers_her(story, client, login):
    store = story
    clerk = login(S.CAST["clerk"])
    lines = client.get("/api/storyline", headers=clerk).json()["patient"]["call_lines"]
    call = client.post("/api/voice/calls", headers=clerk).json()
    replies = [client.post(f"/api/voice/calls/{call['id']}/turn", headers=clerk, json={"text": t}).json()
               for t in lines[:2]]
    assert "急诊登记" in replies[-1]["reply"] and replies[-1]["call"]["language"] == "zh-Hans"
    request = next(r for r in client.get("/api/voice/requests", headers=clerk).json()["requests"]
                   if r["kind"] == "registration")
    assert request["mrn"] == S.MRN and request["args"]["complaint"] == "fall" and request["args"]["mode"] == "ambulance"
    assert store.fhir.read("Encounter", S.VISIT) is None  # nothing is recorded before the clerk confirms
    nurse = login(S.CAST["triage_nurse"])
    assert client.post(f"/api/agents/tasks/{request['task_id']}/decision", headers=nurse,
                       json={"decision": "approve"}).status_code == 403  # the clerk's card
    assert client.post(f"/api/agents/tasks/{request['task_id']}/decision", headers=clerk,
                       json={"decision": "approve", "note": "Registered"}).status_code == 200
    bus.drain()
    visit = store.fhir.read("Encounter", S.VISIT)
    assert visit["class"]["code"] == "EMER" and visit["status"] == "arrived"
    ended = client.post(f"/api/voice/calls/{call['id']}/end", headers=clerk).json()
    assert ended["caller_relation"] == "daughter" and ended["summary"].startswith("Call from the patient's daughter")
    event = next(e for e in bus.events(store, types=["patient.admitted"], limit=20) if e.encounter == S.VISIT)
    assert event.actor == f"user:{S.CAST['clerk']}"  # the EHR records who registered her (EVN-5)
    triage_card = stubs.find_card(store, "triage")  # the next step's card, written as the visit arrived
    assert triage_card is not None and "CTAS 2" in triage_card["description"]


def test_a_walk_in_registration_plans_a_visit(fresh_state):
    store = fresh_state
    plan = store.modules["hospital_plan"]
    busy = {r["patient"] for t in ("visits", "stays", "surgeries") for r in plan[t].values()}
    patient = next(p for p in store.fhir.ids("Patient") if p not in busy and not store.fhir.search(
        "Encounter", patient=p, status=list(simulator.ACTIVE)))
    written = accepted.record(store, {"kind": "registration", "patient_id": patient, "complaint": "chest pain",
                                      "mode": "walk-in"}, operator="U-CLER-01")
    visit = next(r for r in written if r.startswith("Encounter/"))
    enc = store.fhir.read("Encounter", visit.split("/")[1])
    assert enc["status"] == "arrived" and enc["reasonCode"][0]["text"] == "chest pain"
    with pytest.raises(accepted.NotRecorded, match="already registered"):
        accepted.record(store, {"kind": "registration", "patient_id": patient}, operator="U-CLER-01")


def test_only_the_or_coordinator_adjusts_a_booked_case(story):
    store = story
    case = next(c for c in build(tower_fhir(), parse(simulator.clock(store)["now"])).or_cases if c.status == "booked")
    nurse = store.staff[S.CAST["ward_nurse"]]
    with pytest.raises(FhirAccessDenied):
        FhirGateway(Actor.of(nurse), "control_tower").adjust_or_booking(case.appointment_id, 120, reason="x" * 6)
    ops = store.staff[S.CAST["bed_manager"]]
    fhir = FhirGateway(Actor.of(ops), "control_tower")
    stored = fhir.adjust_or_booking(case.appointment_id, case.booked_minutes + 25, reason="predicted longer")
    assert stored.minutesDuration == case.booked_minutes + 25
    assert parse(stored.to_fhir()["end"]) > case.booked_end
    events = store.audit.query(resource_id=case.appointment_id, event_type="write")
    assert any("OR booking" in (e.reason or "") for e in events)
    with pytest.raises(FhirConflict):  # an appointment that is not an OR case
        other = next(a for a in store.fhir.search("Appointment", status="booked")
                     if not any("Location/OR-" in (p.get("actor") or {}).get("reference", "") for p in a["participant"]))
        fhir.adjust_or_booking(other["id"], 30, reason="not an OR case")


def test_block_overtime_counts_only_cases_booked_to_end_by_the_block():
    day = datetime(2026, 10, 9)
    end = day.replace(hour=16)
    assert rules.block_overtime(day.replace(hour=14, minute=30), end, end.replace(minute=25), 25) == 25
    assert rules.block_overtime(day.replace(hour=14, minute=30), end, end.replace(minute=25), 10) == 0  # < 15 min
    assert rules.block_overtime(day.replace(hour=13), day.replace(hour=14), day.replace(hour=14, minute=20), 20) == 0
    assert rules.block_overtime(day.replace(hour=15), end.replace(hour=16, minute=30), end.replace(hour=17), 30) == 0


def test_the_whole_story_offline(story, client):
    """All twelve steps through the HTTP API as the cast, without Temporal: every card decided by its person, every
    EHR record written, every step with its audit events."""
    walk = Walk(client, pump=H.pump, advance=H.advance, timeout_s=60)
    runs = walk.run()
    assert [r.ok for r in runs] == [True] * 12, [(r.key, r.result) for r in runs]
    view = walk.status()
    assert all(s["done"] for s in view["steps"]) and view["current"] is None
    steps = {s["key"]: s for s in view["steps"]}
    for key in ("call", "triage", "imaging", "inr", "bed", "news2", "coding"):
        assert steps[key]["proposal"] and steps[key]["signed"], key
        assert any(a["event_type"] in ("approve", "sign") for a in steps[key]["audit"]), key
    assert steps["bed"]["detail"].startswith("bed SURG-")  # the bed the early discharges freed
    assert steps["or"]["detail"] == "booked 115 min"
    store = story
    summary = store.fhir.read("DocumentReference", f"dsum-{S.STAY}")
    assert summary["docStatus"] == "final"
    # the live walk's wait for this draft counts a call per chunk and the synthesis (summary_draft_s)
    assert len(documents.read_data(summary)["inputs"]["chunks"]) + 1 == SUMMARY_CALLS
    assert store.fhir.read("DocumentReference", f"pinstr-{S.STAY}")["docStatus"] == "final"
    reported = store.fhir.search("Observation", patient=S.PATIENT, category="patient-reported")
    assert reported and store.fhir.search("Task", patient=S.PATIENT, code="followup-escalation")


def test_the_bed_the_early_rounds_free_is_hers_though_someone_elses_admission_comes_due_first(story, client,
                                                                                              monkeypatch):
    """Her admission comes a minute after the freed bed is cleaned. On Thursday 10-15's data a Surgery patient's
    admission from the ED came due in that minute and took the bed, and the walk waited at a beat already past
    (casebook 5.17): the bed is held for her. Here such an admission, preferring her bed, is planted on any day.
    (On Friday's data her own retry finds the other freed bed clean first and she never waits for this one.)"""
    from app.ehr import storyline as ehr_story
    from app.ehr.seed import materialize as M
    from app.ehr.seed import plan_order
    from app.ehr.seed.simulate import Stay

    store, rival = story, "stay-jt001"
    walk = Walk(client, pump=H.pump, advance=H.advance, timeout_s=60)
    walk.run(["call", "triage", "imaging", "inr"])
    plan = store.modules["hospital_plan"]
    busy = {r["patient"] for t in ("visits", "stays", "surgeries") for r in plan[t].values()}
    patient = next(p for p in store.fhir.ids("Patient") if p not in busy and not store.fhir.search(
        "Encounter", patient=p, status=list(simulator.ACTIVE)))
    offer = ehr_story.admit_after_cleaning

    def offer_and_plant_a_rival(store, stay_id, bed):
        offered = offer(store, stay_id, bed)
        if offered:
            plan = store.modules["hospital_plan"]
            at = parse(store.modules["storyline"]["marks"]["bed_offered"]["at"]) - timedelta(seconds=35)
            stay = Stay(id=rival, patient=patient, admit=at, discharge=at + timedelta(days=2), dx="74400008",
                        service_unit="SURG", beds=[(offered, at, at + timedelta(days=2))], source="ed",
                        expected_los=2.0)
            plan["stays"][rival] = M.plan_dict(stay)
            plan["events"].append({"at": at.isoformat(), "kind": "admit", "stay": rival})
            plan["events"].sort(key=plan_order)
            store.modules["hospital_plan"] = plan
        return offered

    monkeypatch.setattr(ehr_story, "admit_after_cleaning", offer_and_plant_a_rival)
    runs = walk.run(["bed"])
    assert runs[-1].key == "bed" and runs[-1].ok
    marks = store.modules["storyline"]["marks"]
    offer_at, offered = parse(marks["bed_offered"]["at"]), f"Location/{marks['bed_offered']['bed']}"
    freed = {store.fhir.read("Encounter", sid)["location"][-1]["location"]["reference"]
             for sid in marks["early_rounds"]["stays"]}
    stay = store.fhir.read("Encounter", S.STAY)
    assert stay["location"][-1]["location"]["reference"] in freed
    if parse(stay["period"]["start"]) >= offer_at:  # she waited for the bed offered to her: it is hers
        assert stay["location"][-1]["location"]["reference"] == offered
        other = store.fhir.read("Encounter", rival)  # admitted to another free bed, or still waiting for one
        assert other is None or other["location"][-1]["location"]["reference"] != offered


def test_the_summary_step_can_wait_for_a_whole_draft_against_a_live_model():
    """A stalled call uses its whole 60 s budget (casebook 3.15), so a draft of five calls can take 300 s: the live
    walk waits that on top of its usual wait for step 9 only."""
    assert summary_draft_s() == SUMMARY_CALLS * 60 == 300
    waits = {}

    def wait(what, check, timeout_s=None):
        waits[what] = timeout_s
        raise WalkFailed("stop here")

    for summary_timeout_s, expected in ((480, 480), (None, None)):  # None: the walk's own timeout_s
        walk = Walk(None, pump=lambda: None, advance=lambda w, b: None, timeout_s=180,
                    summary_timeout_s=summary_timeout_s)
        walk.advance, walk.wait = (lambda beat: None), wait
        with pytest.raises(WalkFailed):
            walk._summary(S.CAST["admin"])
        assert waits == {"the discharge summary draft": expected}


def test_the_journeys_order_review_counts_decisions_the_pharmacist_made_before_it_asked():
    """With Temporal on, the storyline's pharmacist decides the naproxen order while her journey still waits for her
    bed: when the journey's order review runs, every flagged medication order is decided, so it asks no one again."""
    from app.modules.med_safety import journey as med_journey
    from app.modules.med_safety.models import OrderReview

    def review(n: int, status: str, flagged: bool, kind: str = "medication") -> OrderReview:
        return OrderReview(id=f"ORV-{n:05d}", order_ref=f"MedicationRequest/medrx-{n}", kind=kind, patient_id="p",
                           encounter_id=S.STAY, order={}, reviewed_at=datetime(2026, 10, 9, 9), status=status,
                           reviewer_roles=["pharmacist"], findings=[{"severity": "major"}] if flagged else [])

    done = med_journey.decided_before([review(1, "clear", False), review(2, "resolved", True)])
    assert done is not None and not done.wait_for and "already decided" in done.detail
    assert med_journey.decided_before([review(1, "clear", False), review(2, "pending", True)]) is None
    assert med_journey.decided_before([review(1, "clear", False)]) is None  # nothing flagged: the pharmacist confirms
