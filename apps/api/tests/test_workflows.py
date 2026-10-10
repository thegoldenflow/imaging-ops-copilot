"""Durable workflows in Temporal's time-skipping test server (spec 6.5 acceptance).

A worker runs in the test process; events go through the real bridge (bus -> outbox -> dispatcher -> Temporal) and
everything the activities write is rolled back with the test (tests/temporal_harness.py).
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest

from app.agents import approvals, evalsets, reviews
from app.agents.runtime import runtime
from app.core.store import get_store
from app.ehr.consent import consent_status
from app.ehr.events import DomainEvent, bus
from app.ehr.gateway import Actor, FhirGateway
from app.workflows import bridge, faults, progress
from app.workflows import model as M
from tests.temporal_harness import DB, harness

PHARMACIST, PHYSICIAN, NURSE, CLERK, OPS = "U-PHAR-01", "U-DOC-09", "U-NURS-05", "U-CLER-01", "U-OPS"
LONG = {"signoff_timeout_s": 24 * 3600, "escalation_timeout_s": 24 * 3600, "followup_delay_s": 48 * 3600,
        "verify_after_s": 3600}


def _inpatient(store, *, unit="MEDA", consents=("ai_processing", "followup_call"), skip=0) -> tuple[dict, str]:
    """An inpatient on the unit (the demo physician's and nurse's), with these consents permitted."""
    fhir = FhirGateway(Actor.system("wp4c-test"), "workflow_engine")
    found = []
    for enc in sorted(store.fhir.search("Encounter", cls="IMP", status="in-progress", unit=unit), key=lambda e: e["id"]):
        bed = next((loc["location"]["reference"].split("/")[1] for loc in reversed(enc.get("location") or [])
                    if loc.get("status") == "active"), "")
        if bed.count("-") != 2:
            continue
        pid = enc["subject"]["reference"].split("/")[1]
        states = consent_status(fhir, pid)
        if all(states[c] == "permit" for c in consents):
            found.append((enc, pid))
    assert len(found) > skip, "no matching inpatient"
    return found[skip]


def _event(event_type: str, enc: dict, **attrs) -> DomainEvent:
    return DomainEvent(event_id=str(uuid.uuid4()), type=event_type, occurred_at=datetime(2026, 10, 9, 12, 0),
                       correlation_id=uuid.uuid4().hex, actor="system:test", source="test",
                       patient=enc["subject"]["reference"].split("/")[1], encounter=enc["id"],
                       refs={"encounter": f"Encounter/{enc['id']}", "patient": enc["subject"]["reference"]},
                       attrs={"encounter_class": "IMP", **attrs})


def _admit(h, enc: dict) -> str:
    """The admission as the bridge sees it (timers from bridge.timers(), which a test may patch)."""
    with DB:
        bus.publish(_event("patient.admitted", enc))
    h.pump()
    return M.journey_id(enc["id"])


def _timers(monkeypatch, **values) -> None:
    monkeypatch.setattr(bridge, "timers", lambda: {**LONG, **values})


def _user(uid: str):
    return get_store().staff.get(uid)


def _decide(ref: str, uid: str, decision: str = "approve") -> dict:
    with DB:
        out = approvals.decide(_user(uid), ref.split("/", 1)[1], decision, "test")
        if out["kind"] == "approval-request" and decision == "approve":
            runtime.execute_approved(out)  # as POST /api/agents/tasks/{id}/decision does
        return out


def _sign(ref: str, uid: str):
    with DB:
        return FhirGateway(Actor.of(_user(uid)), "signing").sign_document(ref.split("/", 1)[1])


def _documents(store, encounter_id: str, code: str) -> list[dict]:
    return [d for d in store.fhir.search("DocumentReference", encounter=encounter_id)
            if any(c.get("code") == code for c in (d.get("type") or {}).get("coding") or [])]


def test_journey_runs_from_admission_to_the_booked_follow_up_call(fresh_state, monkeypatch):
    store = fresh_state
    with harness(monkeypatch) as h:
        with DB:
            enc, pid = _inpatient(store)
        wf = _admit(h, enc)

        s = h.wait_step(wf, "orderReview", ("waiting",))
        assert h.step(wf, "triageAssist")["status"] in ("done", "skipped")
        _decide(s["waiting_for"]["refs"][0], PHARMACIST)

        s = h.wait_step(wf, "medRecAdmission", ("waiting",))
        doc = s["waiting_for"]["refs"][0]
        assert doc.startswith("DocumentReference/")
        _sign(doc, PHARMACIST)
        h.pump()
        with DB:
            assert h.step(wf, "medRecAdmission")["status"] == "waiting"  # co-signature still missing
        _sign(doc, PHYSICIAN)

        h.wait_step(wf, "wardMonitoring", ("waiting",))
        with DB:
            assert h.step(wf, "bedAssignment")["status"] == "done"
            bus.publish(_event("patient.discharged", enc))

        s = h.wait_step(wf, "draftDischargeSummary", ("waiting",))
        _sign(s["waiting_for"]["refs"][0], PHYSICIAN)
        s = h.wait_step(wf, "draftPatientInstructions", ("waiting",))
        _sign(s["waiting_for"]["refs"][0], NURSE)

        h.wait_step(wf, "followupDelay", ("waiting",))
        h.skip(48 * 3600 + 60)
        s = h.wait_step(wf, "scheduleFollowupCall", ("waiting",))
        approval = s["waiting_for"]["refs"][0]
        _decide(approval, CLERK)

        h.wait(lambda: progress.get(wf).status == "completed", what="the journey to complete")
        h.capture(wf, "journey")
        with DB:
            run = progress.get(wf)
            statuses = {s["key"]: s["status"] for s in run.steps}
            assert statuses["scheduleFollowupCall"] == "done" and statuses["codingStub"] == "done"
            assert all(v in ("done", "skipped") for v in statuses.values()), statuses
            appointment = next(r for r in h.step(wf, "scheduleFollowupCall")["refs"] if r.startswith("Appointment/"))
            assert store.fhir.read("Appointment", appointment.split("/")[1])["status"] == "booked"
            assert len(_documents(store, enc["id"], "medrec")) == 1
            completed = [e for e in bus.events(store, types=["workflow.step_completed"], limit=200)
                         if e.attrs.get("workflow_id") == wf]
            assert {e.attrs["step"] for e in completed} >= {"orderReview", "medRecAdmission", "wardMonitoring"}


def _agent_runs(store, encounter_id: str) -> dict[str, int]:
    from app.agents import trace as traces

    out: dict[str, int] = {}
    for t in traces.table(store).values():
        if t.encounter_id == encounter_id and t.kind == "run":
            out[t.agent_id] = out.get(t.agent_id, 0) + 1
    return out


def _activities(h, workflow_id: str) -> tuple[list[str], dict[str, list[int]]]:
    """Activity types in the order Temporal scheduled them, and the attempt each start of one was."""
    events = h.history(workflow_id).events
    scheduled = {e.event_id: e.activity_task_scheduled_event_attributes.activity_type.name for e in events
                 if e.HasField("activity_task_scheduled_event_attributes")}
    attempts: dict[str, list[int]] = {}
    for e in events:
        if e.HasField("activity_task_started_event_attributes"):
            a = e.activity_task_started_event_attributes
            attempts.setdefault(scheduled[a.scheduled_event_id], []).append(a.attempt)
    return list(scheduled.values()), attempts


def _activities_count(h, workflow_id: str, name: str) -> int:
    scheduled, _ = _activities(h, workflow_id)
    return scheduled.count(name)


def _to_medrec(h, store) -> tuple[dict, str, dict]:
    with DB:
        enc, _ = _inpatient(store)
    wf = _admit(h, enc)
    s = h.wait_step(wf, "orderReview", ("waiting",))
    _decide(s["waiting_for"]["refs"][0], PHARMACIST)
    return enc, wf, h.wait_step(wf, "medRecAdmission", ("waiting",), timeout=90)


def test_a_failed_medrec_activity_is_retried_and_leaves_exactly_one_medrec_document(fresh_state, monkeypatch):
    """6.5 acceptance: a failure injected into the MedRec activity after its draft was written; Temporal retries
    it (1 s, 4 s) and the Tool Gateway's idempotency key returns the first draft, so FHIR holds one."""
    store = fresh_state
    with harness(monkeypatch) as h:
        with DB:
            faults.inject("medRecAdmission", 2)
        enc, wf, s = _to_medrec(h, store)
        h.capture(wf, "journey_medrec_retry")
        with DB:
            assert s["attempts"] == 3
            docs = _documents(store, enc["id"], "medrec")
            assert len(docs) == 1
            assert s["waiting_for"]["refs"] == [f"DocumentReference/{docs[0]['id']}"]
            from app.agents import trace as traces

            calls = [c.status for t in traces.table(store).values() if t.agent_id == "medication_reconciliation"
                     and t.encounter_id == enc["id"] for c in t.tool_calls if c.tool_id == "draftDocument"]
            assert sorted(calls) == ["ok", "replayed", "replayed"]
            assert faults.pending() == {}
        _, attempts = _activities(h, wf)
        assert attempts["journey.medRecAdmission"] == [3]  # one scheduled activity, started on its third attempt


