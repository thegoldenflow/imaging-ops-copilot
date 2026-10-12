"""WP4b (spec 6.4): the Tool Gateway. One positive and one refusal test per side-effect level, the
check order, idempotency, privileged approvals, retries and fallbacks.

Runs act for the seeded hospital staff: the Medicine A nurse (U-NURS-05) and physician (U-DOC-09), the
registration clerk (U-CLER-01) and the bed manager (U-OPS).
"""

import pytest

from app.agents import approvals, registry
from app.agents import trace as traces
from app.agents.gateway import ToolUnavailable
from app.agents.runtime import runtime
from app.ehr.consent import consent_status
from app.ehr.gateway import Actor, FhirGateway

NURSE, PHYSICIAN, CLERK, OPS = "U-NURS-05", "U-DOC-09", "U-CLER-01", "U-OPS"


def _system():
    return FhirGateway(Actor.system("wp4b-test"), "agent_runtime")


def _inpatient(store, unit="MEDA", *, consent=True, exclude=None):
    """An in-progress inpatient in a bed: on `unit`, or (unit None) anywhere but `exclude`."""
    fhir = _system()
    for enc in sorted(store.fhir.search("Encounter", cls="IMP", status="in-progress", unit=unit),
                      key=lambda e: e["id"]):
        bed = next((loc["location"]["reference"].split("/")[1] for loc in reversed(enc.get("location") or [])
                    if loc.get("status") == "active"), "")
        if bed.count("-") != 2 or (exclude and bed.startswith(exclude + "-")):
            continue
        pid = enc["subject"]["reference"].split("/")[1]
        if (consent_status(fhir, pid)["ai_processing"] == "permit") == consent:
            return enc
    raise AssertionError("no matching inpatient")


def _staff(store, user_id):
    return store.staff[user_id]


def _start(store, agent, user_id, encounter_id, **kw):
    return runtime.start(agent, user=_staff(store, user_id) if user_id else None, encounter_id=encounter_id, **kw)


def _tool_events(store, run_id):
    return [e for e in store.audit.query(event_type="tool_call") if f"run {run_id}" in e.reason]


# ---------- read ----------


def test_read_tool_runs_for_the_agent_and_records_its_inputs(fresh_state):
    store = fresh_state
    enc = _inpatient(store)
    with _start(store, "patient_message_triage", NURSE, enc["id"]) as run:
        result = run.tool("getEncounterContext", {"encounter_id": enc["id"]})
    assert result.status == "ok"
    types = {r["resourceType"] for r in result.output["resources"]}
    assert {"Encounter", "Patient"} <= types and types <= {"Encounter", "Patient", "Flag"}
    assert f"Encounter/{enc['id']}" in run.trace.input_refs
    event = _tool_events(store, run.run_id)[-1]
    assert (event.outcome, event.module, event.resource_id, event.user_id) == (
        "allowed", "patient_message_triage", "getEncounterContext", NURSE)


def test_read_tool_outside_the_runs_encounter_or_allow_list_is_refused(fresh_state):
    store = fresh_state
    enc, other = _inpatient(store), _inpatient(store, None, exclude="MEDA")
    with _start(store, "patient_message_triage", NURSE, enc["id"]) as run:
        elsewhere = run.tool("getEncounterContext", {"encounter_id": other["id"]})
        not_listed = run.tool("getEncounterBundle", {"encounter_id": enc["id"]})
        unknown = run.tool("readEverything", {})
    assert (elsewhere.status, elsewhere.reason) == ("refused", "outside_data_scope")
    assert (not_listed.status, not_listed.reason) == ("refused", "not_on_allow_list")
    assert (unknown.status, unknown.reason) == ("refused", "unknown_tool")
    assert [c.status for c in run.trace.tool_calls] == ["refused"] * 3
    assert {e.outcome for e in _tool_events(store, run.run_id)} == {"denied"}


def test_role_check_uses_the_caller_not_the_model(fresh_state):
    """writeCommunication may be caused by physicians, nurses and clerks, not by pharmacists."""
    store = fresh_state
    enc = _inpatient(store)
    pharmacist = next(u for u in store.staff.values() if str(u.role) == "pharmacist" and u.demo_login)
    with registry.temporary("pharmacy_notes", {"kind": "module", "allowed_tools": ["writeCommunication"],
                                               "data_scope": {"resources": ["Encounter"], "scope": "hospital"}}):
        with runtime.start("pharmacy_notes", user=pharmacist, encounter_id=enc["id"]) as run:
            result = run.tool("writeCommunication", {"encounter_id": enc["id"], "direction": "outbound",
                                                     "medium": "phone", "text": "Call back", "status": "completed",
                                                     })
    assert (result.status, result.reason) == ("refused", "role_not_permitted")


