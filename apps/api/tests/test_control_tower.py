"""WP5 (spec 7.1): the hospital flow Control Tower.

Boards from FHIR through the gateway, the four flow models, the rule engine (with
its 20-scenario eval), the exception stream, the narrator agent and its guards, the
action drawer (approve -> Task for the owner role -> audit; reject; defer), the
drill-down patient card per role, the background fast-forward and the board
following the simulator. Acts for the seeded staff: the bed manager (U-OPS), the
Medicine A nurse (U-NURS-05) and physician (U-DOC-09).
"""

import json
import time
from datetime import timedelta

import numpy as np
import pytest

from app.agents import approvals
from app.agents import trace as traces
from app.agents.runtime import lineage
from app.ehr import scenarios, simjobs, simulator
from app.ehr.clock import hospital_now
from app.ehr.gateway import Actor, FhirAccessDenied, FhirGateway
from app.llm.providers import MOCK_FIXTURES
from app.modules.control_tower import agent, evals, rules, service, snapshot
from app.modules.control_tower import exceptions as X
from app.modules.control_tower.flowmodels import MODELS_DIR, NAMES, models

OPS, NURSE, PHYSICIAN = "U-OPS", "U-NURS-05", "U-DOC-09"


@pytest.fixture(autouse=True)
def _fresh_tower():
    service.reset_state()  # the snapshot cache is per process; each test has its own rolled-back data
    yield
    service.reset_state()


def _board(client, login, user=OPS):
    r = client.get("/api/control-tower/board", headers=login(user))
    assert r.status_code == 200, r.text
    return r.json()


def _detected(store) -> list:
    return rules.evaluate(service.refresh(store))


# ---------- FhirGateway additions ----------


def test_hospital_wide_search_is_for_hospital_scope_actors_and_audited_once(fresh_state):
    store = fresh_state
    tower = snapshot.tower_fhir()
    before = len(store.audit.query(module="control_tower"))
    with tower.batch("test-board"):
        units = tower.search("Location", code="wa")
        stays = tower.search("Encounter", cls="IMP", status="in-progress")
    assert {u["id"] for u in units} >= {"MEDA", "MEDB", "SURG", "ORTH", "ICU"} and stays
    events = store.audit.query(module="control_tower")[before:]
    assert len(events) == 1 and events[0].resource_type == "Bundle" and "2 reads" in events[0].reason
    nurse = store.staff[NURSE]
    with pytest.raises(FhirAccessDenied):
        FhirGateway(Actor.of(nurse), "control_tower").search("Encounter", status="in-progress")
    ops = FhirGateway(Actor.of(store.staff[OPS]), "control_tower")
    patient = ops.search("Patient", limit=1)[0]
    assert set(patient) <= {"resourceType", "id", "active", "identifier"}  # the bed manager: MRN only


def test_resolve_refs_finds_only_existing_resources(fresh_state):
    tower = snapshot.tower_fhir()
    stay = tower.search("Encounter", cls="IMP", status="in-progress", limit=1)[0]
    got = tower.resolve_refs([f"Encounter/{stay['id']}", "Encounter/stay-99999", "Location/MEDA", "Nonsense/x",
                              "Location/MEDA"])
    assert got == {f"Encounter/{stay['id']}", "Location/MEDA"}


# ---------- the flow models ----------


def test_committed_models_meet_their_gates_and_explanations_add_up():
    report = json.loads((MODELS_DIR / "report.json").read_text(encoding="utf-8"))
    assert report["passed"] and set(report["models"]) == set(NAMES)
    for name, m in report["models"].items():
        assert m["passed"], name
        if m["metric"] == "auroc":
            assert m["model"] >= m["baseline"] + 0.05, name
        else:
            assert m["model"] <= 0.9 * m["baseline"], name
        assert m["n_test"] > 0 and m["test_dates"][0] > m["train_dates"][1], name  # a time split
    flow = models()
    for name in NAMES:
        bundle, explainer = flow.bundle(name)
        rng = np.random.default_rng(0)
        X_ = rng.normal(size=(5, len(bundle["features"]))) * 3 + 5
        raw = explainer.bias + explainer.contributions(X_).sum(axis=1)
        model = bundle["model"]
        expected = model.decision_function(X_) if bundle["kind"] == "classifier" else model.predict(X_)
        assert np.allclose(raw, expected, atol=1e-6), name


