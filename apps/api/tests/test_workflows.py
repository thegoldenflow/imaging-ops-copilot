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
from app.workflows.activities import StepError, step_activity
from tests import workflow_probe
from tests.temporal_harness import DB, harness

PHARMACIST, PHYSICIAN, NURSE, CLERK, OPS = "U-PHAR-01", "U-DOC-09", "U-NURS-05", "U-CLER-01", "U-OPS"
LONG = {"signoff_timeout_s": 24 * 3600, "escalation_timeout_s": 24 * 3600, "followup_delay_s": 48 * 3600,
        "verify_after_s": 3600}


def _inpatient(store, *, unit="MEDA", consents=("ai_processing", "followup_call"), skip=0,
               leaving: bool = False) -> tuple[dict, str]:
    """An inpatient on the unit (the demo physician's and nurse's), with these consents permitted (`leaving`: with
    discharge prescriptions, so WP6's discharge reconciliation and WP7's discharge summary can be done)."""
    fhir = FhirGateway(Actor.system("wp4c-test"), "workflow_engine")
    found = []
    for enc in sorted(store.fhir.search("Encounter", cls="IMP", status="in-progress", unit=unit), key=lambda e: e["id"]):
        bed = next((loc["location"]["reference"].split("/")[1] for loc in reversed(enc.get("location") or [])
                    if loc.get("status") == "active"), "")
        if bed.count("-") != 2:
            continue
        pid = enc["subject"]["reference"].split("/")[1]
        states = consent_status(fhir, pid)
        if leaving and not any(m.get("intent") == "plan" and m.get("status") == "active"
                               for m in store.fhir.search("MedicationRequest", encounter=enc["id"])):
            continue
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


def _sign_medrec(ref: str, uid: str):
    """The MedRec record signed where people sign it (WP6's workbench): the pharmacist handles every discrepancy and
    signs, then a physician co-signs."""
    from app.modules.med_safety import service as med_safety
    from app.modules.med_safety.models import sessions

    with DB:
        store, user = get_store(), _user(uid)
        session = sessions(store).find_by("document_id", ref.split("/", 1)[1])[0]
        if uid == PHARMACIST:
            for item in med_safety.unhandled(session):
                session = med_safety.handle_item(store, user, session.id, item, "accept", "Checked with the patient")
        return med_safety.sign(store, user, session.id)


def _sign_discharge_medrec(enc: dict) -> None:
    """WP6's discharge reconciliation, so the discharge summary has its signed discharge medication table."""
    from app.modules.med_safety import service as med_safety

    with DB:
        store = get_store()
        session = med_safety.settle(store, med_safety.reconcile(store, enc["id"], "discharge").id)
        for item in med_safety.unhandled(session):
            session = med_safety.handle_item(store, _user(PHARMACIST), session.id, item, "accept", "Intended")
        med_safety.sign(store, _user(PHARMACIST), session.id)
        med_safety.sign(store, _user(PHYSICIAN), session.id)


def _document_step(ref: str, uid: str):
    """A WP7 step: the draft signed in Documentation (the validator re-checks it), or the by-hand Task confirmed."""
    from app.modules.documentation import service as documentation

    if ref.startswith("Task/"):
        return _decide(ref, uid)
    with DB:
        store, user, doc_id = get_store(), _user(uid), ref.split("/", 1)[1]
        view = documentation.document_view(store, user, doc_id)
        if view["blocking"]:  # an untraceable item (the planted note's medicine): the physician removes it
            content = view["content"]
            for issue in sorted({i["path"] for i in view["validation"]["issues"] if i["severity"] == "red"},
                                reverse=True):
                section, _, index = issue.rstrip("]").rpartition("[")
                group = content
                for part in section.split("."):
                    group = group[part]
                del group[int(index)]
            documentation.save(store, user, doc_id, content)
        return documentation.sign(store, user, doc_id)


