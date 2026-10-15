"""Background AI on demand (BACKGROUND_AI, app/core/config.py): with a real model key, a freshly generated data set
carries a backlog the background would write AI output for before anyone looks (about 243 order-review commentaries,
48 reconciliations and 9 narratives per reset). On demand, the background writes the rule results only and the AI of
an order review, a reconciliation or a Control Tower exception runs when a person opens it: reading an item never
calls the model, the web app asks for its AI (POST) as it opens. The imaging side, the job loop, batches and the
activity panel: tests/test_ai_on_demand.py."""

import dataclasses
import threading
import time
from collections import Counter
from contextlib import contextmanager

import pytest
from sqlalchemy import text

from app import main
from app.core import activity, config
from app.core.models import Role
from app.core.store import EngineSource, Store, get_engine
from app.fhir.dt import ref_id
from app.modules.control_tower import exceptions as X
from app.modules.control_tower import service as control_tower
from app.modules.med_safety import service as S
from app.modules.med_safety.models import get_review, review_key, reviews, session_key, sessions
from tests.test_spend_guard import _one_pass

NAPROXEN = "MedicationRequest/medrx-ms-82-naproxen"
QUETIAPINE = "medrx-ms-medrec-quetiapine"


def _mode(monkeypatch, mode: str) -> None:
    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, background_ai=mode))


@pytest.fixture(autouse=True)
def _fresh():
    S.reset_state()
    control_tower.reset_state()
    activity.reset()
    yield
    S.reset_state()
    control_tower.reset_state()
    activity.reset()


def _calls(store) -> Counter:
    return Counter(c.task for c in store.llm_calls)


def _pharmacist(store):
    return next(u for u in store.staff.values() if u.role == Role.PHARMACIST and u.demo_login)


# ---------- what the background writes ----------


def test_on_demand_the_background_writes_the_rule_results_and_calls_no_model(fresh_state, monkeypatch):
    _mode(monkeypatch, "on_demand")
    store = fresh_state
    before = _calls(store)
    S.intake(store, force=True)
    control_tower.refresh(store, force=True)
    table = reviews(store)
    naproxen = table.get(NAPROXEN)
    assert naproxen.commentary_status == "on_open" and naproxen.findings and naproxen.status == "pending"
    statuses = Counter((r.kind, bool(r.findings), r.commentary_status) for r in table.values())
    assert {s for _, _, s in statuses} == {"on_open", "on_demand"}
    assert all(s == "on_demand" for kind, flagged, s in statuses if kind == "service" and not flagged)
    assert all(s.ai_status == "on_open" for s in sessions(store).values()) and len(sessions(store)) > 10
    live = [e for e in X.all_exceptions(store) if e.status in X.LIVE]
    assert live and all(e.narration_status == "on_open" for e in live)
    # nothing for the background's AI steps: what waits for a person is not in their queues
    assert S.pending_commentary(store) == [] and S.pending_settlement(store) == [] and X.pending_narration(store) == []
    S.ai_step(store)
    control_tower.narrate_step(store)
    assert _calls(store) == before


def test_eager_mode_is_unchanged(fresh_state, monkeypatch):
    _mode(monkeypatch, "eager")
    store = fresh_state
    S.intake(store, force=True)
    control_tower.refresh(store, force=True)
    assert reviews(store).get(NAPROXEN).commentary_status == "pending"
    assert all(s.ai_status == "pending" for s in sessions(store).values())
    assert all(e.narration_status == "pending" for e in X.all_exceptions(store) if e.status in X.LIVE)
    assert S.pending_commentary(store) and S.pending_settlement(store) and X.pending_narration(store)


def test_on_demand_the_narrator_loop_writes_only_the_discharge_instructions(monkeypatch):
    activity.touch()  # someone is using the app
    _mode(monkeypatch, "on_demand")
    assert _one_pass(monkeypatch, main._narrator_loop, 1) == ["documentation intake"]
    _mode(monkeypatch, "eager")
    assert _one_pass(monkeypatch, main._narrator_loop, 1) == [
        "control tower narration", "medication safety AI", "documentation intake"]


# ---------- opening an item ----------