def test_a_restarted_worker_resumes_the_journey_without_running_completed_steps_again(fresh_state, monkeypatch):
    """6.5 acceptance: the worker dies while the journey waits for the MedRec signatures; people sign meanwhile;
    a new worker replays the history and goes on from there."""
    store = fresh_state
    with harness(monkeypatch) as h:
        enc, wf, s = _to_medrec(h, store)
        with DB:
            before = _agent_runs(store, enc["id"])
        assert before.get("order_review") == 1 and before.get("medication_reconciliation") == 1

        h.stop_worker()
        doc = s["waiting_for"]["refs"][0]
        _sign(doc, PHARMACIST)
        _sign(doc, PHYSICIAN)
        h.pump()  # the signals reach Temporal and wait there for a worker
        with DB:
            assert h.step(wf, "medRecAdmission")["status"] == "waiting"  # nobody ran the workflow meanwhile
        h.start_worker()  # a new worker with nothing in memory: it rebuilds the journey from its history

        h.wait_step(wf, "wardMonitoring", ("waiting",))
        with DB:
            after = _agent_runs(store, enc["id"])
            assert after["order_review"] == 1 and after["medication_reconciliation"] == 1
            assert len(_documents(store, enc["id"], "medrec")) == 1
        scheduled, _ = _activities(h, wf)
        for name in ("journey.context", "journey.triageAssist", "journey.orderReview", "journey.medRecAdmission"):
            assert scheduled.count(name) == 1, (name, scheduled)