# ---------- recommend ----------


def test_recommend_tool_writes_a_proposal_into_the_review_queue(fresh_state):
    store = fresh_state
    enc = _inpatient(store)
    with _start(store, "patient_message_triage", NURSE, enc["id"]) as run:
        result = run.tool("submitForReview", {"encounter_id": enc["id"], "reviewer_role": "nurse",
                                              "summary": "Reply draft", "recommendation": "We will call you today."})
    assert result.status == "ok"
    task = store.fhir.read("Task", result.output["task_id"])
    assert (task["status"], task["intent"]) == ("requested", "proposal")
    assert traces.run_of(task) == run.run_id
    queue = approvals.queue(_staff(store, NURSE))
    assert any(t["task_id"] == task["id"] and t["recommendation"] == "We will call you today." for t in queue)


def test_recommend_tool_without_the_patients_consent_is_refused(fresh_state):
    store = fresh_state
    enc = _inpatient(store, None, consent=False)
    with runtime.start("patient_instructions", encounter_id=enc["id"]) as run:  # a background run: no user
        result = run.tool("draftDocument", {"encounter_id": enc["id"], "doc_type": "patient_instructions",
                                            "title": "Instructions", "text": "Draft"})
    assert (result.status, result.reason) == ("refused", "consent_missing")
    assert "ai_processing" in result.detail
    assert not [d for d in store.fhir.search("DocumentReference", encounter=enc["id"]) if traces.run_of(d)]


# ---------- action ----------


def test_action_tool_runs_once_and_a_repeat_within_24_hours_returns_the_first_result(fresh_state):
    store = fresh_state
    enc = _inpatient(store)
    args = {"encounter_id": enc["id"], "code": "patient-message", "description": "Call the patient back",
            "performer_role": "nurse"}
    before = len(store.fhir.search("Task", encounter=enc["id"]))
    with _start(store, "patient_message_triage", NURSE, enc["id"]) as run:
        first = run.tool("createTask", args)
        second = run.tool("createTask", dict(reversed(list(args.items()))))  # same arguments, other order
    with _start(store, "patient_message_triage", NURSE, enc["id"]) as later:
        third = later.tool("createTask", args)
    assert first.status == "ok" and (second.status, third.status) == ("replayed", "replayed")
    assert first.output == second.output == third.output
    assert first.idempotency_key == traces.idempotency_key("patient_message_triage", enc["id"], "createTask", args)
    assert len(store.fhir.search("Task", encounter=enc["id"])) == before + 1
    record = store.module(traces.IDEMPOTENCY, dict)[first.idempotency_key]
    assert (record.expires_at - record.created_at).total_seconds() == 24 * 3600
    changed = {**args, "description": "Call the patient back today"}
    with _start(store, "patient_message_triage", NURSE, enc["id"]) as run:
        assert run.tool("createTask", changed).status == "ok"  # other arguments: a new task


def test_action_tool_refuses_unknown_arguments_and_other_patients(fresh_state):
    store = fresh_state
    enc, other = _inpatient(store), _inpatient(store, None, exclude="MEDA")
    with _start(store, "patient_message_triage", NURSE, enc["id"]) as run:
        claims = run.tool("createTask", {"encounter_id": enc["id"], "code": "x-1", "description": "d",
                                         "performer_role": "nurse", "role": "admin", "approved": True})
        elsewhere = run.tool("createTask", {"encounter_id": other["id"], "code": "x-1", "description": "d",
                                            "performer_role": "nurse"})
    assert (claims.status, claims.reason) == ("refused", "invalid_arguments")
    assert "role: not allowed" in claims.detail
    assert (elsewhere.status, elsewhere.reason) == ("refused", "outside_data_scope")


# ---------- privileged ----------