def test_training_data_comes_from_fhir_and_the_duration_model_beats_its_baseline(fresh_state):
    from app.modules.control_tower import flowdata, flowmodels

    tables = flowdata.build(snapshot.tower_fhir(), hospital_now(fresh_state))
    assert all(len(t.rows) > 100 for t in tables.values())
    for table in tables.values():
        assert set(table.rows[0]) <= {"id", "time", "label", "baseline", "booked_minutes", *table.columns}
    _bundle, metrics = flowmodels.train(tables["or_duration"])
    assert metrics["passed"] and metrics["model"] <= 0.9 * metrics["baseline"]


# ---------- the boards ----------


def test_snapshot_has_the_three_boards_with_predictions(fresh_state):
    snap = service.refresh(fresh_state)
    assert [u.id for u in snap.units] == ["MEDA", "MEDB", "SURG", "ORTH", "ICU"]
    assert snap.kpis.beds == 132 and snap.kpis.occupied == sum(u.occupied for u in snap.units)
    assert all(u.net_gap == u.expected_admissions - u.expected_discharges - u.free for u in snap.units)
    triaged = [e for e in snap.ed if e.ctas is not None]
    assert triaged and all(e.admit is not None and 0 <= e.admit.value <= 1 for e in triaged)
    assert all(e.admit.sentence.startswith("Admission probability") for e in triaged)
    occupied = [c for cells in snap.beds.values() for c in cells if c.status == "O" and c.unit != "ED"]
    assert occupied and all(c.discharge is not None for c in occupied)
    assert snap.kpis.ed_predicted_wait is not None
    live = [c for c in snap.or_cases if c.status != "cancelled"]
    assert live and all(c.predicted_minutes and c.predicted_end for c in live)
    assert any(c.preop for c in live)  # pre-op Tasks are linked to their Appointment


def test_board_follows_the_simulator_within_two_seconds(client, login):
    first = _board(client, login)
    ops = login(OPS)
    assert client.post("/api/hospital/simulator/advance", json={"minutes": 45}, headers=ops).status_code == 200
    started = time.monotonic()
    second = _board(client, login)
    assert time.monotonic() - started < 2.0
    assert second["now"] != first["now"] and second["clock"]["now"] == second["now"]


# ---------- the rule engine ----------


def test_rule_engine_matches_all_twenty_scripted_scenarios():
    report = evals.rule_eval()
    assert report["cases"] == 20
    assert [r["id"] for r in report["results"] if not r["passed"]] == []


def test_every_exception_carries_facts_evidence_and_a_menu_from_the_engine(fresh_state):
    found = _detected(fresh_state)
    assert found
    for d in found:
        assert d.facts and d.evidence_refs and d.menu
        assert {m.owner_role for m in d.menu} <= {"physician", "nurse", "operations_manager"}
    refs = {r for d in found for r in d.evidence_refs}
    assert snapshot.tower_fhir().resolve_refs(sorted(refs)) == refs  # real FHIR ids


# ---------- the exception stream ----------


def test_stream_opens_once_clears_and_reminds(fresh_state):
    store = fresh_state
    service.refresh(store)
    opened = X.all_exceptions(store)
    assert opened and all(e.status == "open" for e in opened)
    now = hospital_now(store)
    # the same findings again: nothing changes
    assert X.sync(store, _detected(store), now) == {"opened": [], "changed": [], "cleared": [], "reopened": []}
    target = opened[0]
    service.defer(store, store.staff[OPS], target.id, 30)
    assert X.get(store, target.id).status == "deferred"
    found = [d for d in _detected(store) if d.key == target.key]
    changes = X.sync(store, found, now + timedelta(minutes=31))
    assert changes["reopened"] == [target.id] and X.get(store, target.id).status == "open"
    assert X.get(store, target.id).reminders == 1
    cleared = [e.id for e in opened if e.key != target.key]
    assert sorted(changes["cleared"]) == sorted(cleared)
    assert all(X.get(store, i).status == "cleared" for i in cleared)