def test_a_signoff_not_given_in_24_seconds_escalates_then_tells_the_operations_manager(fresh_state, monkeypatch):
    """6.5 acceptance: the 24-hour sign-off timeout, 24 seconds in the test: a high-priority Task for the signer,
    another 24 seconds later a Task for the operations manager, and the wait goes on."""
    store = fresh_state
    _timers(monkeypatch, signoff_timeout_s=24, escalation_timeout_s=24)
    with harness(monkeypatch) as h:
        with DB:
            enc, _ = _inpatient(store)
        wf = _admit(h, enc)
        s = h.wait_step(wf, "orderReview", ("waiting",))
        signoff = s["waiting_for"]["refs"][0]

        h.skip(25)
        s = h.wait(lambda: (x := h.step(wf, "orderReview")) and x["escalation"] >= 1 and x, what="the escalation")
        with DB:
            task = store.fhir.read("Task", next(t["task_id"] for t in s["tasks"] if t["kind"] == "escalation"))
            assert task["priority"] == "urgent" and task["status"] == "requested"
            assert task["code"]["coding"][0]["code"] == "workflow-escalation"
            assert task["performerType"][0]["coding"][0]["code"] == "pharmacist"
            assert task["focus"]["reference"] == signoff and task["encounter"]["reference"] == f"Encounter/{enc['id']}"
            assert "24 s" in task["description"]

        h.skip(25)
        s = h.wait(lambda: (x := h.step(wf, "orderReview")) and x["escalation"] >= 2 and x, what="the notice")
        with DB:
            note = store.fhir.read("Task", next(t["task_id"] for t in s["tasks"] if t["kind"] == "notify"))
            assert note["priority"] == "stat" and note["code"]["coding"][0]["code"] == "workflow-notify"
            assert note["performerType"][0]["coding"][0]["code"] == "ops"  # the operations manager
            assert s["status"] == "waiting"

        _decide(signoff, PHARMACIST)
        h.wait_step(wf, "orderReview", ("done",))
        h.wait_step(wf, "medRecAdmission", ("waiting",))
        with DB:  # the sign-off came: its escalation and notice are done
            for kind in ("escalation", "notify"):
                t = store.fhir.read("Task", next(t["task_id"] for t in s["tasks"] if t["kind"] == kind))
                assert t["status"] == "completed" and "Resolved" in t["note"][-1]["text"]


