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
import time
from collections import Counter
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import text

from app import main
from app.agents import cause as causes
from app.agents import ondemand
from app.agents import trace as traces
from app.core import activity, config
from app.core.models import Role
from app.core.store import get_engine, get_store, set_ambient_store, unit_of_work
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
from tests.test_ai_on_demand import _consented
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


LEAKS = ("llm_calls", "agent_traces")  # what a committed run leaves (audit events: their own transactions, kept)


@contextmanager
def _committed(monkeypatch, req_id: str | None = None):
    """No shared test store: every request and job in its own transaction, committed. Committed rows outlive the
    test, so: no follow-up messages (an MRI questionnaire once leaked into test_phase2's outbox); the requisition
    `req_id` and its three records put back, one the test created (appended to the yielded list) removed, and the
    model calls and agent traces made meanwhile deleted (they leaked into the activity of a later test once)."""
    set_ambient_store(None)
    monkeypatch.setattr(requisitions, "PROCESSED_HOOKS", [])
    ondemand.reset(everything=True)
    tables = (requisitions.extractions, triage.records, protocols.records)
    created: list[str] = []
    with unit_of_work() as store:
        marks = {t: store.conn().execute(text(f"SELECT coalesce(max(seq), 0) FROM {t}")).scalar() for t in LEAKS}
        saved = store.requisitions[req_id].model_copy(deep=True) if req_id else None
        records = [table(store).get(req_id) for table in tables] if req_id else []
    try:
        yield created
    finally:
        with unit_of_work() as store:
            if req_id:
                store.requisitions[req_id] = saved
                for table, record in zip(tables, records):
                    if record is None:
                        table(store).pop(req_id, None)
                    else:
                        table(store)[req_id] = record
            for new_id in created:
                for table in tables:
                    table(store).pop(new_id, None)
                store.requisitions.pop(new_id, None)
            for name, mark in marks.items():
                store.conn().execute(text(f"DELETE FROM {name} WHERE seq > :mark"), {"mark": mark})
        set_ambient_store(None)
        ondemand.reset(everything=True)


def _jobs_run_at_once(monkeypatch) -> list[str]:
    """Each queued job runs the moment it is queued, in its own thread and transaction (the API's job loop polls
    every 0.3 s: it can beat a slow commit); returns the items run."""
    original, ran = ondemand.request, []

    def request_and_run_at_once(*args, **kwargs):
        queued = original(*args, **kwargs)
        job = ondemand.next_job()
        worker = threading.Thread(target=main._ai_job, args=(job,))
        worker.start()
        worker.join()
        ran.append(job.item_id)
        return queued

    monkeypatch.setattr(ondemand, "request", request_and_run_at_once)
    return ran


def test_a_submitted_requisitions_job_runs_after_the_commit(fresh_state, client, login, monkeypatch):
    """On 5c922b9 the job was queued inside the request, ran before the commit, found no requisition and left it
    "received" for good."""
    try:
        with _committed(monkeypatch) as created:
            ran = _jobs_run_at_once(monkeypatch)
            fd = login("U-FD")
            sample = client.get("/api/requisitions/samples", headers=fd).json()["samples"][0]
            req_id = client.post("/api/requisitions", headers=fd, json={
                "patient_id": sample["patient_id"], "referrer_id": sample["referrer_id"],
                "text": sample["text"]}).json()["id"]
            created.append(req_id)
            with unit_of_work() as store:
                assert ran == [req_id] and store.requisitions[req_id].status == "ready"
    finally:
        set_ambient_store(fresh_state)


# ---------- 6 · the storyline step's Ask AI asks again for a failed item ----------


def test_the_storyline_steps_ask_ai_queues_a_failed_review_for_a_retry(fresh_state, client, login, monkeypatch):
    store = fresh_state
    med_safety.intake(store, force=True)
    review = next(r for r in reviews(store).values() if r.commentary_status == "on_open" and r.encounter_id
                  and "pharmacist" in r.reviewer_roles and _consented(r.patient_id))
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
    review = next(r for r in reviews(store).values() if r.commentary_status == "on_open" and r.encounter_id
                  and _consented(r.patient_id))  # else the consent gate refuses before the budget is asked
    default = ondemand.typical_s("order_review")
    monkeypatch.setattr(budget, "refusal", lambda: f"{budget.REFUSAL_PREFIX}: $5.00 of $5.00")
    ondemand.run_now(store, "order_review", review.id, trigger="opened")
    after = get_review(store, review.id)
    assert after.commentary_status == "unavailable" and "budget" in after.commentary["reason"]
    assert ondemand.typical_s("order_review") == default


# ---------- second round (the re-review of cfdae75): each test here failed on cfdae75 unless it says otherwise ----


