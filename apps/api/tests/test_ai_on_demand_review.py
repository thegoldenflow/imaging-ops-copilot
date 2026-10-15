"""The pre-merge review of AI on demand (casebook 3.23, "review"): each test here failed on 5c922b9.

Retries are decided by the run that wins the item's slot, under the item's lock, and never throw away a person's
work; scope is checked before the model runs, and refusals are audited; work is queued after its request commits
(real transactions); the activity panel shows no patient references or session ids and everyone's only to the
operations roles; a job re-checks its item; the eager narrator commits item by item; the kinds register in any
process; typical times come from runs that got an answer."""

import dataclasses
import os
import subprocess
import sys
import threading
from collections import Counter
from datetime import datetime
from pathlib import Path

import pytest

from app import main
from app.agents import ondemand
from app.agents import trace as traces
from app.core import activity, config
from app.core.models import Role
from app.core.store import get_store, unit_of_work
from app.llm import budget
from app.llm.gateway import LlmGateway, set_gateway
from app.llm.providers import MockProvider, ProviderUnavailable
from app.modules.control_tower import exceptions as X
from app.modules.control_tower import service as control_tower
from app.modules.feedback import service as feedback
from app.modules.med_safety import service as med_safety
from app.modules.med_safety.models import get_review, review_key, reviews, session_key, sessions
from app.modules.protocols import service as protocols
from app.modules.requisitions import service as requisitions
from app.modules.storyline import service as storyline
from app.modules.triage import service as triage
from tests.test_spend_guard import _one_pass

API = Path(__file__).resolve().parents[1]
SESSION = {"X-Demo-Session": "browser-review"}


def _mode(monkeypatch, mode: str) -> None:
    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, background_ai=mode))


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    _mode(monkeypatch, "on_demand")
    med_safety.reset_state()
    control_tower.reset_state()
    activity.reset()
    yield
    med_safety.reset_state()
    control_tower.reset_state()
    activity.reset()
    ondemand.reset(everything=True)


def _calls(store) -> Counter:
    return Counter(c.task for c in store.llm_calls)


class FailingFor(MockProvider):
    """The mock model, down for some tasks."""

    def __init__(self, tasks: set[str]) -> None:
        super().__init__(latency_s=0)
        self.down = tasks

    def complete_json(self, *, task, **kw):
        if task in self.down:
            raise ProviderUnavailable("timeout")
        return super().complete_json(task=task, **kw)


def _staff(store, role: Role):
    return next(u for u in store.staff.values() if u.role == role and u.demo_login)


# ---------- 1 · a retry never throws away a person's work ----------


def test_retry_of_a_requisition_is_ops_only_and_never_after_a_person_worked_on_it(fresh_state, client, login):
    store = fresh_state
    fd, rad, ops = login("U-FD"), login("U-RAD"), login("U-OPS")
    set_gateway(LlmGateway(FailingFor({"protocol_suggest"})))
    for req_id in ("REQ-NEW2", "REQ-NEW3"):
        client.post(f"/api/requisitions/{req_id}/ai?trigger=opened", headers=fd)
    set_gateway(LlmGateway(MockProvider(latency_s=0)))
    before = _calls(store)
    # the front desk may open it, not re-run it (main: only ops and admin could reprocess)
    assert client.post("/api/requisitions/REQ-NEW2/ai?trigger=asked", headers=fd).status_code == 403
    assert store.audit.query(resource_type="requisition", resource_id="REQ-NEW2", outcome="denied")
    ai = client.get("/api/requisitions/REQ-NEW2", headers=fd).json()["summary"]["ai"]
    assert ai["state"] == "unavailable" and ai["retryable"] and "protocol" in ai["error"] and _calls(store) == before
    # the radiologist confirms the AI's triage: now a re-run would throw that away
    assert client.post("/api/requisitions/REQ-NEW2/triage/confirm", headers=rad).status_code == 200
    r = client.post("/api/requisitions/REQ-NEW2/ai?trigger=asked", headers=ops)
    assert r.status_code == 409 and "triage confirmed by" in r.json()["detail"]
    tri = triage.records(store)["REQ-NEW2"]
    assert tri.review_action == "confirmed" and tri.reviewed_by and _calls(store) == before
    assert not client.get("/api/requisitions/REQ-NEW2", headers=ops).json()["summary"]["ai"]["retryable"]
    # nobody worked on REQ-NEW3: ops' Retry runs its pipeline again
    view = client.post("/api/requisitions/REQ-NEW3/ai?trigger=asked", headers=ops).json()
    assert view["summary"]["ai"]["state"] == "done" and protocols.records(store)["REQ-NEW3"].ai_status == "ok"
    assert sum((_calls(store) - before).values()) == 3


