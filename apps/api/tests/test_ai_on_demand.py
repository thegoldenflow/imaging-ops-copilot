"""AI only as a visible consequence of what a person does (app/agents/ondemand.py, BACKGROUND_AI=on_demand).

A fresh data set makes no model call until someone opens an item, presses Ask AI, or does something that creates
new work (a portal requisition, a patient's survey answer, a storyline step). Each item's AI runs once however
often it is asked for. Every run records who caused it, how and for which item, and the activity panel lists the
actions of one browser's demo session. The med-safety and Control Tower side of opening items:
tests/test_background_ai.py."""

import dataclasses
import threading
import time
from collections import Counter

import pytest

from app import main
from app.agents import cause as causes
from app.agents import ondemand
from app.agents import trace as traces
from app.core import activity, config
from app.core.store import get_store
from app.modules.control_tower import service as control_tower
from app.modules.feedback import service as feedback
from app.modules.med_safety import service as med_safety
from app.modules.med_safety.models import get_review, reviews
from app.modules.requisitions import service as requisitions
from tests.test_spend_guard import _one_pass

SESSION = {"X-Demo-Session": "browser-one"}
OTHER = {"X-Demo-Session": "browser-two"}


def _mode(monkeypatch, mode: str) -> None:
    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, background_ai=mode))


@pytest.fixture(autouse=True)
def _fresh():
    med_safety.reset_state()
    control_tower.reset_state()
    activity.reset()
    yield
    med_safety.reset_state()
    control_tower.reset_state()
    activity.reset()


def _calls(store) -> Counter:
    return Counter(c.task for c in store.llm_calls)


def _new_calls(store, before: Counter) -> Counter:
    return _calls(store) - before


def _consented(patient_id: str) -> bool:
    """The AI may read this patient's chart (ai_processing consent). Which patients have it changes with the seeded
    day: on Thursday's the first open order review is a patient without it, and its AI is refused before the model."""
    from app.agents.gateway import runtime_fhir
    from app.ehr.consent import permits

    return permits(runtime_fhir(), patient_id, "ai_processing")


# ---------- nothing nobody asked for ----------


def test_a_fresh_data_set_makes_no_model_call_until_something_is_opened(fresh_state, monkeypatch):
    """Every background step that could call the model, as the API's loops run them with someone signed in: the
    rules run (reviews, reconciliations and exceptions appear), the model is not called. Eager, the same steps
    work through the seeded backlog."""
    _mode(monkeypatch, "on_demand")
    store = fresh_state
    activity.touch()
    before = _calls(store)
    for _ in range(2):
        requisitions.process_pending(store)
        feedback.process_pending(store)
        med_safety.intake(store, force=True)
        control_tower.refresh(store, force=True)
        control_tower.narrate_step(store)
        med_safety.ai_step(store)
        from app.modules.documentation import service as documentation
        from app.modules.storyline import service as storyline

        documentation.intake_step(store)
        storyline.ai_step(store)
        ondemand.drain(store)
    assert _calls(store) == before
    assert reviews(store) and sum(r.commentary_status == "on_open" for r in reviews(store).values()) > 100
    assert {r.status for r in store.requisitions.values() if r.id.startswith("REQ-NEW")} == {"received"}
    assert [r for r in feedback.responses(store).values() if r.ai_status is None]
    assert _one_pass(monkeypatch, main._narrator_loop, 1) == ["documentation intake"]

    _mode(monkeypatch, "eager")
    requisitions.process_pending(store)
    feedback.process_pending(store)
    new = _new_calls(store, before)
    assert new["requisition_triage"] == 3 and new["feedback_classify"] == 2


# ---------- the requisition pipeline ----------