def test_opening_an_item_runs_its_ai_and_nothing_else(fresh_state, client, login, monkeypatch):
    _mode(monkeypatch, "on_demand")
    store = fresh_state
    pharmacist = login("U-PHAR-01")
    items = client.get("/api/med-safety/queue?show=all", headers=pharmacist).json()["items"]
    review = next(i for i in items if i.get("order_ref") == NAPROXEN)
    assert review["commentary_status"] == "on_open"
    waiting = sum(r.commentary_status == "on_open" for r in reviews(store).values())

    before = _calls(store)
    view = client.get(f"/api/med-safety/reviews/{review['id']}", headers=pharmacist).json()
    assert view["commentary_status"] == "on_open" and view["findings"]  # the rules show at once, no model call
    assert _calls(store) == before
    assert view["ai"]["state"] == "not_requested" and view["ai"]["auto"]
    # the web app asks for the commentary as the review opens
    view = client.post(f"/api/med-safety/reviews/{review['id']}/commentary?trigger=opened", headers=pharmacist).json()
    assert view["commentary_status"] == "done" and view["commentary"]["concerns"]
    assert view["ai"]["state"] == "done" and view["ai"]["generated_at"] and view["ai"]["duration_ms"] is not None
    assert view["ai"]["cause"]["trigger"] == "opened" and view["ai"]["cause"]["user_id"] == "U-PHAR-01"
    assert set(_calls(store) - before) == {"order_review"}
    assert sum(r.commentary_status == "on_open" for r in reviews(store).values()) == waiting - 1

    encounter = ref_id(store.fhir.read("MedicationRequest", QUETIAPINE)["encounter"])
    medrec = next(i for i in items if i["kind"] == "medrec" and i["encounter_id"] == encounter)
    assert medrec["commentary_status"] == "on_open" and "when opened" in medrec["title"]
    before = _calls(store)
    view = client.get(f"/api/med-safety/medrec/{medrec['id']}", headers=pharmacist).json()
    assert view["ai_status"] == "on_open" and view["ai"]["state"] == "not_requested" and view["items"]  # rules first
    assert _calls(store) == before
    view = client.post(f"/api/med-safety/medrec/{medrec['id']}/ai?trigger=opened", headers=pharmacist).json()
    assert view["ai_status"] == "done" and view["ai"]["state"] == "done"
    assert set(_calls(store) - before) == {"medication_reconciliation"}

    ops = login("U-OPS")
    board = client.get("/api/control-tower/board", headers=ops).json()
    opened = [e for e in board["exceptions"] if e["status"] == "open"]
    assert opened and all(e["narration_status"] == "on_open" and e["narrative"] is None for e in opened)
    before = _calls(store)
    detail = client.get(f"/api/control-tower/exceptions/{opened[0]['id']}", headers=ops).json()
    assert detail["narration_status"] == "on_open" and detail["ai"]["state"] == "not_requested" and detail["facts"]
    assert _calls(store) == before
    detail = client.post(f"/api/control-tower/exceptions/{opened[0]['id']}/narrate?trigger=opened", headers=ops).json()
    assert detail["narration_status"] == "done" and detail["narration"]["narrative"] and detail["ai"]["state"] == "done"
    assert set(_calls(store) - before) == {"control_tower"}
    board = client.get("/api/control-tower/board", headers=ops).json()
    assert [e["narration_status"] for e in board["exceptions"] if e["id"] in {x["id"] for x in opened[1:]}] == \
        ["on_open"] * (len(opened) - 1)


def test_a_decision_on_an_unopened_review_skips_the_commentary(fresh_state, monkeypatch):
    _mode(monkeypatch, "on_demand")
    store = fresh_state
    review = S.review_order(store, NAPROXEN)
    before = _calls(store)
    decided = S.decide_review(store, _pharmacist(store), review.id, "request_change", "Stop naproxen: on warfarin")
    assert decided.commentary_status == "skipped" and decided.status == "resolved"
    assert S.comment(store, review.id, wait=True).commentary_status == "skipped" and _calls(store) == before


# ---------- a person and the background (or another person) at once ----------


@contextmanager
def committed_review(store, order_ref: str):
    """The order's review committed (the other transactions below must see it); removed again afterwards, once the
    test's own transaction has let go of it."""
    setup = Store(EngineSource(get_engine()))
    review = S.review_order(setup, order_ref)
    setup.commit()
    setup.close()
    try:
        yield review
    finally:
        store.rollback()  # its savepoint: releases the row and advisory locks the test's decision took
        cleanup = Store(EngineSource(get_engine()))
        del reviews(cleanup)[review_key(order_ref)]
        cleanup.commit()
        cleanup.close()