# ---------- 2 · a retry is decided under the item's lock; a decided review is never commented ----------


def test_a_decided_review_is_never_commented_whatever_its_status_says(fresh_state):
    """The race the review found: a Retry re-armed a review just before the pharmacist's decision landed; the
    commentary then ran on a decided review and opened a Task nobody would close (casebook 3.22)."""
    store = fresh_state
    med_safety.intake(store, force=True)
    review = next(r for r in reviews(store).values() if r.commentary_status == "on_open" and r.encounter_id)
    pharmacist = _staff(store, Role.PHARMACIST)
    decided = med_safety.decide_review(store, pharmacist, review.id, "accept_order", "Reviewed with the team")
    reviews(store)[review_key(decided.order_ref)] = decided.model_copy(update={"commentary_status": "on_demand"})
    before = _calls(store)
    after = med_safety.comment(store, review.id, wait=True)
    assert _calls(store) == before and after.decision and after.review_task_id == decided.review_task_id
    assert not med_safety.review_ai(after)["retryable"]


def test_a_retry_is_the_runs_decision_not_a_write_before_it(fresh_state, client, login):
    """Retry on a narrative the template wrote: the exception is narrated again by the model, once; on 5c922b9 the
    retry re-armed the record outside the lock, before the running check."""
    store = fresh_state
    ops = login("U-OPS")
    control_tower.refresh(store, force=True)
    exc = next(e for e in X.all_exceptions(store) if e.status in X.LIVE and e.cleared_at is None)
    set_gateway(LlmGateway(FailingFor({"control_tower"})))
    view = client.post(f"/api/control-tower/exceptions/{exc.id}/narrate?trigger=opened", headers=ops).json()
    assert view["ai"]["state"] == "unavailable" and view["ai"]["retryable"]
    set_gateway(LlmGateway(MockProvider(latency_s=0)))
    before = _calls(store)
    view = client.post(f"/api/control-tower/exceptions/{exc.id}/narrate?trigger=opened", headers=ops).json()
    assert view["ai"]["state"] == "unavailable" and _calls(store) == before  # opening again never retries
    view = client.post(f"/api/control-tower/exceptions/{exc.id}/narrate?trigger=asked", headers=ops).json()
    assert view["ai"]["state"] == "done" and _calls(store) - before == Counter(control_tower=1)
    client.post(f"/api/control-tower/exceptions/{exc.id}/narrate?trigger=asked", headers=ops)
    assert _calls(store) - before == Counter(control_tower=1)  # a second Retry finds it done


# ---------- 3 · the activity panel ----------


def test_the_activity_shows_no_patient_refs_no_session_ids_and_everyones_to_ops_only(fresh_state, client, login):
    store = fresh_state
    fd = {**login("U-FD"), **SESSION}
    run = traces.AgentTrace(run_id=store.next_id("RUN"), agent_id="discharge_summary", agent_version="1",
                            kind="run", eval_status="pending", evaluated=False, encounter_id="stay-01109",
                            input_refs=["Patient/pat-00042", "Encounter/stay-01109"], outcome="completed",
                            started_at=datetime.now(), finished_at=datetime.now(),
                            cause={"session": "browser-review", "trigger": "action", "user_id": "U-FD"},
                            llm_calls=[traces.LlmCallRecord(call_id="LLM-X", agent_id="discharge_summary",
                                                            model="m", prompt_version="p", status="ok")])
    traces.save(run, store)
    mine = client.get("/api/ai/activity", headers=fd).json()
    assert [a["item"] for a in mine["actions"]] == [None]  # not "Patient/pat-00042"
    assert "session" not in mine and all("session" not in a for a in mine["actions"])
    assert "browser-review" not in str(mine)
    r = client.get("/api/ai/activity?scope=all", headers=fd)
    assert r.status_code == 403 and store.audit.query(resource_type="ai_activity", outcome="denied")
    ops = {**login("U-OPS"), **SESSION}
    assert client.get("/api/ai/activity?scope=all", headers=ops).status_code == 200
    assert store.audit.query(resource_type="ai_activity", resource_id="all", action="read")


# ---------- 4 · scope before the model ----------