# ---------- the narrator ----------


def _exception(store) -> dict:
    service.refresh(store)
    return X.stream(store)[0].model_dump(mode="json")


def test_guards_refuse_off_menu_actions_new_numbers_claims_and_unknown_evidence(fresh_state):
    exc = _exception(fresh_state)
    menu = exc["menu"][0]
    good = agent.NarratorOutput(exception_id=exc["id"], severity=exc["severity"], narrative=exc["summary"],
                                recommended_actions=[{"action_id": menu["action_id"], "action": menu["label"],
                                                      "rationale": menu["why"], "owner_role": menu["owner_role"],
                                                      "expected_effect": menu["effect"]}],
                                evidence_refs=exc["evidence_refs"][:1])
    assert agent.check(good, exc) == []
    other_role = next(r for r in ("physician", "nurse", "operations_manager") if r != menu["owner_role"])
    bad = agent.NarratorOutput.model_validate(dict(
        good.model_dump(), severity="low" if exc["severity"] != "low" else "high",
        narrative=exc["summary"] + " About 4321 patients are affected and the beds have been assigned.",
        recommended_actions=[{**good.recommended_actions[0].model_dump(), "owner_role": other_role},
                             {"action_id": "call_the_minister", "action": "x", "rationale": "x",
                              "owner_role": "nurse", "expected_effect": "x"}],
        evidence_refs=["Encounter/made-up-1"]))
    problems = " | ".join(agent.check(bad, exc))
    for expected in ("severity must be", "not on the menu", "belongs to", "4321", "claims an action was taken",
                     "evidence not offered"):
        assert expected in problems, expected


def test_narration_goes_to_the_review_queue_with_trace_and_provenance(fresh_state):
    store = fresh_state
    exc_id = _exception(store)["id"]
    exc = service.narrate(store, exc_id)
    n = exc.narration
    assert n["source"] == "model" and n["attempts"] == 1 and not n["problems"] and not n["unresolved_refs"]
    agent.NarratorOutput.model_validate({k: n[k] for k in ("exception_id", "severity", "narrative",
                                                           "recommended_actions", "evidence_refs")})
    task = store.fhir.read("Task", exc.review_task_id)
    assert approvals.code_of(task) == approvals.REVIEW and task["intent"] == "proposal"
    assert {c["coding"][0]["code"] for c in task["performerType"]} == {"ops", "nurse"}
    back = lineage(f"Task/{exc.review_task_id}")
    assert back["run"] == n["run_id"] and back["prompt_version"] == "control_tower@1"
    assert back["trace"]["actor_id"] == "system" and back["provenance"]["target"][0]["reference"] == \
        f"Task/{exc.review_task_id}"


def test_bad_model_answers_fall_back_to_the_engines_template(fresh_state, monkeypatch):
    store = fresh_state
    exc_id = _exception(store)["id"]

    def liar(text, images, attempt):
        return {**agent._mock(text, images, attempt), "narrative": "I have moved 99 patients to the overflow ward."}

    monkeypatch.setitem(MOCK_FIXTURES, "control_tower", liar)
    exc = service.narrate(store, exc_id)
    n = exc.narration
    assert n["source"] == "template" and n["attempts"] == 2 and n["ai_status"] == "guard"
    assert any("99" in p for p in n["problems"]) and any("claims" in p for p in n["problems"])
    assert n["narrative"] == exc.summary and exc.review_task_id  # still in the review queue, marked as template


def test_evidence_that_does_not_resolve_in_fhir_is_never_shown(fresh_state):
    store = fresh_state
    service.refresh(store)
    exc = X.stream(store)[0]
    X.save(store, exc.model_copy(update=dict(evidence_refs=["Encounter/stay-99999", *exc.evidence_refs])))
    narrated = service.narrate(store, exc.id)
    assert "Encounter/stay-99999" not in narrated.narration["evidence_refs"]
    assert "Encounter/stay-99999" in narrated.narration["unresolved_refs"]


# ---------- the action drawer ----------


