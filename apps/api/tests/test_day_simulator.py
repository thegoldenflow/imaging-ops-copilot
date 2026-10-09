"""WP3: the day simulator plays the hospital EHR through the planned day (spec 6.2)."""

from collections import Counter
from datetime import datetime, timedelta

import pytest

from app.ehr import scenarios, simulator
from app.ehr.events import bus
from app.ehr.gateway import Actor, FhirGateway
from app.fhir.dt import parse, ref_id
from app.fhir.types import validate


@pytest.fixture
def recorder():
    received = []
    bus.subscribe("*", received.append, consumer="test.recorder")
    yield received
    bus.unsubscribe("test.recorder")


def _now(store) -> datetime:
    return parse(store.modules["hospital_clock"]["now"])


def test_advance_one_hour_writes_fhir_and_publishes_events(fresh_state, recorder):
    start = _now(fresh_state)
    plan = fresh_state.modules["hospital_plan"]
    due = [e for e in plan["events"] if start < parse(e["at"]) <= start + timedelta(hours=1)]
    arrival = next(e for e in due if e["kind"] == "ed_arrival")
    assert fresh_state.fhir.read("Encounter", arrival["visit"]) is None

    result = simulator.advance_by(fresh_state, 60)

    assert _now(fresh_state) == start + timedelta(hours=1)
    assert sum(result.applied.values()) >= len([e for e in due if e["kind"] != "bed_clean"]) // 2
    enc = fresh_state.fhir.read("Encounter", arrival["visit"])
    assert enc["class"]["code"] == "EMER" and enc["status"] in ("arrived", "triaged", "in-progress", "finished")
    assert result.events["patient.admitted"] >= 1
    assert simulator.consistency_problems(fresh_state) == []
    # Subscribers get the events once the transaction is done (here: the shared test store)
    bus.drain()
    assert [e.seq for e in recorder] == sorted(e.seq for e in result.published)
    first = next(e for e in recorder if e.encounter == arrival["visit"])
    assert first.type == "patient.admitted" and first.source == "ADT^A01"
    assert first.refs["patient"].startswith("Patient/pat-") and first.attrs["encounter_class"] == "EMER"
    assert first.actor == "system:demo-ehr" and first.correlation_id and first.event_id


def test_a_day_in_order_with_six_event_types_and_valid_resources(fresh_state, recorder):
    """Spec 6.2: the simulator advances a day, fires at least six kinds of event, and subscribers
    receive them in timeline order."""
    start = _now(fresh_state)
    result = simulator.fast_forward(fresh_state, 8)
    assert _now(fresh_state) == (start + timedelta(days=1)).replace(hour=8)

    kinds = set(result.events)
    assert len(kinds) >= 6
    # Every weekday plan has these; transfers (ICU step-down) and OR bookings depend on the day
    assert {"patient.admitted", "patient.discharged", "encounter.updated", "order.placed", "result.available",
            "bed.status_changed", "appointment.updated"} <= kinds
    assert result.applied["discharge"] > 10 and result.vitals_panels > 100
    # Elective patients come in at 06:00 on the day of their surgery: a Friday's day runs to Saturday 08:00, with no
    # OR list, so it admits from the ED only (7 on the seeded Friday)
    assert result.applied["admit"] > (10 if (start + timedelta(days=1)).weekday() < 5 else 5)

    bus.drain()
    assert len(recorder) == sum(result.events.values())
    times = [e.occurred_at for e in recorder]
    assert times == sorted(times), "subscribers see the timeline in order"
    # Per encounter the lifecycle is in order: admitted first, discharged last
    by_encounter: dict[str, list[str]] = {}
    for e in recorder:
        if e.encounter and e.type.startswith("patient."):
            by_encounter.setdefault(e.encounter, []).append(e.type)
    for types in by_encounter.values():
        if "patient.admitted" in types:
            assert types[0] == "patient.admitted"
        if "patient.discharged" in types:
            assert types[-1] == "patient.discharged"

    assert simulator.consistency_problems(fresh_state) == []
    for rtype, rid in sorted(result.written):
        validate(fresh_state.fhir.read(rtype, rid))