def test_a_seeded_requisition_runs_its_pipeline_when_opened_and_once_only(fresh_state, client, login, monkeypatch):
    _mode(monkeypatch, "on_demand")
    store = fresh_state
    fd = {**login("U-FD"), **SESSION}
    queue = client.get("/api/requisitions", headers=fd).json()["requisitions"]
    waiting = [r for r in queue if r["status"] == "received"]
    assert {r["id"] for r in waiting} == {"REQ-NEW1", "REQ-NEW2", "REQ-NEW3"}
    assert all(r["ai"]["state"] == "not_requested" and r["ai"]["auto"] for r in waiting)
    seeded = next(r for r in queue if r["status"] != "received")
    assert seeded["ai"] is None  # pre-processed by the rule baseline when the data was made

    before = _calls(store)
    detail = client.get("/api/requisitions/REQ-NEW2", headers=fd).json()
    assert detail["summary"]["ai"]["state"] == "not_requested" and detail["triage"] is None
    assert _calls(store) == before  # reading it calls nothing
    detail = client.post("/api/requisitions/REQ-NEW2/ai?trigger=opened", headers=fd).json()
    ai = detail["summary"]["ai"]
    assert ai["state"] == "done" and ai["generated_at"] and ai["duration_ms"] >= 0
    assert detail["triage"]["ai_priority"] and detail["protocol"]["primary_id"]
    assert _new_calls(store, before) == Counter(requisition_extract=1, requisition_triage=1, protocol_suggest=1)
    # opened again, asked again: nothing more
    client.post("/api/requisitions/REQ-NEW2/ai?trigger=opened", headers=fd)
    client.post("/api/requisitions/REQ-NEW2/ai?trigger=asked", headers=fd)
    assert sum(_new_calls(store, before).values()) == 3

    # the three calls are one action of this browser's session, "opened"
    activity = client.get("/api/ai/activity", headers=fd).json()
    assert len(activity["actions"]) == 1
    action = activity["actions"][0]
    assert action["calls"] == 3 and action["trigger"] == "opened" and action["item"]["item_id"] == "REQ-NEW2"
    assert action["item"]["link"] == "/requisitions/REQ-NEW2" and action["by"]["name"]
    assert action["what"].startswith("Extracted, triaged") and activity["totals"]["calls"] == 3
    assert client.get("/api/ai/activity", headers={**login("U-FD"), **OTHER}).json()["actions"] == []


def test_a_submitted_requisition_runs_at_once_as_the_persons_action(fresh_state, client, login, monkeypatch):
    _mode(monkeypatch, "on_demand")
    store = fresh_state
    ref = {**login("U-REF"), **SESSION}
    body = {"new_patient": {"given_name": "Ana", "family_name": "Example", "dob": "1958-02-11", "sex": "F",
                            "phone": "+1-416-555-0101", "health_card": "1234567890", "health_card_version": "AB",
                            "preferred_language": "fr"},
            "exam_requested": "CT abdomen and pelvis with contrast",
            "clinical_information": "Right lower quadrant pain and weight loss over 2 months", "urgent": True}
    req_id = client.post("/api/portal/requisitions", json=body, headers=ref).json()["id"]
    snapshot = ondemand.snapshot()
    assert [(j["kind"], j["item_id"]) for j in snapshot["queued"]] == [("requisition", req_id)]
    assert snapshot["queued"][0]["cause"]["session"] == "browser-one"
    assert ondemand.drain(store) == 1
    assert store.requisitions[req_id].status == "ready"
    runs = [t for t in traces.table(store).values() if (t.cause or {}).get("item_id") == req_id]
    assert len(runs) == 3 and {t.cause["trigger"] for t in runs} == {"action"}
    assert {t.cause["note"] for t in runs} == {"submitted in the portal"} and len({t.cause["action_id"] for t in runs}) == 1
    # the staff detail says when and why: the referrer's action, not "the background"
    shown = client.get(f"/api/requisitions/{req_id}", headers=login("U-RAD")).json()["summary"]["ai"]
    assert shown["state"] == "done" and shown["cause"]["note"] == "submitted in the portal"
    assert shown["cause"]["trigger"] == "action" and shown["generated_at"]
    # the referrer's own activity shows it; the seeded faxes still wait
    actions = client.get("/api/ai/activity", headers=ref).json()["actions"]
    assert [a["item"]["item_id"] for a in actions] == [req_id]
    assert {r.status for r in store.requisitions.values() if r.id.startswith("REQ-NEW")} == {"received"}


def test_opening_an_item_already_queued_runs_it_now_as_what_it_was_asked_for(fresh_state, client, login,
                                                                              monkeypatch):
    """The front desk submits a requisition and its page opens at once, before the job loop's turn: the page's
    request runs the queued job now, and the run stays the person's submission, not an "opened"."""
    _mode(monkeypatch, "on_demand")
    store = fresh_state
    fd = {**login("U-FD"), **SESSION}
    sample = client.get("/api/requisitions/samples", headers=fd).json()["samples"][0]
    req_id = client.post("/api/requisitions", headers=fd, json={
        "patient_id": sample["patient_id"], "referrer_id": sample["referrer_id"], "text": sample["text"]}).json()["id"]
    assert [j["item_id"] for j in ondemand.snapshot()["queued"]] == [req_id]
    detail = client.post(f"/api/requisitions/{req_id}/ai?trigger=opened", headers=fd).json()
    ai = detail["summary"]["ai"]
    assert ai["state"] == "done" and ai["cause"]["trigger"] == "action"
    assert ai["cause"]["note"] == "received from the intake queue"
    assert ondemand.snapshot()["queued"] == [] and ondemand.drain(store) == 0