def _slow_feedback_run(monkeypatch, release: threading.Event | None = None, hold_s: float = 0.4):
    """A fake run for feedback item FB-X: it records (item, retry) and holds the slot until `release` (or hold_s)."""
    calls, started = [], threading.Event()

    def run(store, item_id, retry):
        calls.append((item_id, retry))
        started.set()
        if release is not None:
            release.wait(10)
        else:
            time.sleep(hold_s)

    kind = ondemand.KINDS["feedback"]
    monkeypatch.setitem(ondemand.KINDS, "feedback", dataclasses.replace(kind, run=run))
    return calls, started


def test_a_second_caller_waits_at_most_its_kinds_budget_then_shows_the_item_running(fresh_state, monkeypatch):
    """A first run that never returns: on cfdae75 the waiter's loop waited 180 s, then again, for good (holding its
    request and pooled connection); now it gives up after the kind's budget and the item shows as running."""
    release = threading.Event()
    calls, started = _slow_feedback_run(monkeypatch, release)
    budget_s = getattr(ondemand, "wait_s", None)  # (none on cfdae75)
    monkeypatch.setattr(ondemand, "wait_s", lambda k: 0.3, raising=False)
    monkeypatch.setattr(ondemand, "WAIT_S", 0.3, raising=False)  # cfdae75's limit, shortened alike: it waited again
    first = threading.Thread(target=ondemand.run_now, args=(None, "feedback", "FB-X"))
    first.start()
    started.wait(2)
    second = threading.Thread(target=ondemand.run_now, args=(None, "feedback", "FB-X"), kwargs={"trigger": "asked"})
    second.start()
    second.join(5)
    try:
        assert not second.is_alive() and calls == [("FB-X", False)]
        assert ondemand.live("feedback", "FB-X")["state"] == "running"
    finally:
        release.set()
        first.join(5)
        second.join(5)
    # the real bound: each model call may make two attempts within its budget (a requisition: three calls)
    assert budget_s(ondemand.kind("requisition")) >= 3 * 2 * config.settings.llm_timeout_s


def test_a_retry_that_waited_for_an_opening_runs_after_it(fresh_state, monkeypatch):
    """Retry pressed while someone's opening (or a plain job) holds the failed item's slot: that run does not retry,
    so the Retry runs next (on cfdae75 its flag was cleared and nothing ran again). Two Retries: the second reads the
    first's result (test_ai_on_demand.py)."""
    calls, started = _slow_feedback_run(monkeypatch)
    opening = threading.Thread(target=ondemand.run_now, args=(None, "feedback", "FB-X"), kwargs={"trigger": "opened"})
    opening.start()
    started.wait(2)
    ondemand.run_now(None, "feedback", "FB-X", trigger="asked", retry=True)
    opening.join(5)
    assert calls == [("FB-X", False), ("FB-X", True)]


def test_two_retries_of_one_failed_requisition_at_once_run_its_pipeline_once(fresh_state, monkeypatch):
    """Two operations managers press Retry together, each request in its own transaction: one runs the pipeline,
    the other waits for it, then reads the requisition under its lock once the first has committed. (This held on
    cfdae75 too: it is the real-transaction version of the fake-run tests.)"""
    try:
        with _committed(monkeypatch, "REQ-NEW2"):
            set_gateway(LlmGateway(FailingFor({"protocol_suggest"})))
            with unit_of_work() as store:
                ondemand.run_now(store, "requisition", "REQ-NEW2", trigger="opened")
            with unit_of_work() as store:
                assert requisitions.retryable(store, store.requisitions["REQ-NEW2"])
                before = _calls(store)
            set_gateway(LlmGateway(MockProvider(latency_s=0.3)))

            def retry():
                with unit_of_work() as store:
                    ondemand.run_now(store, "requisition", "REQ-NEW2", trigger="asked", retry=True)

            first, second = threading.Thread(target=retry), threading.Thread(target=retry)
            first.start()
            for _ in range(100):
                if ondemand.live("requisition", "REQ-NEW2"):
                    break
                time.sleep(0.02)
            second.start()
            first.join(30)
            second.join(30)
            with unit_of_work() as store:
                assert sum((_calls(store) - before).values()) == 3  # extraction, triage, protocol: once
                assert store.requisitions["REQ-NEW2"].status == "ready"
                assert protocols.records(store)["REQ-NEW2"].ai_status == "ok"
    finally:
        set_ambient_store(fresh_state)