def test_every_admission_decided_in_the_plan_has_its_stay_whatever_the_time_of_seeding():
    # The plan covers 36 hours from 07:00 on the day of seeding. On a Tuesday the wards are full: ED patients decided
    # for admission that evening wait for a bed until after the plan ends, and the decision's bed request needs the
    # stay. Seed as of every 6 hours over 3 days from a Tuesday; the generator reads only the hospital day, so one
    # seeding per day stands for the others.
    from app.core.config import settings
    from app.core.store import Store, use_store
    from app.ehr.seed import generate, hospital_day_start

    times = [datetime(2026, 10, 13) + timedelta(hours=h) for h in range(0, 72, 6)]
    seedings = {hospital_day_start(t): t for t in times}
    assert [d.strftime("%a") for d in sorted(seedings)] == ["Tue", "Wed", "Thu"]
    for wall in seedings.values():
        s = Store()
        with use_store(s):
            generate(s, settings.seed, wall)
        plan = s.modules["hospital_plan"]
        decided = [plan["visits"][e["visit"]] for e in plan["events"] if e["kind"] == "ed_decision"]
        missing = [v["id"] for v in decided if v["admit"] and v["stay"] not in plan["stays"]]
        assert missing == [], wall


def test_discharge_frees_the_bed_through_housekeeping(fresh_state):
    plan = fresh_state.modules["hospital_plan"]
    start = _now(fresh_state)
    d = next(e for e in plan["events"] if e["kind"] == "discharge" and parse(e["at"]) > start)
    stay = d["stay"]
    bed = ref_id(fresh_state.fhir.read("Encounter", stay)["location"][-1]["location"])
    simulator.advance(fresh_state, parse(d["at"]))
    enc = fresh_state.fhir.read("Encounter", stay)
    assert enc["status"] == "finished" and enc["hospitalization"]["dischargeDisposition"]
    assert all(loc["status"] == "completed" for loc in enc["location"])
    assert fresh_state.fhir.read("Location", bed)["operationalStatus"]["code"] == "K"
    assert not fresh_state.fhir.search("MedicationRequest", encounter=stay, status="active")
    simulator.advance_by(fresh_state, 180)
    assert fresh_state.fhir.read("Location", bed)["operationalStatus"]["code"] in ("U", "O")  # cleaned (maybe reused)


def test_planned_transfer_moves_the_patient_and_leaves_the_old_bed_dirty(fresh_state, recorder):
    start = _now(fresh_state)
    plan = fresh_state.modules["hospital_plan"]
    stay = next(e for e in fresh_state.fhir.search("Encounter", cls="IMP", status="in-progress")
                if e["id"] in plan["stays"] and ref_id(e["location"][-1]["location"]).startswith("MEDA"))
    old_bed = ref_id(stay["location"][-1]["location"])
    # a free bed anywhere: which units have one at 07:00 depends on the day of seeding (Surgery is full on a Friday)
    target = next(b for unit in ("SURG", "MEDB", "ORTH", "MEDA", "ICU")
                  for b in fresh_state.fhir.ids("Location", unit=unit, code="bd", status="U"))
    at = start + timedelta(minutes=10)
    plan["events"].append({"at": at.isoformat(), "kind": "transfer", "stay": stay["id"], "bed": target})
    plan["events"].sort(key=lambda e: e["at"])
    fresh_state.modules["hospital_plan"] = plan

    simulator.advance(fresh_state, at)
    enc = fresh_state.fhir.read("Encounter", stay["id"])
    assert ref_id(enc["location"][-1]["location"]) == target and enc["location"][-2]["status"] == "completed"
    assert fresh_state.fhir.read("Location", target)["operationalStatus"]["code"] == "O"
    assert fresh_state.fhir.read("Location", old_bed)["operationalStatus"]["code"] == "K"
    bus.drain()
    moved = [e for e in recorder if e.type == "patient.transferred"]
    assert moved[-1].refs["from_location"] == f"Location/{old_bed}" and moved[-1].refs["location"] == f"Location/{target}"
    simulator.advance_by(fresh_state, 120)  # housekeeping comes for the old bed even though no cleaning was planned
    assert fresh_state.fhir.read("Location", old_bed)["operationalStatus"]["code"] in ("U", "O")
    assert simulator.consistency_problems(fresh_state) == []