def test_a_failed_privileged_call_is_not_retried_and_waits_for_a_retry_or_skip(fresh_state, monkeypatch, client, login):
    """bookAppointment is privileged: its activity runs once; the failure becomes a Task for the clerk and the step
    waits for the operations manager (retry, then skip; both audited)."""
    store = fresh_state
    _timers(monkeypatch, followup_delay_s=60)
    with harness(monkeypatch) as h:
        enc, wf, s = _to_medrec(h, store)
        _sign(s["waiting_for"]["refs"][0], PHARMACIST)
        _sign(s["waiting_for"]["refs"][0], PHYSICIAN)
        h.wait_step(wf, "wardMonitoring", ("waiting",))
        with DB:
            bus.publish(_event("patient.discharged", enc))
        s = h.wait_step(wf, "draftDischargeSummary", ("waiting",))
        _sign(s["waiting_for"]["refs"][0], PHYSICIAN)
        s = h.wait_step(wf, "draftPatientInstructions", ("waiting",))
        _sign(s["waiting_for"]["refs"][0], NURSE)
        h.wait_step(wf, "followupDelay", ("waiting",))
        h.skip(61)
        s = h.wait_step(wf, "scheduleFollowupCall", ("waiting",))
        appointment = next(r for r in s["refs"] if r.startswith("Appointment/")).split("/")[1]
        with DB:  # the slot is gone before the clerk approves: the booking will fail
            appt = store.fhir.read("Appointment", appointment)
            appt["status"] = "cancelled"
            store.fhir.update(appt)
        _decide(s["waiting_for"]["refs"][0], CLERK)

        s = h.wait(lambda: (x := h.step(wf, "scheduleFollowupCall")) and x["status"] == "failed"
                   and any(t["kind"] == "failed" for t in x["tasks"]) and x, what="the failed booking's Task")
        assert s["attempts"] == 1
        with DB:
            failed = store.fhir.read("Task", next(t["task_id"] for t in s["tasks"] if t["kind"] == "failed"))
            assert failed["performerType"][0]["coding"][0]["code"] == "clerk" and failed["priority"] == "urgent"
        _, attempts = _activities(h, wf)
        assert attempts["journey.bookFollowupCall"] == [1]

        with DB:
            nurse = login(NURSE)
            assert client.post(f"/api/workflows/{wf}/steps/scheduleFollowupCall/retry", json={},
                               headers=nurse).status_code == 403
            ops = login(OPS)
            r = client.post(f"/api/workflows/{wf}/steps/scheduleFollowupCall/retry", json={"note": "slot reopened?"},
                            headers=ops)
            assert r.status_code == 202, r.text
        h.wait(lambda: _activities_count(h, wf, "journey.bookFollowupCall") == 2, what="the retried booking")
        h.wait(lambda: (x := h.step(wf, "scheduleFollowupCall")) and x["status"] == "failed"
               and x["detail"].startswith("Failed after"), what="the second failure")
        with DB:
            r = client.post(f"/api/workflows/{wf}/steps/scheduleFollowupCall/skip", json={"note": "booked by phone"},
                            headers=ops)
            assert r.status_code == 202, r.text
        h.wait(lambda: progress.get(wf).status == "completed", what="the journey to complete")
        with DB:
            assert h.step(wf, "scheduleFollowupCall")["status"] == "skipped"
            assert store.fhir.read("Task", failed["id"])["status"] == "completed"  # skipped: the failure Task is done
            actions = {e.action for e in store.audit.query(resource_type="Workflow", resource_id=wf)}
            assert {"workflow_retry", "workflow_skip"} <= actions