def test_a_reprocessed_requisitions_job_runs_after_the_commit(fresh_state, client, login, monkeypatch):
    """Reprocess (operations) queues the pipeline once its request commits, as a submitted requisition does (this
    held on cfdae75 too)."""
    try:
        with _committed(monkeypatch, "REQ-NEW1"):
            ran = _jobs_run_at_once(monkeypatch)
            assert client.post("/api/requisitions/REQ-NEW1/reprocess", headers=login("U-OPS")).status_code == 200
            with unit_of_work() as store:
                assert ran == ["REQ-NEW1"] and store.requisitions["REQ-NEW1"].status == "ready"
    finally:
        set_ambient_store(fresh_state)


def test_an_item_the_job_loop_took_but_has_not_started_runs_as_what_it_was_queued_for(fresh_state):
    """A submitted requisition is queued after its commit; the loop takes the job and, while its transaction
    starts, the page that follows the submission opens the requisition. The opening runs it as the submission. On
    cfdae75 the job was off the queue and not running yet: invisible (`live` None), and the opening ran it as its
    own (here, outside a request, "background"; in the browser "when you opened it", which the pipeline e2e spec
    caught once, after this round's budget reads had widened the gap)."""
    store = fresh_state
    assert ondemand.request("requisition", "REQ-NEW1", causes.AiCause(trigger="action", note="submitted in the portal"))
    job = ondemand.next_job()  # the loop has it; its transaction is starting
    assert (ondemand.live("requisition", "REQ-NEW1") or {}).get("state") == "queued"
    assert not ondemand.request("requisition", "REQ-NEW1")  # never queued twice
    before = _calls(store)
    ondemand.run_now(store, "requisition", "REQ-NEW1", trigger="opened")  # the page's opening POST
    ondemand.run_job(store, job)  # the loop's run then finds it done
    assert sum((_calls(store) - before).values()) == 3
    runs = [t for t in traces.table(store).values() if (t.cause or {}).get("item_id") == "REQ-NEW1"]
    assert len(runs) == 3 and {t.cause["trigger"] for t in runs} == {"action"}
    assert ondemand.live("requisition", "REQ-NEW1") is None and ondemand.snapshot()["queued"] == []


def test_the_eager_steps_pass_over_an_item_they_cannot_run_now(fresh_state, monkeypatch):
    """On cfdae75 the eager commentary only ever tried the top pending review: one without an encounter (never
    commentable) or held by another transaction (a pharmacist deciding it) stopped the background's commentary."""
    _mode(monkeypatch, "eager")
    store = fresh_state
    med_safety.intake(store, force=True)
    top, held, third = med_safety.pending_commentary(store)[:3]
    reviews(store)[review_key(top.order_ref)] = top.model_copy(update={"encounter_id": None})
    settling = med_safety.pending_settlement(store)[:2]
    other = get_engine().connect()
    tx = other.begin()
    try:
        for name in (held.id, settling[0].id):  # another transaction holds them
            other.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": med_safety._lock_key(name)})
        before = _calls(store)
        med_safety.commentary_step(store)
        med_safety.settle_step(store)
        # one item each; it may end without a model call (no consent: refused; a reconciliation with nothing to judge)
        new = _calls(store) - before
        assert new["order_review"] <= 1 and set(new) <= {"order_review", "medication_reconciliation"}
        assert get_review(store, held.id).commentary_status == "pending"
        assert get_review(store, third.id).commentary_status != "pending"
        state = [sessions(store).get(session_key(s.encounter_id, s.kind)).ai_status for s in settling]
        assert state[0] == "pending" and state[1] != "pending"
    finally:
        tx.rollback()
        other.close()
    assert top.id not in [r.id for r in med_safety.pending_commentary(store)]  # never the background's to comment


def test_a_storyline_steps_ask_ai_audits_each_item_it_refuses_when_others_were_asked(fresh_state, client, login,
                                                                                     monkeypatch):
    store = fresh_state
    med_safety.intake(store, force=True)
    review = next(r for r in reviews(store).values() if r.commentary_status == "on_open" and r.encounter_id
                  and "pharmacist" in r.reviewer_roles)
    session = next(s for s in sessions(store).values() if s.ai_status == "on_open")
    store.modules["storyline"] = {"prepared_at": datetime.now().isoformat()}
    monkeypatch.setattr(storyline, "_step_items", lambda s, key: [("order_review", review.id), ("medrec", session.id)])

    def outside(store, user, item_id):
        raise ondemand.NotAllowed("This patient is outside your units")

    monkeypatch.setitem(ondemand.KINDS, "medrec", dataclasses.replace(ondemand.kind("medrec"), check=outside))
    r = client.post("/api/storyline/steps/medrec/ai", headers=login("U-PHAR-01"))
    assert r.status_code == 200 and r.json()["items"] == [["order_review", review.id]]
    assert store.audit.query(resource_type="ai_medrec", resource_id=session.id, outcome="denied")