@contextmanager
def written_elsewhere(lock_name: str, write):
    """Another transaction (a request that opened the item, or the background) holds the item's lock while it writes
    (`write(store)`), and commits a second later. If the test's transaction wrote the same row meanwhile (the
    decision did not wait), that commit would wait for the test, which waits for it: it gives up after 5 s."""
    other = Store(EngineSource(get_engine()))
    other.conn().execute(text("SET LOCAL lock_timeout = '5s'"))
    assert S._try_lock(other, lock_name)
    write(other)
    timer = threading.Timer(1.0, other.commit)
    timer.start()
    try:
        yield
    finally:
        timer.join()
        other.close()


CONCERN = {"type": "drug_interaction", "severity": "major", "explanation": "Bleeding risk with warfarin.",
           "evidence_refs": [], "confidence": 0.9}


def test_a_decision_waits_for_the_commentary_being_written(fresh_state, monkeypatch):
    """The pharmacist opens a review and decides on the rule findings while the AI is still writing the commentary.
    The decision waits for it: before, it went ahead, and the commentary's transaction then saved its copy of the
    review over the decision (status back to pending, commentary_status done)."""
    _mode(monkeypatch, "on_demand")
    store = fresh_state

    def comment(other):
        found = get_review(other, review.id)
        reviews(other)[review_key(found.order_ref)] = found.model_copy(update={
            "commentary_status": "done", "commentary": {"concerns": [CONCERN], "reason": None}})

    with committed_review(store, NAPROXEN) as review:
        with written_elsewhere(review.id, comment):
            started = time.monotonic()
            decided = S.decide_review(store, _pharmacist(store), review.id, "request_change",
                                      "Stop naproxen: warfarin at home, INR 3.8")
            assert time.monotonic() - started >= 0.8
        assert decided.commentary_status == "done" and decided.commentary["concerns"] == [CONCERN]
        assert decided.status == "resolved" and decided.decision["decision"] == "request_change"


def test_a_person_asking_for_the_commentary_waits_for_the_one_being_written(fresh_state, monkeypatch):
    _mode(monkeypatch, "on_demand")
    store = fresh_state

    def comment(other):
        found = get_review(other, review.id)
        reviews(other)[review_key(found.order_ref)] = found.model_copy(update={
            "commentary_status": "done", "commentary": {"concerns": [CONCERN], "reason": None}})

    with committed_review(store, NAPROXEN) as review:
        before = _calls(store)
        with written_elsewhere(review.id, comment):
            assert S.comment(store, review.id).commentary_status == "on_open"  # the background leaves it
            started = time.monotonic()
            asked = S.comment(store, review.id, wait=True)  # a person waits and gets that commentary
            assert time.monotonic() - started >= 0.8
        assert asked.commentary_status == "done" and asked.commentary["concerns"] == [CONCERN]
        assert _calls(store) == before  # written once


def test_opening_a_reconciliation_waits_for_the_ai_step_under_way(fresh_state, monkeypatch):
    """A reconciliation the background (eager) or another person's request is settling: the person opening it, or
    handling one of its items, waits for that step instead of working on (and saving over) the items before it."""
    _mode(monkeypatch, "on_demand")
    store = fresh_state
    encounter = ref_id(store.fhir.read("MedicationRequest", QUETIAPINE)["encounter"])
    setup = Store(EngineSource(get_engine()))
    session = S.reconcile(setup, encounter, "admission")
    assert session.ai_status == "on_open"
    setup.commit()
    setup.close()

    def settle(other):
        found = sessions(other)[session_key(encounter, "admission")]
        sessions(other)[session_key(encounter, "admission")] = found.model_copy(update={
            "ai_status": "done", "ai_detail": {"run_id": "elsewhere"}})

    try:
        before = _calls(store)
        with written_elsewhere(session.id, settle):
            assert S.settle(store, session.id).ai_status == "on_open"  # the background leaves it
            started = time.monotonic()
            settled = S.settle(store, session.id, wait=True)
            assert time.monotonic() - started >= 0.8
        assert settled.ai_status == "done" and settled.ai_detail == {"run_id": "elsewhere"}
        assert _calls(store) == before
    finally:
        store.rollback()
        cleanup = Store(EngineSource(get_engine()))
        del sessions(cleanup)[session_key(encounter, "admission")]
        cleanup.commit()
        cleanup.close()