def test_a_taken_bed_sends_the_admission_elsewhere(fresh_state):
    """A bed manager moved someone into the bed a planned admission was going to get."""
    plan = fresh_state.modules["hospital_plan"]
    start = _now(fresh_state)
    admit = next(e for e in plan["events"] if e["kind"] == "admit" and parse(e["at"]) > start
                 and plan["stays"][e["stay"]]["beds"][0][0].startswith(("MEDA", "MEDB", "SURG")))
    stay = plan["stays"][admit["stay"]]
    planned_bed = stay["beds"][0][0]
    simulator.advance(fresh_state, parse(admit["at"]) - timedelta(seconds=1))
    if fresh_state.fhir.read("Location", planned_bed)["operationalStatus"]["code"] != "U":
        pytest.skip("planned bed not free just before the admission")
    mover = next(e for e in fresh_state.fhir.search("Encounter", cls="IMP", status="in-progress")
                 if e["id"] != stay["id"])
    ops = next(u for u in fresh_state.staff.values() if u.role == "operations_manager")
    FhirGateway(Actor.of(ops), module="control_tower").append_encounter_location(mover["id"], planned_bed)

    result = simulator.advance(fresh_state, parse(admit["at"]) + timedelta(minutes=1))
    enc = fresh_state.fhir.read("Encounter", stay["id"])
    if enc is not None:
        assert ref_id(enc["location"][0]["location"]) != planned_bed
    else:
        assert result.deferred >= 1  # no bed anywhere: still boarding in the ED
    assert simulator.consistency_problems(fresh_state) == []
    simulator.advance_by(fresh_state, 240)  # the old bed the mover left gets cleaned too
    assert simulator.consistency_problems(fresh_state) == []


def test_icu_surge_boards_patients_when_icu_is_full(fresh_state, recorder):
    added = scenarios.inject(fresh_state, "icu_surge", operator="U-OPS")
    assert len(added["patients"]) == max(3, added["icu_free_beds_now"] + 1)  # fills ICU plus one
    result = simulator.advance_by(fresh_state, 150)
    boarded = 0
    for p in added["patients"]:
        ed = fresh_state.fhir.read("Encounter", p["visit"])
        assert ed is not None and ed["class"]["code"] == "EMER"
        stay = fresh_state.fhir.read("Encounter", p["stay"])
        if stay is not None:  # got a bed: ICU first
            assert ref_id(stay["location"][0]["location"]).startswith("ICU-")
            assert ed["status"] == "finished"
        else:  # boarding: the ED visit is still open and the bed request active
            boarded += 1
            assert ed["status"] in ("in-progress", "triaged")
            assert fresh_state.fhir.read("ServiceRequest", f"bedreq-{p['visit']}")["status"] == "active"
    bus.drain()
    icu_vacated = [e for e in recorder if e.type in ("patient.discharged", "patient.transferred")
                   and str(e.refs.get("from_location", "")).startswith("Location/ICU-")]
    if not icu_vacated:
        assert boarded >= 1 and result.deferred >= boarded
    assert simulator.consistency_problems(fresh_state) == []
    injected = [e for e in recorder if e.correlation_id == added["correlation_id"]]
    assert injected and all(e.actor == "user:U-OPS" for e in injected)
    assert {"patient.admitted", "encounter.updated"} <= {e.type for e in injected}
    small = scenarios.inject(fresh_state, "icu_surge", operator="U-OPS", count=1)
    assert len(small["patients"]) == 1


def test_ed_surge_books_an_emergency_case(fresh_state, recorder):
    added = scenarios.inject(fresh_state, "ed_surge", operator="U-OPS")
    surgery = next(p["surgery"] for p in added["patients"] if "surgery" in p)
    booked_at = parse(fresh_state.modules["hospital_plan"]["surgeries"][surgery]["booked_at"])
    simulator.advance(fresh_state, booked_at + timedelta(minutes=1))
    bus.drain()
    scheduled = [e for e in recorder if e.type == "appointment.scheduled"]
    assert scheduled and scheduled[-1].source == "SIU^S12"
    appointment = fresh_state.fhir.read("Appointment", ref_id(scheduled[-1].refs["appointment"]))
    assert appointment["status"] == "booked" and appointment["participant"][1]["actor"]["reference"] == "Location/OR-04"
    assert simulator.consistency_problems(fresh_state) == []