def _documents(store, encounter_id: str, code: str) -> list[dict]:
    return [d for d in store.fhir.search("DocumentReference", encounter=encounter_id)
            if any(c.get("code") == code for c in (d.get("type") or {}).get("coding") or [])]


def _ensure_phone(store, patient_id: str) -> None:
    """7.4 calls only a patient with a phone number on file."""
    patient = store.fhir.read("Patient", patient_id)
    if not any(t.get("system") == "phone" for t in patient.get("telecom") or []):
        patient["telecom"] = [{"system": "phone", "value": "416-555-0199", "use": "home"}]
        store.fhir.update(patient)


def test_journey_runs_from_admission_to_the_scheduled_follow_up_call(fresh_state, monkeypatch):
    from app.modules.voice_services import followup

    store = fresh_state
    with harness(monkeypatch) as h:
        with DB:
            enc, pid = _inpatient(store, leaving=True)
            _ensure_phone(store, pid)
        wf = _admit(h, enc)

        s = h.wait_step(wf, "orderReview", ("waiting",))
        assert h.step(wf, "triageAssist")["status"] in ("done", "skipped")
        _decide(s["waiting_for"]["refs"][0], PHARMACIST)

        s = h.wait_step(wf, "medRecAdmission", ("waiting",))
        doc = s["waiting_for"]["refs"][0]
        assert doc.startswith("DocumentReference/")
        _sign_medrec(doc, PHARMACIST)
        h.pump()
        with DB:
            assert h.step(wf, "medRecAdmission")["status"] == "waiting"  # co-signature still missing
        _sign_medrec(doc, PHYSICIAN)

        h.wait_step(wf, "wardMonitoring", ("waiting",))
        _sign_discharge_medrec(enc)
        with DB:
            assert h.step(wf, "bedAssignment")["status"] == "done"
            bus.publish(_event("patient.discharged", enc))

        # WP7: the AI draft of the discharge summary, signed by the physician; the instructions from it, by a nurse
        s = h.wait_step(wf, "draftDischargeSummary", ("waiting",))
        assert s["waiting_for"]["refs"] == [f"DocumentReference/dsum-{enc['id']}"]
        _document_step(s["waiting_for"]["refs"][0], PHYSICIAN)
        s = h.wait_step(wf, "draftPatientInstructions", ("waiting",))
        assert s["waiting_for"]["refs"] == [f"DocumentReference/pinstr-{enc['id']}"]
        _document_step(s["waiting_for"]["refs"][0], NURSE)

        h.wait_step(wf, "followupDelay", ("waiting",))
        h.skip(48 * 3600 + 60)
        # WP8 (7.4): the four conditions hold (discharged, consent, phone, signed instructions): the call is scheduled
        # by the follow-up service itself, no booking and no clerk
        h.wait(lambda: progress.get(wf).status == "completed", what="the journey to complete")
        h.capture(wf, "journey")
        with DB:
            run = progress.get(wf)
            statuses = {s["key"]: s["status"] for s in run.steps}
            assert statuses["scheduleFollowupCall"] == "done" and statuses["codingStub"] == "done"
            assert all(v in ("done", "skipped") for v in statuses.values()), statuses
            comm = next(r for r in h.step(wf, "scheduleFollowupCall")["refs"] if r.startswith("Communication/"))
            call = followup.get(followup.call_id_for(enc["id"]))
            assert call.status == "scheduled" and call.source == "journey" and call.workflow_id == wf
            assert f"Communication/{call.communication_id}" == comm and call.script[0]["key"] == "identity"
            assert store.fhir.read("Communication", call.communication_id)["status"] == "preparation"
            assert not any(a.get("status") == "proposed" for a in store.fhir.search("Appointment", patient=pid)
                           if (a.get("serviceType") or [{}])[0].get("coding", [{}])[0].get("code") == "followup-call")
            assert [d["id"] for d in _documents(store, enc["id"], "medrec")
                    if d["id"].endswith("-admission")] == [f"medrec-{enc['id']}-admission"]  # (and the discharge one)
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
    it (1 s, 4 s) and the Tool Gateway's idempotency key returns the first draft, so FHIR holds one. The retries
    do not ask the model again: they send the stored reconciliation's draft (WP6), which the gateway replays."""
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
                     and t.encounter_id == enc["id"] for c in t.tool_calls if c.tool_id == "draftMedRec"]
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
        _sign_medrec(doc, PHARMACIST)
        _sign_medrec(doc, PHYSICIAN)
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