def test_an_out_of_scope_physician_runs_nothing_and_the_refusal_is_audited(fresh_state, client, login):
    store = fresh_state
    med_safety.intake(store, force=True)
    physician = _staff(store, Role.PHYSICIAN)
    md = login(physician.id)
    review = next(r for r in reviews(store).values() if "physician" in r.reviewer_roles and r.encounter_id
                  and r.unit_id not in physician.unit_ids and r.commentary_status == "on_open")
    session = next(s for s in sessions(store).values() if s.unit_id not in physician.unit_ids
                   and s.ai_status == "on_open")
    before = _calls(store)
    out = client.post("/api/ai/batch", headers=md, json={"items": [{"kind": "order_review", "id": review.id},
                                                                    {"kind": "medrec", "id": session.id}]}).json()
    assert out["queued"] == 0 and out["denied"] == 2 and ondemand.snapshot()["queued"] == []
    assert store.audit.query(resource_type="ai_order_review", resource_id=review.id, outcome="denied")
    r = client.post(f"/api/med-safety/medrec/{session.id}/ai?trigger=opened", headers=md)
    assert r.status_code == 403 and r.json()["code"] in ("break_glass_required", "forbidden")
    r = client.post(f"/api/med-safety/reviews/{review.id}/commentary?trigger=opened", headers=md)
    assert r.status_code == 403
    assert _calls(store) == before
    assert sessions(store).get(session_key(session.encounter_id, session.kind)).ai_status == "on_open"  # unwritten


# ---------- 5 · work is queued once its request commits (real transactions) ----------


def test_a_submitted_requisitions_job_runs_after_the_commit(fresh_state, client, login, monkeypatch):
    """No shared test store here: each request commits on its own, and the job runs in its own transaction, the
    moment it is queued (the job loop polls every 0.3 s: it can beat a slow commit). On 5c922b9 the job was queued
    inside the request, ran before the commit, found no requisition and left it "received" for good."""
    from app.core.store import set_ambient_store

    set_ambient_store(None)  # every request and the job: their own transactions, committed
    # committed rows outlive the test: no MRI questionnaire or other follow-up (its message leaked into
    # test_phase2's outbox once), and the requisition's own records are removed below
    monkeypatch.setattr(requisitions, "PROCESSED_HOOKS", [])
    ondemand.reset(everything=True)
    original = ondemand.request
    ran = []

    def request_and_run_at_once(*args, **kwargs):
        queued = original(*args, **kwargs)
        job = ondemand.next_job()
        worker = threading.Thread(target=main._ai_job, args=(job,))
        worker.start()
        worker.join()
        ran.append(job.item_id)
        return queued

    monkeypatch.setattr(ondemand, "request", request_and_run_at_once)
    fd = login("U-FD")
    sample = client.get("/api/requisitions/samples", headers=fd).json()["samples"][0]
    req_id = client.post("/api/requisitions", headers=fd, json={
        "patient_id": sample["patient_id"], "referrer_id": sample["referrer_id"], "text": sample["text"]}).json()["id"]
    try:
        with unit_of_work() as store:
            status = store.requisitions[req_id].status
        assert ran == [req_id] and status == "ready"
    finally:
        with unit_of_work() as store:
            for table in (requisitions.extractions(store), triage.records(store), protocols.records(store)):
                table.pop(req_id, None)
            store.requisitions.pop(req_id, None)
        set_ambient_store(fresh_state)
        ondemand.reset(everything=True)


# ---------- 6 · the storyline step's Ask AI asks again for a failed item ----------


def test_the_storyline_steps_ask_ai_queues_a_failed_review_for_a_retry(fresh_state, client, login, monkeypatch):
    store = fresh_state
    med_safety.intake(store, force=True)
    review = next(r for r in reviews(store).values() if r.commentary_status == "on_open" and r.encounter_id
                  and "pharmacist" in r.reviewer_roles)
    reviews(store)[review_key(review.order_ref)] = review.model_copy(update={"commentary_status": "unavailable"})
    store.modules["storyline"] = {"prepared_at": datetime.now().isoformat()}
    monkeypatch.setattr(storyline, "_step_items", lambda s, key: [("order_review", review.id)])
    r = client.post("/api/storyline/steps/medrec/ai", headers=login("U-PHAR-01"))
    assert r.status_code == 200
    before = _calls(store)
    assert ondemand.drain(store) == 1 and _calls(store) - before == Counter(order_review=1)
    assert get_review(store, review.id).commentary_status == "done"


def test_the_storyline_steps_ask_ai_is_refused_to_whoever_may_ask_for_none_of_its_items(fresh_state, client,
                                                                                        login, monkeypatch):
    store = fresh_state
    med_safety.intake(store, force=True)
    review = next(r for r in reviews(store).values() if r.commentary_status == "on_open" and r.encounter_id)
    store.modules["storyline"] = {"prepared_at": datetime.now().isoformat()}
    monkeypatch.setattr(storyline, "_step_items", lambda s, key: [("order_review", review.id)])
    r = client.post("/api/storyline/steps/medrec/ai", headers=login("U-REF"))
    assert r.status_code == 403 and store.audit.query(resource_type="storyline_step", outcome="denied")