def test_exception_approve_task_audit(fresh_state, client, login):
    store = fresh_state
    board = _board(client, login)
    exc = board["exceptions"][0]
    detail = client.get(f"/api/control-tower/exceptions/{exc['id']}", headers=login(OPS)).json()
    assert detail["narration"]["recommended_actions"] and detail["can_decide"]
    chosen = [a["action_id"] for a in detail["narration"]["recommended_actions"]][:2]
    r = client.post(f"/api/control-tower/exceptions/{exc['id']}/approve",
                    json={"action_ids": chosen, "note": "go ahead"}, headers=login(OPS))
    assert r.status_code == 200, r.text
    done = r.json()
    assert done["status"] == "approved" and [a["action_id"] for a in done["decision"]["actions"]] == chosen
    menu = {m["action_id"]: m for m in detail["menu"]}
    codes = {"physician": "doctor", "nurse": "nurse", "operations_manager": "ops"}
    for action in done["decision"]["actions"]:
        task = store.fhir.read("Task", action["task_id"])
        assert task["status"] == "requested" and task["code"]["coding"][0]["code"] == "flow-action"
        assert task["performerType"][0]["coding"][0]["code"] == codes[menu[action["action_id"]]["owner_role"]]
        assert task["basedOn"][0]["reference"] == f"Task/{detail['review_task_id']}"
        assert traces.run_of(task) == done["decision"]["execution_run"]  # written by the agent, after approval
    review = store.fhir.read("Task", detail["review_task_id"])
    assert review["status"] == "completed" and approvals.outputs(review)["decided_by"] == OPS
    approve = [e for e in store.audit.query(event_type="approve") if e.resource_id == detail["review_task_id"]]
    assert approve and approve[0].user_id == OPS and approve[0].module == "control_tower"
    tool_calls = [e for e in store.audit.query(event_type="tool_call") if "createFlowTask" in (e.resource_id or "")]
    assert len(tool_calls) >= len(chosen)
    trace = traces.get(done["decision"]["execution_run"])
    assert trace.human_action["decision"] == "approve" and trace.human_action["user_id"] == OPS
    again = client.post(f"/api/control-tower/exceptions/{exc['id']}/approve", json={"action_ids": chosen},
                        headers=login(OPS))
    assert again.status_code == 409  # decided once


def test_reject_needs_a_reason_and_defer_a_time(client, login):
    exceptions = _board(client, login)["exceptions"]
    ops = login(OPS)
    first, second = exceptions[0]["id"], exceptions[1]["id"]
    assert client.post(f"/api/control-tower/exceptions/{first}/reject", json={"reason": "no"},
                       headers=ops).status_code == 409
    r = client.post(f"/api/control-tower/exceptions/{first}/reject", json={"reason": "Discharges already planned"},
                    headers=ops)
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert r.json()["decision"]["note"] == "Discharges already planned"
    assert client.post(f"/api/control-tower/exceptions/{second}/defer", json={"minutes": 5},
                       headers=ops).status_code == 409
    r = client.post(f"/api/control-tower/exceptions/{second}/defer", json={"minutes": 60}, headers=ops)
    assert r.status_code == 200 and r.json()["status"] == "deferred" and r.json()["remind_at"]


def test_only_the_bed_manager_or_a_charge_nurse_of_the_unit_decides(fresh_state, client, login):
    store = fresh_state
    service.refresh(store)
    exc = X.stream(store)[0]
    r = client.post(f"/api/control-tower/exceptions/{exc.id}/approve",
                    json={"action_ids": [exc.menu[0]["action_id"]]}, headers=login(PHYSICIAN))
    assert r.status_code == 403
    nurse = store.staff[NURSE]
    foreign = next((e for e in X.stream(store) if e.unit_id not in nurse.unit_ids), None)
    if foreign is not None:
        r = client.post(f"/api/control-tower/exceptions/{foreign.id}/approve",
                        json={"action_ids": [foreign.menu[0]["action_id"]]}, headers=login(NURSE))
        assert r.status_code == 403 and "not one of your units" in r.json()["detail"]


# ---------- views and the patient card ----------