def test_the_pharmacists_last_order_decision_in_the_workbench_signs_off_the_order_review(fresh_state, monkeypatch):
    """WP6 in the journey: the order review step reviews the stay's orders by the rules and waits for the
    pharmacist's sign-off Task; deciding the last flagged medication order in Medication safety confirms it, and the
    reconciliation that follows writes the stay's own MedRec record."""
    from app.modules.med_safety import service as med_safety
    from app.modules.med_safety.models import reviews

    store = fresh_state
    with harness(monkeypatch) as h:
        with DB:  # the stay with the planted penicillin-allergy order: a blocking flag
            order = store.fhir.read("MedicationRequest", "medrx-ms-allergy-piperacillin")
            enc = store.fhir.read("Encounter", order["encounter"]["reference"].split("/")[1])
        wf = _admit(h, enc)
        s = h.wait_step(wf, "orderReview", ("waiting",))
        signoff = s["waiting_for"]["refs"][0]
        with DB:
            task = store.fhir.read("Task", signoff.split("/")[1])
            assert task["code"]["coding"][0]["code"] == "workflow-signoff"
            assert task["performerType"][0]["coding"][0]["code"] == "pharmacist"
            assert task["priority"] == "stat" and "1 blocking" in task["description"], task["description"]
            assert "MedicationRequest/medrx-ms-allergy-piperacillin" in s["refs"]
            pending = sorted((r for r in reviews(store).find_by("encounter_id", enc["id"])
                              if r.status == "pending" and r.kind == "medication"), key=lambda r: not r.blocking)
            assert pending and pending[0].order_ref == "MedicationRequest/medrx-ms-allergy-piperacillin"
        for n, review in enumerate(pending, 1):
            with DB:
                med_safety.decide_review(store, _user(PHARMACIST), review.id,
                                         "request_change" if review.blocking else "accept_order",
                                         "Reviewed with the prescriber")
                status = store.fhir.read("Task", signoff.split("/")[1])["status"]
                assert status == ("completed" if n == len(pending) else "requested")
        h.wait_step(wf, "orderReview", ("done",))
        s = h.wait_step(wf, "medRecAdmission", ("waiting",), timeout=90)
        with DB:
            task = store.fhir.read("Task", signoff.split("/")[1])
            decided = {o["type"]["text"]: o["valueString"] for o in task["output"]}
            assert decided["decision"] == "approve" and decided["decided_by"] == PHARMACIST
            assert "Medication safety" in decided["note"]
            assert s["waiting_for"]["refs"] == [f"DocumentReference/medrec-{enc['id']}-admission"]
            doc = store.fhir.read("DocumentReference", f"medrec-{enc['id']}-admission")
            assert doc["docStatus"] == "preliminary" and len(_documents(store, enc["id"], "medrec")) == 1


@step_activity(workflow_probe.ACTIVITY, workflow_probe.STEP, max_attempts=M.PRIVILEGED_MAX_ATTEMPTS)
def _probe_privileged_call(args: dict) -> dict:
    """A real privileged call that the Tool Gateway refuses: a booking whose approval record is not one."""
    with runtime.start("workflow_engine", user=None, encounter_id=args["encounter_id"],
                       context_id=args["workflow_id"]) as run:
        proposed = run.tool("proposeAppointment", {"encounter_id": args["encounter_id"], "service": "probe-booking",
                                                   "start": "2030-01-07T09:00", "minutes": 15})
        booked = run.tool("bookAppointment", {"appointment_id": proposed.output["appointment_id"],
                                              "approval_task_id": "not-an-approval"})
        run.finish(outcome=booked.status)
    if not booked.ok:
        raise StepError(f"bookAppointment {booked.status}: {booked.reason}")
    return {"detail": "booked"}