def test_or_overrun_pushes_the_room_list(fresh_state):
    plan = fresh_state.modules["hospital_plan"]
    before = {sid: dict(s) for sid, s in plan["surgeries"].items()}
    added = scenarios.inject(fresh_state, "or_overrun", operator="U-OPS")
    case = plan["surgeries"][added["case"]]
    assert parse(case["end"]) - parse(before[added["case"]]["end"]) == timedelta(minutes=50)
    for sid in added["pushed"]:
        assert parse(plan["surgeries"][sid]["start"]) - parse(before[sid]["start"]) == timedelta(minutes=50)
    end_event = next(e for e in plan["events"] if e["kind"] == "surgery_end" and e.get("surgery") == added["case"])
    assert end_event["at"] == case["end"]


def test_consent_revoked_is_a_platform_event(fresh_state, recorder):
    added = scenarios.inject(fresh_state, "consent_revoked", operator="U-OPS")
    simulator.advance_by(fresh_state, 5)
    assert fresh_state.fhir.read("Consent", added["consent"])["provision"]["type"] == "deny"
    bus.drain()
    revoked = [e for e in recorder if e.type == "consent.revoked"]
    assert len(revoked) == 1 and revoked[0].source == "platform" and revoked[0].attrs["category"] == "sms"
    assert revoked[0].patient == added["patient"]


def test_running_clock_ticks_at_the_demo_rate(fresh_state):
    start = _now(fresh_state)
    wall = datetime(2030, 1, 1, 12, 0, 0)
    simulator.run(fresh_state, 60, wall_now=wall)
    simulator.tick(fresh_state, wall + timedelta(seconds=30))
    assert _now(fresh_state) == start + timedelta(minutes=30)
    simulator.tick(fresh_state, wall + timedelta(hours=2))  # a stalled loop never jumps more than MAX_TICK
    assert _now(fresh_state) == start + timedelta(minutes=30) + simulator.MAX_TICK
    simulator.pause(fresh_state)
    assert simulator.tick(fresh_state, wall + timedelta(hours=3)) is None


def test_the_clock_stops_at_the_end_of_the_plan(fresh_state):
    horizon = parse(fresh_state.modules["hospital_plan"]["horizon"])
    simulator.run(fresh_state, 60)
    simulator.advance(fresh_state, horizon + timedelta(hours=5))
    state = simulator.status(fresh_state)
    assert state["now"] == horizon and not state["running"] and "end of the planned day" in state["stopped"]
    assert state["remaining"] == 0
    with pytest.raises(ValueError):
        simulator.run(fresh_state, 60)
    with pytest.raises(scenarios.ScenarioError):
        scenarios.inject(fresh_state, "icu_surge", operator="U-OPS")


def test_simulator_api_roles_and_audit(client, login, fresh_state):
    ops, fd = login("U-OPS"), login("U-FD")
    assert client.get("/api/hospital/simulator", headers=fd).status_code == 403
    state = client.get("/api/hospital/simulator", headers=ops).json()
    assert state["upcoming"] and "icu_surge" in state["scenarios"]
    r = client.post("/api/hospital/simulator/advance", json={"minutes": 30}, headers=ops)
    assert r.status_code == 200 and r.json()["events"]
    assert client.post("/api/hospital/simulator/advance", json={}, headers=ops).status_code == 422
    r = client.post("/api/hospital/simulator/inject", json={"scenario": "icu_surge"}, headers=ops)
    assert r.status_code == 200 and len(r.json()["patients"]) >= 3
    assert client.post("/api/hospital/simulator/inject", json={"scenario": "nope"}, headers=ops).status_code == 422
    log = client.get("/api/hospital/events?limit=5", headers=ops).json()
    assert len(log["events"]) == 5 and log["events"][0]["seq"] > log["events"][-1]["seq"]
    assert sum(log["counts"].values()) >= 5 and set(log["counts"]) <= set(log["types"])
    assert client.get("/api/hospital/events?type=bogus", headers=ops).status_code == 422
    assert client.post("/api/hospital/simulator/run", json={"rate": 120}, headers=ops).json()["running"]
    assert not client.post("/api/hospital/simulator/pause", headers=ops).json()["running"]
    actions = Counter(e.reason.split(":")[0] for e in fresh_state.audit.events() if e.action == "simulator_event")
    assert actions["advance"] == 1 and actions["inject"] == 1 and actions["run"] == 1 and actions["pause"] == 1