def test_the_same_item_asked_for_twice_at_once_runs_once(fresh_state, monkeypatch):
    """Two people (or a double click) press Retry on the same item while its AI runs: only the first may retry; the
    second waits for it, then reads the item again under its lock (`retry=False`: no second paid run, nothing of
    the first overwritten), and only then returns."""
    _mode(monkeypatch, "on_demand")
    calls = []
    started = threading.Event()

    def slow_run(store, item_id, retry):
        calls.append((item_id, retry, time.monotonic()))
        started.set()
        time.sleep(0.4)

    kind = ondemand.KINDS["feedback"]
    monkeypatch.setitem(ondemand.KINDS, "feedback", dataclasses.replace(kind, run=slow_run))
    first = threading.Thread(target=ondemand.run_now, args=(None, "feedback", "FB-X"),
                             kwargs={"trigger": "asked", "retry": True})
    first.start()
    started.wait(2)
    assert ondemand.live("feedback", "FB-X")["state"] == "running"
    began = time.monotonic()
    ondemand.run_now(None, "feedback", "FB-X", trigger="asked", retry=True)
    first.join()
    assert [(i, r) for i, r, _ in calls] == [("FB-X", True), ("FB-X", False)]
    assert calls[1][2] - began >= 0.2  # the second read happened after the first run had finished
    assert ondemand.live("feedback", "FB-X") is None
    assert ondemand.request("feedback", "FB-X") is True and ondemand.request("feedback", "FB-X") is False


# ---------- feedback ----------


def test_a_patients_answer_is_labelled_at_once_and_the_seeded_ones_wait(fresh_state, client, login, monkeypatch):
    _mode(monkeypatch, "on_demand")
    store = fresh_state
    waiting = [r.id for r in feedback.responses(store).values() if r.ai_status is None]
    survey = next(s for s in feedback.surveys(store).values() if s.status == "sent")
    r = client.post(f"/api/public/feedback/{survey.token}", json={"rating": 2, "comment": "Long wait, rude desk."},
                    headers=SESSION)
    resp_id = r.json()["id"]
    before = _calls(store)
    assert ondemand.drain(store) == 1 and _new_calls(store, before) == Counter(feedback_classify=1)
    assert feedback.responses(store)[resp_id].ai_status == "ok"
    assert all(feedback.responses(store)[w].ai_status is None for w in waiting)

    ops = {**login("U-OPS"), **SESSION}
    overview = client.get("/api/feedback/overview", headers=ops).json()
    assert set(overview["ai_not_run"]) == set(waiting)
    shown = next(x for x in overview["responses"] if x["id"] == resp_id)
    assert shown["ai"]["state"] == "done" and shown["ai"]["generated_at"]
    assert shown["ai"]["cause"]["note"] == "a patient answered the survey"
    row = client.post(f"/api/feedback/responses/{waiting[0]}/ai?trigger=asked", headers=ops).json()
    assert row["ai"]["state"] == "done" and row["ai"]["cause"]["trigger"] == "asked"


# ---------- batches ----------