def _meda_stay(store):
    return next(c for c in service.refresh(store).beds["MEDA"] if c.encounter_id)


def test_patient_card_hides_fields_by_role(fresh_state, client, login):
    store = fresh_state
    cell = _meda_stay(store)
    ops = client.get(f"/api/control-tower/patients/{cell.encounter_id}", headers=login(OPS)).json()
    assert ops["mrn"] == cell.mrn and ops["admitted_at"] and ops["expected_discharge"]
    for hidden in ("name", "age", "reason", "flags", "vitals", "discharge"):
        assert hidden not in ops and hidden in ops["hidden"]
    nurse = client.get(f"/api/control-tower/patients/{cell.encounter_id}", headers=login(NURSE)).json()
    assert nurse["hidden"] == [] and nurse["name"] and nurse["reason"] and nurse["mrn"] == cell.mrn
    assert nurse["discharge"]["sentence"].startswith("Discharge within 24 h")
    outside = next(c for c in service.refresh(store).beds["ICU"]
                   if c.encounter_id and not FhirGateway(Actor.of(store.staff[PHYSICIAN]), "t")
                   .is_in_scope(c.patient_id))
    r = client.get(f"/api/control-tower/patients/{outside.encounter_id}", headers=login(PHYSICIAN))
    assert r.status_code == 403 and r.json()["code"] == "break_glass_required"


def test_board_shows_mrns_only_within_the_viewers_scope(fresh_state, client, login):
    ops = _board(client, login, OPS)
    nurse = _board(client, login, NURSE)
    assert ops["ed"] and all(row["mrn"] for row in ops["ed"])
    assert all(row["mrn"] is None and not row["identified"] for row in nurse["ed"])  # the ED is not their unit
    triaged = [row for row in ops["ed"] if row["admit"]]
    if triaged:  # names of the factors only for the bed manager (no clinical values)
        clinical = {r["encounter_id"]: r for r in nurse["ed"]}
        assert any(row["admit"]["sentence"] != clinical[row["encounter_id"]]["admit"]["sentence"] for row in triaged)
    meda = client.get("/api/control-tower/units/MEDA", headers=login(NURSE)).json()
    assert any(b["mrn"] for b in meda["beds"] if b["encounter_id"])
    assert client.get("/api/control-tower/board", headers=login("U-ADMIN")).status_code == 403


# ---------- the simulator from the control bar ----------


def test_fast_forward_job_advances_in_steps(fresh_state):
    store = fresh_state
    now = hospital_now(store)
    target = now + timedelta(hours=2)
    events = simjobs.run_sync(store, target)
    assert hospital_now(store) == target and events > 0


def test_fast_forward_job_needs_the_bed_manager(client, login):
    r = client.post("/api/hospital/simulator/fast-forward/start", json={"hour": 8}, headers=login(NURSE))
    assert r.status_code == 403


def test_icu_surge_fills_icu_and_the_engine_raises_it(fresh_state):
    store = fresh_state
    scenarios.inject(store, "icu_surge", operator=OPS, count=9)
    simulator.advance_by(store, 240)
    keys = {d.key: d.severity for d in _detected(store)}
    assert "unit_occupancy:ICU" in keys and keys["unit_occupancy:ICU"] in ("med", "high")
    assert "ed_boarding:ED" in keys


def test_a_persons_simulator_action_waits_for_a_tick_in_progress(fresh_state):
    """The background tick holds the simulator lock for its transaction; pause / run / advance wait for it
    (up to 5 s) instead of answering 409, so the control bar's buttons do not fail at random."""
    import threading

    from sqlalchemy import text

    from app.core.store import get_engine

    other = get_engine().connect()
    tick = other.begin()
    other.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": simulator.SIM_LOCK})
    threading.Timer(1.0, tick.rollback).start()  # the "tick" ends after a second
    started = time.monotonic()
    try:
        assert simulator.pause(fresh_state)["running"] is False
        assert 0.8 < time.monotonic() - started < simulator_wait_seconds()
    finally:
        other.close()


def simulator_wait_seconds() -> float:
    return float(simulator.LOCK_WAIT.rstrip("s"))
