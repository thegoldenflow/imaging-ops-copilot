"""The EventBus <-> Temporal bridge, the workflow API and the platform events behind them (spec 6.5), without a
Temporal server: a fake link stands in for Temporal, or none at all (TEMPORAL_ADDRESS unset: offline)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.agents import approvals, registry, reviews
from app.agents.runtime import runtime
from app.core.store import get_store
from app.ehr.consent import consent_status
from app.ehr.events import DomainEvent, bus
from app.ehr.gateway import Actor, FhirGateway
from app.modules.control_tower import exceptions as X
from app.modules.control_tower import service
from app.workflows import bridge, client, faults, progress
from app.workflows import model as M
from app.workflows.tables import workflow_commands

OPS, ADMIN, NURSE, PHYSICIAN, PHARMACIST, CLERK = "U-OPS", "U-ADMIN", "U-NURS-05", "U-DOC-09", "U-PHAR-01", "U-CLER-01"


class FakeLink(client.TemporalLink):
    """Temporal as the bridge sees it: starts and signals recorded; `down` makes it unreachable."""

    def __init__(self) -> None:
        super().__init__("fake:7233", "hospital-demo", "test")
        self.calls: list[tuple] = []
        self.running: set[str] = set()
        self.down = False

    def _check(self) -> None:
        if self.down:
            raise client.TemporalUnavailable("connection refused")

    def start(self, workflow_type, workflow_id, arg):
        self._check()
        self.calls.append(("start", workflow_id, arg))
        if workflow_id in self.running:
            return "duplicate"
        self.running.add(workflow_id)
        return "started"

    def signal(self, workflow_id, name, payload):
        self._check()
        self.calls.append(("signal", workflow_id, name, payload))
        return "sent" if workflow_id in self.running else "not_found"

    def status(self) -> dict:
        return {"configured": True, "online": not self.down, "workers": 1, "address": self.address,
                "namespace": self.namespace, "task_queue": self.task_queue, "error": None}

    def describe(self, workflow_id):
        return {"status": "RUNNING", "pending_activities": []}


@pytest.fixture
def link():
    fake = FakeLink()
    with client.use(fake):
        bridge.install()
        yield fake
        bridge.uninstall()


def _event(event_type: str, *, encounter: str | None = "stay-00001", patient: str | None = "pat-0001",
           refs: dict | None = None, **attrs) -> DomainEvent:
    base_refs = {**({"encounter": f"Encounter/{encounter}"} if encounter else {}),
                 **({"patient": f"Patient/{patient}"} if patient else {})}
    return DomainEvent(event_id=str(uuid.uuid4()), type=event_type, occurred_at=datetime(2026, 10, 9, 9, 30),
                       correlation_id="c", actor="system:test", source="test", patient=patient, encounter=encounter,
                       refs={**base_refs, **(refs or {})}, attrs=attrs)


def _commands(store) -> list:
    return store.conn().execute(select(workflow_commands).order_by(workflow_commands.c.seq)).all()


def _user(uid: str):
    return get_store().staff.get(uid)


def _imp_encounter(store, unit="MEDA") -> dict:
    return sorted(store.fhir.search("Encounter", cls="IMP", status="in-progress", unit=unit), key=lambda e: e["id"])[0]


# ---------- offline ----------


def test_without_temporal_the_bridge_is_off_and_the_view_says_offline(fresh_state, client, login):
    assert client is not None and bridge.install() is False  # TEMPORAL_ADDRESS is not set in the tests
    assert not any(s.consumer == bridge.CONSUMER for s in bus.subscriptions())
    assert bridge.dispatch_step(fresh_state) == 0
    status = client.get("/api/workflows/status", headers=login(OPS)).json()
    assert status["configured"] is False and status["online"] is False and "TEMPORAL_ADDRESS" in status["error"]
    assert client.get("/api/workflows", headers=login(OPS)).json()["workflows"] == []
    enc = _imp_encounter(fresh_state)
    assert client.post("/api/workflows/journeys", json={"encounter_id": enc["id"]},
                       headers=login(OPS)).status_code == 409
    assert bridge.running_capacity_workflow(fresh_state, "EXC-00001") is None


# ---------- events -> commands ----------


def test_domain_events_map_to_workflow_starts_and_signals(fresh_state):
    store = fresh_state
    start = bridge.commands_for(_event("patient.admitted", encounter_class="IMP"))
    assert [(c["kind"], c["workflow_id"], c["workflow_type"]) for c in start] == [
        ("start", "journey-stay-00001", M.JOURNEY)]
    assert start[0]["payload"]["timers"]["signoff_timeout_s"] == 24 * 3600
    assert start[0]["payload"]["encounter_id"] == "stay-00001" and start[0]["payload"]["started_by"]
    assert bridge.commands_for(_event("patient.admitted", encounter="ed-00001", encounter_class="EMER")) == []
    [discharged] = bridge.commands_for(_event("patient.discharged", encounter_class="IMP", disposition="home"))
    assert discharged["signal"] == M.SIGNAL_EVENT and discharged["payload"]["kind"] == "discharged"
    [news2] = bridge.commands_for(_event("flag.raised", refs={"flag": "Flag/f-1"}, code="news2"))
    assert news2["payload"] == {**news2["payload"], "kind": "news2", "ref": "Flag/f-1"}
    assert bridge.commands_for(_event("flag.raised", refs={"flag": "Flag/f-2"}, code="fall-risk")) == []

    signed = _event("document.signed", refs={"document": "DocumentReference/doc-9"}, role="pharmacist", final=False)
    assert bridge.commands_for(signed) == []  # nobody waits for it
    bridge.register_wait("DocumentReference/doc-9", "journey-stay-00001", "medRecAdmission", store)
    [cmd] = bridge.commands_for(signed)
    assert cmd["workflow_id"] == "journey-stay-00001" and cmd["payload"]["final"] is False
    bridge.register_wait("Task/t-7", "review-RUN-00001", "reviewed", store)
    [cmd] = bridge.commands_for(_event("task.decided", refs={"task": "Task/t-7"}, decision="accept", role="nurse"))
    assert cmd["workflow_id"] == "review-RUN-00001" and cmd["payload"]["decision"] == "accept"

    loc = {"location": "Location/MEDA"}
    [opened] = bridge.commands_for(_event("exception.opened", encounter=None, patient=None, refs=loc,
                                          exception_id="EXC-00004"))
    assert (opened["kind"], opened["workflow_id"]) == ("start", "capacity-EXC-00004")
    [decided] = bridge.commands_for(_event("exception.decided", encounter=None, patient=None, refs=loc,
                                           exception_id="EXC-00004", decision="approved"))
    assert decided["payload"]["ref"] == "FlowException/EXC-00004" and decided["payload"]["decision"] == "approved"
    assert bridge.commands_for(_event("exception.cleared", encounter=None, patient=None, refs=loc,
                                      exception_id="EXC-00004", decided=True)) == []
    [low] = bridge.commands_for(_event("agent.low_confidence", agent_id="patient_message_triage", run_id="RUN-00009",
                                       review_id="REV-00001"))
    assert (low["workflow_id"], low["payload"]["review_id"]) == ("review-RUN-00009", "REV-00001")


def test_the_outbox_takes_each_event_once_and_sends_in_order(fresh_state, link):
    store = fresh_state
    admitted = _event("patient.admitted", encounter_class="IMP")
    discharged = _event("patient.discharged", encounter_class="IMP")
    bus.publish_many([admitted, discharged])
    bus.drain()
    assert bridge.handle(admitted) is None  # delivered again (e.g. after a rollback): insert-first adds nothing
    rows = _commands(store)
    assert [(r.kind, r.status) for r in rows] == [("start", "pending"), ("signal", "pending")]
    assert bridge.dispatch_step(store) == 2
    assert [c[0] for c in link.calls] == ["start", "signal"]
    assert [r.status for r in _commands(store)] == ["sent", "sent"]
    assert bridge.dispatch_step(store) == 0
    again = _event("patient.admitted", encounter_class="IMP")  # the same encounter admitted again: same business key
    bus.publish(again)
    bus.drain()
    bridge.dispatch_step(store)
    assert _commands(store)[-1].status == "duplicate"


def test_commands_wait_while_temporal_is_unreachable_and_go_out_when_it_is_back(fresh_state, link):
    store = fresh_state
    link.down = True
    bus.publish_many([_event("patient.admitted", encounter_class="IMP"),
                      _event("patient.admitted", encounter="stay-00002", encounter_class="IMP")])
    bus.drain()
    assert bridge.dispatch_step(store) == 0
    first, second = _commands(store)
    assert first.status == "pending" and first.attempts == 1 and first.next_attempt_at > datetime.now()
    assert second.attempts == 0  # not tried: Temporal was down, the rest waits in order
    link.down = False
    store.conn().execute(update(workflow_commands).values(next_attempt_at=datetime.now() - timedelta(seconds=1)))
    assert bridge.dispatch_step(store) == 2
    assert [c[1] for c in link.calls] == ["journey-stay-00001", "journey-stay-00002"]


def test_a_signal_for_a_workflow_that_does_not_run_is_dropped(fresh_state, link):
    store = fresh_state
    bus.publish(_event("patient.discharged", encounter="stay-00777", encounter_class="IMP"))
    bus.drain()
    bridge.dispatch_step(store)
    assert _commands(store)[0].status == "dropped"  # admitted before the bridge ran, or finished


# ---------- platform events ----------


def test_people_and_modules_publish_what_workflows_wait_for(fresh_state):
    store = fresh_state
    enc = _imp_encounter(store)
    with runtime.start("workflow_engine", user=None, encounter_id=enc["id"], context_id="journey-x") as run:
        task = run.tool("createWorkflowTask", {"workflow_id": f"journey-{enc['id']}", "step": "orderReview",
                                               "kind": "signoff", "performer_role": "pharmacist",
                                               "encounter_id": enc["id"], "description": "Confirm the admission orders"})
    assert task.ok, task.detail
    assert [t["task_id"] for t in approvals.queue(_user(PHARMACIST))].count(task.output["task_id"]) == 1
    approvals.decide(_user(PHARMACIST), task.output["task_id"], "approve", "fine")
    decided = bus.events(store, types=["task.decided"], limit=5)[0]
    assert decided.refs["task"] == f"Task/{task.output['task_id']}" and decided.attrs["decision"] == "approve"
    assert decided.attrs["code"] == "workflow-signoff" and decided.encounter == enc["id"]

    signed = FhirGateway(Actor.of(_user(PHYSICIAN)), "signing").sign_document("doc-demo-discharge")
    event = bus.events(store, types=["document.signed"], limit=1)[0]
    assert event.refs["document"] == "DocumentReference/doc-demo-discharge"
    assert event.attrs["final"] is (signed.doc_status == "final") and event.attrs["role"] == "physician"

    with runtime.start("bedside_nursing", user=_user(NURSE), encounter_id=enc["id"]) as run:
        flag = run.tool("createFlag", {"encounter_id": enc["id"], "code": "news2", "display": "NEWS2 7"})
    assert flag.ok
    raised = bus.events(store, types=["flag.raised"], limit=1)[0]
    assert raised.attrs["code"] == "news2" and raised.refs["flag"] == f"Flag/{flag.output['flag_id']}"


def test_the_exception_stream_publishes_opened_decided_and_cleared(fresh_state):
    store = fresh_state
    service.reset_state()
    snap = service.refresh(store, force=True)
    opened = bus.events(store, types=["exception.opened"], limit=100)
    live = [e for e in X.stream(store) if e.status == "open"]
    assert {e.attrs["exception_id"] for e in opened} == {e.id for e in live}
    service.reject(store, _user(OPS), live[0].id, "Overflow beds staffed already")
    decided = bus.events(store, types=["exception.decided"], limit=1)[0]
    assert decided.attrs == {**decided.attrs, "exception_id": live[0].id, "decision": "rejected"}
    X.sync(store, [], snap.now)
    cleared = {e.attrs["exception_id"]: e.attrs["decided"] for e in bus.events(store, types=["exception.cleared"],
                                                                                limit=100)}
    assert cleared[live[0].id] is True and cleared[live[1].id] is False
    service.reset_state()


def test_a_run_below_its_confidence_threshold_keeps_a_de_identified_case_for_review(fresh_state):
    from app.agents.library import patient_message_triage as triage

    store = fresh_state
    fhir = FhirGateway(Actor.system("wp4c-test"), "agent_runtime")
    enc = next(e for e in sorted(store.fhir.search("Encounter", cls="IMP", status="in-progress", unit="MEDA"),
                                 key=lambda e: e["id"])
               if consent_status(fhir, e["subject"]["reference"].split("/")[1])["ai_processing"] == "permit")
    with runtime.start(triage.AGENT_ID, user=_user(NURSE), encounter_id=enc["id"]) as run:
        low = triage.run(run, "I think I left my reading glasses in the room.")
    with runtime.start(triage.AGENT_ID, user=_user(NURSE), encounter_id=enc["id"]) as run:
        sure = triage.run(run, "Can we move the follow-up appointment to Tuesday?")
    assert low.confidence < 0.7 <= sure.confidence
    found = [r for r in reviews.table(store).values() if r.run_id in (low.run_id, sure.run_id)]
    assert [r.run_id for r in found] == [low.run_id]
    review = found[0]
    assert review.threshold == 0.7 and review.output["category"] == "other"
    assert set(review.input) == {"context", "channel", "rule_flags", "message"}
    patient = store.fhir.read("Patient", enc["subject"]["reference"].split("/")[1])
    family = patient["name"][0]["family"]
    assert family not in review.input["context"]  # de-identified as the model saw it
    event = bus.events(store, types=["agent.low_confidence"], limit=1)[0]
    assert event.attrs["review_id"] == review.id and event.attrs["run_id"] == low.run_id


# ---------- the workflow API ----------


def _snapshot(workflow_id: str, steps: dict[str, str], *, version: int = 1, unit: str = "MEDA",
              encounter: str = "stay-00001", status: str = "running") -> dict:
    workflow_type = M.type_of(workflow_id)
    timeline = M.timeline(M.STEPS[workflow_type])
    for s in timeline:
        s["status"] = steps.get(s["key"], "pending")
    current = next((s["key"] for s in timeline if s["status"] not in ("done", "skipped", "pending")), None)
    return {"id": workflow_id, "workflow_type": workflow_type, "status": status, "version": version,
            "current_step": current, "steps": timeline, "notes": [], "temporal_run_id": "r1",
            "meta": {"encounter_id": encounter, "patient_id": "pat-0001", "unit_id": unit},
            "started_at": "2026-10-09T09:00:00+00:00", "updated_at": "2026-10-09T09:05:00+00:00"}


def test_the_timeline_keeps_the_newest_version_and_shows_retries(fresh_state):
    store = fresh_state
    assert progress.record(_snapshot("journey-stay-00001", {"triageAssist": "done", "orderReview": "running"},
                                     version=2))
    assert not progress.record(_snapshot("journey-stay-00001", {"triageAssist": "running"}, version=1))
    assert progress.get("journey-stay-00001").steps[1]["status"] == "running"
    done = [e for e in bus.events(store, types=["workflow.step_completed"], limit=10)]
    assert [e.attrs["step"] for e in done] == ["triageAssist"] and done[0].encounter == "stay-00001"
    progress.mark_attempt("journey-stay-00001", "orderReview", 1, "ConnectionError: gone", max_attempts=4)
    step = progress.get("journey-stay-00001").steps[1]
    assert step["status"] == "retrying" and "Attempt 1 of 4 failed" in step["detail"]
    view = progress.view(progress.get("journey-stay-00001"))
    assert view["next_step"] == "medRecAdmission" and view["steps"][0]["label"] == "Triage assist"


def test_retry_and_skip_are_for_the_operations_manager_and_admin_and_audited(fresh_state, client, login, link):
    store = fresh_state
    wf = "journey-stay-00001"
    progress.record(_snapshot(wf, {"triageAssist": "done", "orderReview": "waiting", "medRecAdmission": "pending"}))
    url = f"/api/workflows/{wf}/steps/orderReview"
    assert client.post(f"{url}/skip", json={}, headers=login(NURSE)).status_code == 403
    assert client.post(f"{url}/skip", json={}, headers=login(CLERK)).status_code == 403
    assert client.post(f"{url}/retry", json={}, headers=login(OPS)).status_code == 409  # not failed
    r = client.post(f"{url}/skip", json={"note": "reviewed on paper"}, headers=login(OPS))
    assert r.status_code == 202, r.text
    [audit] = [e for e in store.audit.query(resource_type="Workflow", resource_id=wf) if e.action == "workflow_skip"]
    assert audit.user_id == OPS and "reviewed on paper" in audit.reason and audit.module == "workflow_engine"
    command = _commands(store)[-1]
    assert command.signal == M.SIGNAL_CONTROL and command.payload["action"] == "skip"
    assert command.payload["step"] == "orderReview" and command.payload["event_id"]
    progress.record(_snapshot(wf, {"triageAssist": "done", "orderReview": "failed"}, version=2))
    assert client.post(f"{url}/retry", json={}, headers=login(ADMIN)).status_code == 202
    bridge.dispatch_step(store)
    assert [c[2] for c in link.calls if c[0] == "signal"] == [M.SIGNAL_CONTROL, M.SIGNAL_CONTROL]


def test_the_view_shows_nurses_only_their_units(fresh_state, client, login, link):
    progress.record(_snapshot("journey-stay-00001", {"triageAssist": "done"}, unit="MEDA"))
    progress.record(_snapshot("journey-stay-00002", {"triageAssist": "done"}, unit="ORTH", encounter="stay-00002"))
    ids = {w["id"] for w in client.get("/api/workflows", headers=login(NURSE)).json()["workflows"]}
    assert ids == {"journey-stay-00001"}
    assert client.get("/api/workflows/journey-stay-00002", headers=login(NURSE)).status_code == 403
    ops = client.get("/api/workflows", headers=login(OPS)).json()
    assert {w["id"] for w in ops["workflows"]} == {"journey-stay-00001", "journey-stay-00002"}
    detail = client.get("/api/workflows/journey-stay-00001", headers=login(OPS)).json()
    assert detail["can_control"] is True and detail["temporal"]["status"] == "RUNNING"
    assert client.get("/api/workflows/encounters/stay-00002", headers=login(OPS)).json()["id"] == "journey-stay-00002"


def test_a_journey_starts_from_the_view_only_for_an_inpatient_stay(fresh_state, client, login, link):
    store = fresh_state
    enc = _imp_encounter(store)
    r = client.post("/api/workflows/journeys", json={"encounter_id": enc["id"], "signoff_timeout_s": 60},
                    headers=login(OPS))
    assert r.status_code == 202 and r.json()["workflow_id"] == f"journey-{enc['id']}"
    command = _commands(store)[-1]
    assert command.kind == "start" and command.payload["timers"]["signoff_timeout_s"] == 60
    assert command.payload["started_by"] == f"user:{OPS}"
    ed = sorted(store.fhir.search("Encounter", cls="EMER", status="in-progress"), key=lambda e: e["id"])[0]
    assert client.post("/api/workflows/journeys", json={"encounter_id": ed["id"]}, headers=login(OPS)).status_code == 409
    assert client.post("/api/workflows/journeys", json={"encounter_id": enc["id"]},
                       headers=login(NURSE)).status_code == 403


def test_deliberate_failures_are_a_demo_feature_and_audited(fresh_state, client, login):
    store = fresh_state
    r = client.post("/api/workflows/faults", json={"step": "medRecAdmission", "times": 2}, headers=login(OPS))
    assert r.status_code == 200 and r.json()["faults"] == {"medRecAdmission": 2}
    assert client.post("/api/workflows/faults", json={"step": "nonsense", "times": 1},
                       headers=login(OPS)).status_code == 422
    assert [e.action for e in store.audit.query(resource_type="Workflow") if e.action == "workflow_fault"]
    with pytest.raises(faults.InjectedFailure):
        faults.maybe_fail("medRecAdmission")
    assert faults.pending() == {"medRecAdmission": 1}
    with registry.mode("prod"):
        assert client.post("/api/workflows/faults", json={"step": "execute", "times": 1},
                           headers=login(OPS)).status_code == 409
    assert client.delete("/api/workflows/faults", headers=login(OPS)).json() == {"faults": {}}


def test_a_running_capacity_workflow_executes_the_approval_instead_of_the_request(fresh_state, link):
    store = fresh_state
    service.reset_state()
    service.refresh(store, force=True)
    exc = next(e for e in X.stream(store) if e.status == "open")
    service.narrate(store, exc.id)
    wf = M.capacity_id(exc.id)
    progress.record(_snapshot(wf, {"detect": "done", "explainAndRecommend": "done", "decision": "waiting"},
                              unit=exc.unit_id))
    exc = X.get(store, exc.id)
    actions = [a["action_id"] for a in exc.narration["recommended_actions"]]
    decided = service.approve(store, _user(OPS), exc.id, actions)
    assert decided.decision["executed_by"] == "workflow" and decided.decision["workflow_id"] == wf
    assert all(a["status"] == "queued" and not a["task_id"] for a in decided.decision["actions"])
    done = service.execute_actions(store, exc.id)  # what the workflow's execute activity does
    assert all(a["task_id"] for a in done.decision["actions"])
    again = service.execute_actions(store, exc.id)  # a retried activity: the same Tasks
    assert [a["task_id"] for a in again.decision["actions"]] == [a["task_id"] for a in done.decision["actions"]]
    outcome = service.record_outcome(store, exc.id, "approved", service.verify(store, exc.id)).outcome
    assert outcome["decision"] == "approved" and len(outcome["verified"]["tasks"]) == len(actions)
    service.reset_state()


def test_a_low_confidence_review_is_corrected_by_the_agents_signer(fresh_state, client, login):
    store = fresh_state
    enc = _imp_encounter(store)
    review = reviews.AgentReview(id="REV-09001", run_id="RUN-09001", agent_id="patient_message_triage",
                                 agent_version="0.1.0", encounter_id=enc["id"],
                                 patient_id=enc["subject"]["reference"].split("/")[1], confidence=0.5, threshold=0.7,
                                 input={"message": "<untrusted source=\"message\">\nglasses\n</untrusted>"},
                                 output={"category": "other", "urgency": "routine", "summary": "s"},
                                 created_at=datetime.now())
    with runtime.start("workflow_engine", user=None, encounter_id=enc["id"], context_id="review-RUN-09001") as run:
        task = run.tool("createWorkflowTask", {"workflow_id": "review-RUN-09001", "step": "reviewed", "kind": "review",
                                               "description": "Low AI confidence", "performer_role": "nurse",
                                               "encounter_id": enc["id"], "inputs": {"review": review.id}})
    assert task.ok, task.detail
    reviews.save(review.model_copy(update={"task_id": task.output["task_id"]}))
    url = f"/api/workflows/reviews/{review.id}"
    shown = client.get(url, headers=login(NURSE)).json()
    assert shown["output"]["category"] == "other" and "category" in shown["correctable"]
    assert client.get(url, headers=login(PHARMACIST)).status_code == 403  # triage output is reviewed by nurses
    assert client.post(url, json={"correction": {"category": "lunch"}}, headers=login(NURSE)).status_code == 409
    r = client.post(url, json={"correction": {"category": "administrative"}, "note": "belongings"},
                    headers=login(NURSE))
    assert r.status_code == 200, r.text
    assert r.json()["correction"] == {"category": "administrative"} and reviews.get(review.id).status == "reviewed"
    assert store.fhir.read("Task", task.output["task_id"])["status"] == "completed"
    assert bus.events(store, types=["task.decided"], limit=1)[0].refs["task"] == f"Task/{task.output['task_id']}"


def test_an_exception_is_read_fresh_after_its_lock_so_it_is_narrated_once(fresh_state):
    """The narrator loop, a request and the workflow's activity may all narrate the same exception. Each takes the
    exception's lock, then reads it again: a copy loaded earlier in the transaction (the loop's list) may be stale."""
    from sqlalchemy import text

    store = fresh_state
    service.reset_state()
    service.refresh(store, force=True)
    exc = next(e for e in X.pending_narration(store))  # loaded into this transaction, narration pending
    store.flush()
    store.conn().execute(text("UPDATE flow_exceptions SET narration_status = 'done' WHERE row_key = :k"),
                         {"k": exc.id})  # another transaction narrated it meanwhile
    assert X.get(store, exc.id).narration_status == "pending"  # this transaction's earlier copy
    assert X.fresh(store, exc.id).narration_status == "done"
    reviews_before = len(store.fhir.search("Task", code="ai-review"))
    service.narrate(store, exc.id)
    assert len(store.fhir.search("Task", code="ai-review")) == reviews_before  # not narrated a second time
    service.reset_state()