def test_a_batch_states_its_time_queues_what_needs_ai_and_skips_the_rest(fresh_state, client, login, monkeypatch):
    _mode(monkeypatch, "on_demand")
    store = fresh_state
    pharmacist = {**login("U-PHAR-01"), **SESSION}
    items = client.get("/api/med-safety/queue?show=open", headers=pharmacist).json()
    assert items["counts"]["ai_not_run"] > 3
    reviews_ = [i for i in items["items"] if i["kind"] == "order_review" and i["ai_state"] == "not_requested"
                and _consented(get_review(store, i["id"]).patient_id)][:3]
    first = reviews_[0]["id"]
    client.post(f"/api/med-safety/reviews/{first}/commentary?trigger=opened", headers=pharmacist)
    kinds = client.get("/api/ai/kinds", headers=pharmacist).json()["kinds"]
    assert kinds["order_review"]["calls"] == 1 and kinds["requisition"]["calls"] == 3

    body = {"items": [{"kind": "order_review", "id": i["id"]} for i in reviews_]
            + [{"kind": "requisition", "id": "REQ-NEW1"}]}  # not a pharmacist's: left out
    out = client.post("/api/ai/batch", json=body, headers=pharmacist).json()
    assert out["queued"] == 2 and out["skipped"] == 2 and out["calls"] == 2 and out["expected_s"] > 0
    assert store.audit.query(resource_type="ai_requisition", resource_id="REQ-NEW1", outcome="denied")  # on record
    assert out["denied"] == 1
    queued = ondemand.snapshot()["queued"]
    assert [j["cause"]["note"] for j in queued] == ["1 of 2", "2 of 2"]
    assert {j["cause"]["trigger"] for j in queued} == {"batch"}
    before = _calls(store)
    assert ondemand.drain(store) == 2 and _new_calls(store, before) == Counter(order_review=2)
    actions = client.get("/api/ai/activity", headers=pharmacist).json()["actions"]
    assert Counter(a["trigger"] for a in actions) == Counter(batch=2, opened=1)


# ---------- retry ----------


def test_ask_ai_again_after_the_model_was_unavailable(fresh_state, client, login, monkeypatch):
    from app.llm.gateway import LlmGateway, set_gateway
    from app.llm.providers import MockProvider, ProviderUnavailable

    class Down(MockProvider):
        def complete_json(self, **_):
            raise ProviderUnavailable("timeout")

    _mode(monkeypatch, "on_demand")
    store = fresh_state
    pharmacist = login("U-PHAR-01")
    med_safety.intake(store, force=True)
    review = next(r for r in reviews(store).values() if r.commentary_status == "on_open" and r.encounter_id
                  and _consented(r.patient_id))
    set_gateway(LlmGateway(Down(latency_s=0)))
    view = client.post(f"/api/med-safety/reviews/{review.id}/commentary?trigger=opened", headers=pharmacist).json()
    assert view["ai"]["state"] == "unavailable" and view["findings"] is not None and view["ai"]["error"]
    set_gateway(LlmGateway(MockProvider(latency_s=0)))
    # opening it again does not retry by itself; Ask AI does
    view = client.post(f"/api/med-safety/reviews/{review.id}/commentary?trigger=opened", headers=pharmacist).json()
    assert view["ai"]["state"] == "unavailable"
    view = client.post(f"/api/med-safety/reviews/{review.id}/commentary?trigger=asked", headers=pharmacist).json()
    assert view["ai"]["state"] == "done" and view["ai"]["cause"]["trigger"] == "asked"


# ---------- who caused a run ----------


def test_a_run_inside_a_request_is_the_persons_even_when_the_agent_writes_as_itself(fresh_state, client, login,
                                                                                      monkeypatch):
    _mode(monkeypatch, "on_demand")
    store = fresh_state
    ops = {**login("U-OPS"), **SESSION}
    control_tower.refresh(store, force=True)
    exc = next(e for e in control_tower.X.all_exceptions(store) if e.status in control_tower.X.LIVE)
    detail = client.post(f"/api/control-tower/exceptions/{exc.id}/narrate?trigger=opened", headers=ops).json()
    run = traces.get(detail["narration"]["run_id"])
    assert run.actor_id == "system"  # the Control Tower's agent writes on its own authority
    assert run.cause["session"] == "browser-one" and run.cause["user_id"] == "U-OPS"
    assert run.cause["trigger"] == "opened" and run.cause["item_id"] == exc.id
    assert run.cause["link"] == f"/control-tower?exception={exc.id}"
    # the background's own run has no cause
    with causes.using(None):
        assert causes.current() is None


def test_the_activity_of_everyone_includes_the_backgrounds_own_runs(fresh_state, client, login, monkeypatch):
    _mode(monkeypatch, "eager")
    store = fresh_state
    med_safety.intake(store, force=True)
    med_safety.ai_step(store, limit=1)  # the eager background writes one commentary
    ops = {**login("U-OPS"), **SESSION}
    mine = client.get("/api/ai/activity", headers=ops).json()
    everyone = client.get("/api/ai/activity?scope=all", headers=ops).json()
    assert mine["actions"] == [] and mine["background_ai"] == "eager"
    background = [a for a in everyone["actions"] if a["trigger"] == "background"]
    assert {a["what"] for a in background} == {"Reviewed an order", "Judged the new medications of a reconciliation"}
    assert everyone["totals"]["calls"] >= 2 and all(a["by"] is None for a in background)