# ---------- CapacityExceptionWorkflow ----------


def _open_exceptions(h, store) -> list:
    """The Control Tower finds the seeded hospital's exceptions; the bridge starts a workflow for each."""
    from app.modules.control_tower import exceptions as X
    from app.modules.control_tower import service

    with DB:
        service.reset_state()
        service.refresh(store, force=True)
        found = [e for e in X.stream(store) if e.status == "open"]
    assert len(found) >= 3, "the seeded hospital should have exceptions at 07:00"
    for exc in found:
        h.wait_step(M.capacity_id(exc.id), "decision", ("waiting",), timeout=90)
    return found


def _flow_tasks(store, exception_id: str) -> list[dict]:
    return [t for t in store.fhir.search("Task", code="flow-action")
            if any(i.get("valueString") == exception_id for i in t.get("input") or [])]


def test_capacity_workflow_executes_the_approval_and_checks_the_occupancy_an_hour_later(fresh_state, monkeypatch):
    """detect -> explain (narrator) -> approved -> execute (createFlowTask; a failure after the Tasks were written
    is retried and replays them) -> 1 h -> verify -> outcome on the exception."""
    from app.modules.control_tower import exceptions as X
    from app.modules.control_tower import service

    store = fresh_state
    with harness(monkeypatch) as h:
        exc = _open_exceptions(h, store)[0]
        wf = M.capacity_id(exc.id)
        with DB:
            exc = X.get(store, exc.id)
            assert exc.narration_status == "done" and h.step(wf, "explainAndRecommend")["status"] == "done"
            faults.inject("execute", 1)
            actions = [a["action_id"] for a in exc.narration["recommended_actions"]]
            decided = service.approve(store, _user(OPS), exc.id, actions, "approved in the test")
            assert decided.decision["executed_by"] == "workflow" and decided.decision["workflow_id"] == wf
            assert all(a["status"] == "queued" and a["task_id"] is None for a in decided.decision["actions"])

        h.wait_step(wf, "verifyDelay", ("waiting",), timeout=90)
        with DB:
            exc = X.get(store, exc.id)
            assert all(a["task_id"] for a in exc.decision["actions"]) and exc.decision["execution_run"]
            assert h.step(wf, "execute")["attempts"] == 2
            tasks = _flow_tasks(store, exc.id)
            assert len(tasks) == len(actions)  # the retried execution wrote each Task once
        h.skip(3601)
        h.wait(lambda: progress.get(wf).status == "completed", what="the capacity workflow to complete")
        h.capture(wf, "capacity")
        with DB:
            exc = X.get(store, exc.id)
            assert exc.outcome["decision"] == "approved" and exc.outcome["verified"]["tasks"]
            assert exc.outcome["resolved"] is not None
            assert {s["key"]: s["status"] for s in progress.get(wf).steps}["verify"] == "done"


def test_rejected_and_cleared_exceptions_record_their_outcome_too(fresh_state, monkeypatch):
    from app.modules.control_tower import exceptions as X
    from app.modules.control_tower import rules, service
    from app.modules.control_tower.snapshot import cache

    store = fresh_state
    with harness(monkeypatch) as h:
        found = _open_exceptions(h, store)
        rejected, cleared = found[0], found[1]
        with DB:
            service.reject(store, _user(OPS), rejected.id, "Overflow beds are staffed already")
            snap = cache.get(store)
            X.sync(store, [d for d in rules.evaluate(snap) if d.key != cleared.key], snap.now)
        for exc, decision in ((rejected, "rejected"), (cleared, "cleared")):
            wf = M.capacity_id(exc.id)
            h.wait(lambda wf=wf: progress.get(wf).status == "completed", what=f"{wf} to complete")
            with DB:
                assert X.get(store, exc.id).outcome["decision"] == decision
                steps = {s["key"]: s["status"] for s in progress.get(wf).steps}
                assert steps["execute"] == "skipped" and steps["recordOutcome"] == "done"
                assert not _flow_tasks(store, exc.id)
        h.capture(M.capacity_id(rejected.id), "capacity_rejected")