# ---------- 8 · a job re-checks its item ----------


def test_a_job_for_an_exception_that_cleared_meanwhile_calls_nothing(fresh_state):
    store = fresh_state
    control_tower.refresh(store, force=True)
    exc = next(e for e in X.all_exceptions(store) if e.status in X.LIVE and e.cleared_at is None)
    assert ondemand.request("exception", exc.id)
    X.save(store, exc.model_copy(update={"cleared_at": exc.detected_at}))
    before = _calls(store)
    ondemand.drain(store)
    assert _calls(store) == before
    assert control_tower.narrate(store, exc.id).narration_status != "done" and _calls(store) == before


# ---------- 9 · the eager narrator commits item by item ----------


def test_the_eager_narrator_works_one_item_per_transaction(fresh_state, monkeypatch):
    _mode(monkeypatch, "eager")
    activity.touch()
    names = _one_pass(monkeypatch, main._narrator_loop, 1)
    assert names.count("medication safety commentary") == 2 and "medication safety AI" not in names
    store = fresh_state
    med_safety.intake(store, force=True)
    waiting = sum(r.commentary_status == "pending" for r in reviews(store).values())
    before = _calls(store)
    med_safety.commentary_step(store)
    assert _calls(store) - before == Counter(order_review=1)
    assert sum(r.commentary_status == "pending" for r in reviews(store).values()) == waiting - 1


# ---------- 10 · the kinds register in any process ----------


def test_a_process_that_never_imported_a_module_still_finds_its_kind():
    """The standalone Temporal worker runs the storyline's imaging step (run_now("requisition", ...)) without having
    imported the requisitions module."""
    code = "from app.agents import ondemand; print(ondemand.kind('requisition').name, ondemand.kind('medrec').name)"
    out = subprocess.run([sys.executable, "-c", code], cwd=API, capture_output=True, text=True, timeout=120,
                         env={**os.environ, "PYTHONUTF8": "1"})
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.split() == ["requisition", "medrec"]


# ---------- 11 · refusals are audited ----------


def test_feedback_out_of_site_is_denied_and_audited(fresh_state, client, login):
    store = fresh_state
    fd_user = _staff(store, Role.FRONT_DESK)
    other = next(r for r in feedback.responses(store).values() if fd_user.site_ids and r.site_id not in fd_user.site_ids)
    r = client.post(f"/api/feedback/responses/{other.id}/ai?trigger=asked", headers=login(fd_user.id))
    assert r.status_code == 403
    assert store.audit.query(resource_type="patient_feedback", resource_id=other.id, outcome="denied")


# ---------- 12 · lists show what is queued; nothing to retry is not a write ----------


def test_the_med_safety_queue_shows_a_queued_item_and_a_done_requisition_is_not_rewritten(fresh_state, client,
                                                                                          login):
    store = fresh_state
    pharmacist = login("U-PHAR-01")
    items = client.get("/api/med-safety/queue?show=all", headers=pharmacist).json()["items"]
    item = next(i for i in items if i["kind"] == "order_review" and i["ai_state"] == "not_requested"
                and i["commentary_status"] == "on_open")
    assert ondemand.request("order_review", item["id"])
    items = client.get("/api/med-safety/queue?show=all", headers=pharmacist).json()["items"]
    assert next(i for i in items if i["id"] == item["id"])["ai_state"] == "queued"
    ondemand.reset()
    fd = login("U-FD")
    client.post("/api/requisitions/REQ-NEW1/ai?trigger=opened", headers=fd)
    updates = len(store.audit.query(resource_type="requisition", resource_id="REQ-NEW1", action="update"))
    before = _calls(store)
    client.post("/api/requisitions/REQ-NEW1/ai?trigger=opened", headers=fd)  # opened again: nothing to run
    assert _calls(store) == before
    assert len(store.audit.query(resource_type="requisition", resource_id="REQ-NEW1", action="update")) == updates


# ---------- 13 · typical times from answers; the budget's own words ----------


def test_a_refused_run_neither_shortens_the_typical_time_nor_hides_the_budget(fresh_state, monkeypatch):
    store = fresh_state
    med_safety.intake(store, force=True)
    review = next(r for r in reviews(store).values() if r.commentary_status == "on_open" and r.encounter_id)
    default = ondemand.typical_s("order_review")
    monkeypatch.setattr(budget, "refusal", lambda: f"{budget.REFUSAL_PREFIX}: $5.00 of $5.00")
    ondemand.run_now(store, "order_review", review.id, trigger="opened")
    after = get_review(store, review.id)
    assert after.commentary_status == "unavailable" and "budget" in after.commentary["reason"]
    assert ondemand.typical_s("order_review") == default