def test_a_failed_privileged_call_is_not_retried_and_waits_for_a_retry_or_skip(fresh_state, monkeypatch, client, login):
    """6.5: an activity that calls a privileged tool runs once; the failure becomes an urgent Task for the step's
    owner and the step waits for the operations manager (retry, then skip; both audited). Since WP8 no journey step
    is privileged (7.4 places the follow-up call without a booking), so a probe workflow on the same FlowBase
    machinery runs the step (tests/workflow_probe.py)."""
    from app.workflows import worker
    from tests.temporal_harness import TASK_QUEUE

    store = fresh_state
    monkeypatch.setattr(worker, "WORKFLOWS", [*worker.WORKFLOWS, workflow_probe.PrivilegedStepProbe])
    monkeypatch.setattr(worker, "ACTIVITIES", [*worker.ACTIVITIES, _probe_privileged_call])
    with harness(monkeypatch) as h:
        with DB:
            enc, pid = _inpatient(store)
        wf = f"journey-probe-{uuid.uuid4().hex[:8]}"
        h.loop.run(h.client.start_workflow(workflow_probe.PROBE, {"encounter_id": enc["id"], "patient_id": pid},
                                           id=wf, task_queue=TASK_QUEUE))
        step = workflow_probe.STEP
        s = h.wait(lambda: (x := h.step(wf, step)) and x["status"] == "failed"
                   and any(t["kind"] == "failed" for t in x["tasks"]) and x, what="the failed call's Task")
        assert s["attempts"] == 1 and "approval_invalid" in s["detail"]
        with DB:
            failed = store.fhir.read("Task", next(t["task_id"] for t in s["tasks"] if t["kind"] == "failed"))
            assert failed["performerType"][0]["coding"][0]["code"] == "clerk" and failed["priority"] == "urgent"
            assert "never retried automatically" in failed["description"]
        _, attempts = _activities(h, wf)
        assert attempts[workflow_probe.ACTIVITY] == [1]

        with DB:
            nurse = login(NURSE)
            assert client.post(f"/api/workflows/{wf}/steps/{step}/retry", json={}, headers=nurse).status_code == 403
            ops = login(OPS)
            r = client.post(f"/api/workflows/{wf}/steps/{step}/retry", json={"note": "approval fixed?"}, headers=ops)
            assert r.status_code == 202, r.text
        h.wait(lambda: _activities_count(h, wf, workflow_probe.ACTIVITY) == 2, what="the retried call")
        h.wait(lambda: (x := h.step(wf, step)) and x["status"] == "failed" and x["detail"].startswith("Failed after"),
               what="the second failure")
        with DB:
            r = client.post(f"/api/workflows/{wf}/steps/{step}/skip", json={"note": "booked by phone"}, headers=ops)
            assert r.status_code == 202, r.text
        h.wait(lambda: progress.get(wf).status == "completed", what="the probe to complete")
        with DB:
            assert h.step(wf, step)["status"] == "skipped"
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
            final = reviews.get(review.id)
            assert final.status == "learned" and final.case_id == case["id"] and final.regression["cases"] == authored + 1
            # the regression ran through the one eval runner (6.6): a run file next to the eval set
            run = __import__("json").loads((tmp_path / "runs" / f"{final.regression['run_id']}.json").read_text("utf-8"))
            entry = run["agents"]["patient_message_triage"]
            assert run["trigger"] == f"regression:{result.run_id}" and entry["metrics"]["cases"] == authored + 1
            assert any(c["id"] == case["id"] and c["source"] == "human_review" for c in entry["case_results"])
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