def test_privileged_tool_without_an_approval_record_is_refused_and_opens_one(fresh_state):
    store = fresh_state
    enc = _inpatient(store)
    with _start(store, "discharge_summary", PHYSICIAN, enc["id"]) as run:
        draft = run.tool("draftDocument", {"encounter_id": enc["id"], "doc_type": "discharge_summary",
                                           "title": "Discharge summary", "text": "Draft text"})
        doc_id = draft.output["document_id"]
        asked = run.tool("signDocumentFinal", {"document_id": doc_id})
        asked_again = run.tool("signDocumentFinal", {"document_id": doc_id})
        forged = run.tool("signDocumentFinal", {"document_id": doc_id, "approval_task_id": "approved"})
        pending = run.tool("signDocumentFinal", {"document_id": doc_id, "approval_task_id": asked.approval_task_id})
    assert (asked.status, asked.reason) == ("approval_required", "approval_required")
    assert asked_again.approval_task_id == asked.approval_task_id  # one open approval per call
    task = store.fhir.read("Task", asked.approval_task_id)
    assert approvals.code_of(task) == approvals.APPROVAL and task["focus"]["reference"] == f"DocumentReference/{doc_id}"
    assert (forged.status, forged.reason) == ("refused", "approval_invalid")
    assert (pending.status, pending.reason) == ("refused", "approval_invalid") and "not approved" in pending.detail
    assert store.fhir.read("DocumentReference", doc_id)["docStatus"] == "preliminary"
    # a nurse cannot give the physician's approval
    with pytest.raises(approvals.DecisionDenied):
        approvals.decide(_staff(store, NURSE), asked.approval_task_id, "approve")


def test_privileged_tool_runs_after_the_approval_as_the_approver_with_a_double_audit(fresh_state):
    store = fresh_state
    enc = _inpatient(store)
    physician = _staff(store, PHYSICIAN)
    with _start(store, "discharge_summary", PHYSICIAN, enc["id"]) as run:
        doc_id = run.tool("draftDocument", {"encounter_id": enc["id"], "doc_type": "discharge_summary",
                                            "title": "Discharge summary", "text": "Draft"}).output["document_id"]
        task_id = run.tool("signDocumentFinal", {"document_id": doc_id}).approval_task_id
    decision = approvals.decide(physician, task_id, "approve", "Read and agreed")
    result = runtime.execute_approved(decision)
    assert result.status == "ok" and result.output["doc_status"] == "final"
    doc = store.fhir.read("DocumentReference", doc_id)
    assert doc["docStatus"] == "final" and doc["authenticator"]["reference"] == f"Practitioner/{physician.practitioner_id}"
    privileged = [e for e in _tool_events(store, run.run_id) if e.action == "privileged_call"]
    assert [e.outcome for e in privileged] == ["approved", "allowed"]
    assert store.audit.query(event_type="sign", resource_id=doc_id, outcome="allowed")
    assert store.audit.query(event_type="approve", resource_id=task_id, user_id=physician.id)
    trace = traces.get(run.run_id)
    sign_call = next(c for c in trace.tool_calls if c.tool_id == "signDocumentFinal" and c.status == "ok")
    assert sign_call.attempts == 1 and sign_call.approval_task_id == task_id
    assert trace.human_action["decision"] == "sign" and trace.human_action["user_id"] == physician.id
    prov = store.fhir.read("Provenance", traces.provenance_id(run.run_id))
    assert any(a["who"].get("reference") == f"Practitioner/{physician.practitioner_id}" for a in prov["agent"])
    # the same approved call again: replayed, not signed twice
    with runtime.resume(run.run_id) as again:
        assert again.tool("signDocumentFinal", {"document_id": doc_id, "approval_task_id": task_id}).status == "replayed"


def test_booking_is_privileged_and_needs_a_clerks_approval(fresh_state):
    """The path WP2 deferred: Appointment proposed -> booked only with a recorded clerk approval."""
    store = fresh_state
    enc = _inpatient(store)
    clerk = _staff(store, CLERK)
    with _start(store, "registration", CLERK, enc["id"]) as run:
        appt = run.tool("proposeAppointment", {"encounter_id": enc["id"], "service": "follow-up",
                                               "start": "2026-11-02T09:00", "minutes": 30}).output["appointment_id"]
        asked = run.tool("bookAppointment", {"appointment_id": appt})
    assert asked.status == "approval_required"
    assert store.fhir.read("Appointment", appt)["status"] == "proposed"
    approval = store.fhir.read("Task", asked.approval_task_id)
    assert approval["performerType"][0]["coding"][0]["code"] == "clerk"
    result = runtime.execute_approved(approvals.decide(clerk, asked.approval_task_id, "approve"))
    assert result.status == "ok" and store.fhir.read("Appointment", appt)["status"] == "booked"
    from app.ehr.events import InProcessEventBus

    assert any(e.type == "appointment.scheduled" and e.refs.get("appointment") == f"Appointment/{appt}"
               for e in InProcessEventBus.events(store))