# ---------- LowConfidenceReviewWorkflow ----------


def test_low_confidence_output_is_corrected_added_as_an_eval_case_and_the_regression_runs(fresh_state, monkeypatch,
                                                                                          tmp_path):
    """6.5 acceptance: a person corrects a low-confidence triage; the eval set gains a human_review case and the
    agent's regression eval runs over it."""
    import shutil

    from app.agents.library import patient_message_triage as triage

    store = fresh_state
    starter = evalsets.cases_path("patient_message_triage")
    (tmp_path / "patient_message_triage").mkdir()
    if starter.exists():
        shutil.copy(starter, tmp_path / "patient_message_triage" / "cases.jsonl")
    authored = len(evalsets.load("patient_message_triage"))
    with evalsets.use_root(tmp_path), harness(monkeypatch) as h:
        with DB:
            enc, _ = _inpatient(store, consents=("ai_processing",))
            with runtime.start(triage.AGENT_ID, user=_user(NURSE), encounter_id=enc["id"]) as run:
                result = triage.run(run, "I think I left my reading glasses in the room, can someone keep them?")
            assert result.category == "other" and result.confidence < 0.7
            review = next(r for r in reviews.table(store).values() if r.run_id == result.run_id)
            assert review.input["message"].startswith("<untrusted") and review.output["category"] == "other"
        wf = M.review_id(result.run_id)
        s = h.wait_step(wf, "reviewed", ("waiting",))
        with DB:
            task = store.fhir.read("Task", s["waiting_for"]["refs"][0].split("/")[1])
            assert task["code"]["coding"][0]["code"] == "ai-review"
            assert task["performerType"][0]["coding"][0]["code"] == "nurse"
            reviews.correct(_user(NURSE), review.id, {"category": "administrative"}, "belongings")
        h.wait(lambda: progress.get(wf).status == "completed", what="the review workflow to complete")
        h.capture(wf, "review")
        with DB:
            cases = evalsets.load("patient_message_triage")
            assert len(cases) == authored + 1
            case = cases[-1]
            assert case["source"] == "human_review" and case["id"] == f"hr-{result.run_id.lower()}"
            assert case["expected"] == {"category": "administrative", "urgency": result.urgency}
            assert case["model_output"]["category"] == "other"
            report = __import__("json").loads((tmp_path / "patient_message_triage" / "report.json").read_text("utf-8"))
            assert report["cases"] == authored + 1 and any(r["id"] == case["id"] for r in report["results"])
            final = reviews.get(review.id)
            assert final.status == "learned" and final.case_id == case["id"] and final.regression["cases"] == authored + 1
            from app.agents import trace as traces

            assert traces.get(result.run_id).human_action["decision"] == "corrected"


def test_a_journey_whose_encounter_is_gone_ends_without_asking_anyone(fresh_state, monkeypatch):
    """A demo reset regenerates the data under running workflows: a step that finds its subject gone ends the
    workflow as failed instead of opening a Task in the new data."""
    store = fresh_state
    with harness(monkeypatch) as h:
        with DB:
            enc, _ = _inpatient(store)
        wf = _admit(h, enc)
        s = h.wait_step(wf, "orderReview", ("waiting",))
        with DB:
            store.fhir.delete("Encounter", enc["id"])
        _decide(s["waiting_for"]["refs"][0], PHARMACIST)
        h.wait(lambda: progress.get(wf).status == "failed", what="the journey to end")
        with DB:
            step = h.step(wf, "medRecAdmission")
            assert step["status"] == "failed" and "gone" in step["detail"] and step["tasks"] == []
            assert not [t for t in store.fhir.search("Task", code="workflow-failed")]