def test_bed_move_is_privileged_and_runs_with_the_bed_managers_approval(fresh_state):
    store = fresh_state
    enc = _inpatient(store)
    bed = next(b for b in store.fhir.search("Location", status="U") if b["id"].count("-") == 2)
    ops = _staff(store, OPS)
    with _start(store, "control_tower", OPS, enc["id"]) as run:
        asked = run.tool("appendEncounterLocation", {"encounter_id": enc["id"], "bed_id": bed["id"]})
    result = runtime.execute_approved(approvals.decide(ops, asked.approval_task_id, "approve"))
    assert result.status == "ok"
    moved = store.fhir.read("Encounter", enc["id"])
    assert moved["location"][-1]["location"]["reference"] == f"Location/{bed['id']}"


# ---------- retries and fallbacks ----------

CALLS: list[str] = []


def flaky(ctx, encounter_id):
    """A handler that fails twice, then answers (for the retry tests)."""
    CALLS.append(encounter_id)
    if len(CALLS) < 3:
        raise ToolUnavailable("backend timeout")
    return {"resources": []}


def always_down(ctx, **_):
    CALLS.append("down")
    raise ToolUnavailable("backend down")


def _tool(tool_id, level, handler, attempts, **extra):
    schema = {"type": "object", "properties": {"encounter_id": {"type": "string"}}, "required": ["encounter_id"],
              "additionalProperties": False}
    spec = {"tool_id": tool_id, "description": "test", "side_effect_level": level, "handler": handler,
            "domain": "fhir", "input_schema": schema, "output_schema": {"type": "object"},
            "permission_policy": {"roles": ["nurse", "physician", "system"], "target": {"encounter": "encounter_id"},
                                  **extra.pop("policy", {})},
            "timeout_ms": 1000, "retry": {"max_attempts": attempts, "backoff_ms": 1},
            "audit_level": "double" if level == "privileged" else "standard", **extra}
    if level in ("action", "privileged"):
        spec["idempotency"] = {"required": True}
    return registry.temporary_tool(tool_id, spec)


def test_reads_are_retried_per_the_registry(fresh_state):
    store = fresh_state
    enc = _inpatient(store)
    CALLS.clear()
    with _tool("flakyRead", "read", "test_tool_gateway:flaky", 3), \
            registry.temporary("retry_agent", {"kind": "module", "allowed_tools": ["flakyRead"],
                                               "data_scope": {"resources": [], "scope": "hospital"}}):
        with _start(store, "retry_agent", NURSE, enc["id"]) as run:
            result = run.tool("flakyRead", {"encounter_id": enc["id"]})
    assert result.status == "ok" and len(CALLS) == 3 and run.trace.tool_calls[-1].attempts == 3


def test_exhausted_retries_fall_back_to_the_rule_result_or_a_task_for_a_person(fresh_state):
    store = fresh_state
    enc = _inpatient(store)
    CALLS.clear()
    with _tool("downAction", "action", "test_tool_gateway:always_down", 2), \
            registry.temporary("retry_agent", {"kind": "module", "allowed_tools": ["downAction"],
                                               "required_signoff_role": ["nurse"],
                                               "data_scope": {"resources": [], "scope": "hospital"}}):
        with _start(store, "retry_agent", NURSE, enc["id"]) as run:
            with_rules = run.tool("downAction", {"encounter_id": enc["id"]}, fallback={"rule": "result"})
            without = run.tool("downAction", {"encounter_id": enc["id"]})
    assert (with_rules.status, with_rules.output) == ("fallback", {"rule": "result"})
    assert without.status == "failed" and without.needs_human_task_id
    task = store.fhir.read("Task", without.needs_human_task_id)
    assert approvals.code_of(task) == approvals.NEEDS_HUMAN and task["performerType"][0]["coding"][0]["code"] == "nurse"
    assert len(CALLS) == 4  # two attempts each


def test_privileged_tools_are_never_retried():
    for tool in registry.tools().values():
        if tool.side_effect_level == "privileged":
            assert tool.retry.max_attempts == 1 and tool.audit_level == "double", tool.tool_id
    with pytest.raises(ValueError, match="0 automatic retries"):
        registry.ToolSpec.model_validate({
            "tool_id": "badPrivileged", "description": "x", "side_effect_level": "privileged",
            "handler": "x:y", "domain": "fhir",
            "input_schema": {"type": "object", "properties": {"approval_task_id": {"type": "string"}},
                             "required": ["approval_task_id"]},
            "output_schema": {"type": "object"},
            "permission_policy": {"roles": ["nurse"], "approver_roles": ["nurse"]},
            "idempotency": {"required": True}, "timeout_ms": 1000, "retry": {"max_attempts": 3},
            "audit_level": "double"})
