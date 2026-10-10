"""Activities of the three workflows (spec 6.5): where the work happens.

Each activity runs in its own unit of work (one database transaction, committed when it returns), in the worker
process. Every side effect goes through the Tool Gateway (6.4) as a registered agent or as the `workflow_engine`
module, so the gateway's idempotency key (agent, encounter, tool, arguments) makes a retry harmless: when Temporal
runs an activity again after its work committed, the gateway returns the first result instead of writing a second
draft or Task. Activity arguments are built only from data that does not change afterwards, so a retry sends the
same arguments.

Retry policy (base.py): 3 retries 1 s / 4 s / 16 s; the activity that books the follow-up call (privileged tool
bookAppointment) runs once and a failure becomes a Task. A missing resource or invalid data is not retried (the
step fails and waits for a person). Every failed attempt and every retry is shown on the workflow's timeline
while it happens (`progress.mark_attempt`). `faults.maybe_fail` lets the demo break a step on purpose after its
work committed.
"""

from __future__ import annotations

import contextlib
import functools
import logging
from datetime import datetime, timedelta

from temporalio import activity
from temporalio.exceptions import ApplicationError

from app.agents import evalsets, registry, reviews
from app.agents import trace as traces
from app.agents.gateway import ToolResult
from app.agents.library import journey_stubs as stubs
from app.agents.runtime import runtime
from sqlalchemy import text

from app.core.store import REWRITE_LOCK, get_store, unit_of_work
from app.ehr import access
from app.ehr.clock import hospital_now
from app.ehr.gateway import Actor, FhirGateway
from app.ehr.reference import UNIT_BY_ID
from app.fhir.dt import fhir_datetime, parse
from app.workflows import bridge, faults, progress
from app.workflows import model as M

log = logging.getLogger("app.workflows")
ENGINE = "workflow_engine"  # the registry entry the workflows' own tool calls run as


class StepError(RuntimeError):
    """A step could not do its work (retried)."""


@contextlib.contextmanager
def _work():
    """An activity's transaction. Like the API's background steps it takes the demo-reset lock shared first, so a
    reset (which holds it exclusively while it truncates every table) and an activity never deadlock: the activity
    waits for the reset, the reset for the activity's transaction."""
    with unit_of_work() as store:
        if not store.detached:
            store.conn().execute(text("SELECT pg_advisory_xact_lock_shared(:key)"), {"key": REWRITE_LOCK})
        yield store


def _mark(workflow_id: str, step: str, attempt: int, error: str | None, max_attempts: int) -> None:
    try:
        with _work():
            progress.mark_attempt(workflow_id, step, attempt, error, max_attempts=max_attempts)
    except Exception:  # the timeline is a view; never fail the activity for it
        log.exception("could not mark the attempt of %s on %s", step, workflow_id)


def step_activity(name: str, step: str | None = None, *, max_attempts: int = M.RETRY_MAX_ATTEMPTS):
    """An activity of a step: its own transaction, attempts shown on the timeline, missing data not retried,
    and the demo's deliberate failure (faults.py) after the work committed."""
    def deco(fn):
        @activity.defn(name=name)
        @functools.wraps(fn)
        def run(args: dict) -> dict:
            info = activity.info()
            key = step or args.get("step") or name
            if info.attempt > 1:
                _mark(info.workflow_id, key, info.attempt, None, max_attempts)
            try:
                with _work():
                    out = fn(args) or {}
                faults.maybe_fail(key)
            except Exception as e:
                _mark(info.workflow_id, key, info.attempt, f"{type(e).__name__}: {e}", max_attempts)
                if isinstance(e, (LookupError, ValueError)):  # retrying would not help
                    kind = "SubjectGone" if isinstance(e, LookupError) else type(e).__name__
                    raise ApplicationError(f"{type(e).__name__}: {e}", type=kind, non_retryable=True) from e
                raise
            return {**out, "_attempt": info.attempt}
        return run
    return deco


# ---------- helpers: the Tool Gateway as an agent or as the workflow engine ----------


def _fhir() -> FhirGateway:
    """Reads for the workflows' own bookkeeping (system actor, audited, purpose module workflow_engine)."""
    return FhirGateway(Actor.system("workflows"), ENGINE)


def _engine(tool: str, args: dict, *, encounter_id: str | None, context_id: str) -> ToolResult:
    with runtime.start(ENGINE, user=None, encounter_id=encounter_id, context_id=context_id) as run:
        result = run.tool(tool, args)
        run.finish(outcome="completed" if result.ok else result.status)
    return result


def _engine_task(args: dict, step: str, kind: str, description: str, role: str, *, priority: str = "routine",
                 focus: str | None = None, inputs: dict | None = None) -> str:
    """A Task of the workflow (createWorkflowTask), idempotent per workflow, step, kind and text."""
    tool_args = {"workflow_id": args["workflow_id"], "step": step, "kind": kind, "description": description[:1000],
                 "performer_role": role, "priority": priority}
    if args.get("encounter_id"):
        tool_args["encounter_id"] = args["encounter_id"]
    elif args.get("unit_id"):
        tool_args["unit_id"] = args["unit_id"]
    if focus:
        tool_args["focus"] = focus
    if inputs:
        tool_args["inputs"] = {k: str(v) for k, v in inputs.items()}
    result = _engine("createWorkflowTask", tool_args, encounter_id=args.get("encounter_id"),
                     context_id=args["workflow_id"])
    if not result.ok:
        raise StepError(f"createWorkflowTask {result.status}: {result.reason}: {result.detail}")
    return result.output["task_id"]


def _agent_step(agent_id: str, encounter_id: str, fn) -> stubs.StepResult:
    """Run a journey step as the agent that owns it (no user: the agent acts as itself)."""
    try:
        with runtime.start(agent_id, user=None, encounter_id=encounter_id) as run:
            result = fn(run)
            run.finish(outcome="refused" if result.refused else "completed")
        return result
    except registry.AgentNotDeployable as e:  # prod mode before the agent passed its eval
        return stubs.StepResult(refused=f"not_deployable: {e}")


def _signoff(args: dict, step: str, result: stubs.StepResult, role: str, by_hand: str) -> dict:
    """What the workflow waits for; when the agent was refused (no consent, not deployable), a Task to do it by
    hand instead (the 6.3 degradation)."""
    if result.refused:
        reason = result.refused.split(":", 1)[0]
        task = _engine_task(args, step, "signoff", f"{by_hand} (the AI step did not run: {result.refused[:200]}).",
                            role)
        ref = f"Task/{task}"
        bridge.register_wait(ref, args["workflow_id"], step)
        return {"refs": [ref], "wait_for": [ref], "signer": role, "detail": f"By hand ({reason.replace('_', ' ')})"}
    for ref in result.wait_for:
        bridge.register_wait(ref, args["workflow_id"], step)
    return {"refs": result.refs[:30], "wait_for": result.wait_for, "signer": result.signer or role,
            "detail": result.detail}


def _hhmm(value: str | None) -> str:
    when = parse(value) if value else None
    return when.strftime("%a %H:%M") if when else "?"


# ---------- common ----------


@activity.defn(name="workflows.recordProgress")
def record_progress(snapshot: dict) -> bool:
    """The workflow's timeline for the view (idempotent: a newer version wins)."""
    with _work():
        return progress.record(snapshot)


@step_activity("workflows.openTask")
def open_task(args: dict) -> dict:
    """A Task the workflow opens for a person: an escalation, a note to the operations manager, a failed step."""
    task_id = _engine_task(args, args["step"], args["kind"], args["description"], args["performer_role"],
                           priority=args.get("priority", "routine"), focus=args.get("focus"),
                           inputs=args.get("inputs"))
    return {"task_id": task_id}


@step_activity("workflows.closeTasks")
def close_tasks(args: dict) -> dict:
    """The escalation, notice and failure Tasks of a step that is over (updateTask, completed, with a note)."""
    closed = []
    fhir = _fhir()
    for task_id in args["task_ids"]:
        task = fhir.read("Task", task_id)
        if task is None or task.status not in ("requested", "received", "accepted", "ready", "in-progress",
                                                "on-hold"):
            continue
        result = _engine("updateTask", {"task_id": task_id, "status": "completed", "note": args["note"][:1000]},
                         encounter_id=args.get("encounter_id"), context_id=args["workflow_id"])
        if not result.ok:
            raise StepError(f"updateTask {result.status}: {result.reason}: {result.detail}")
        closed.append(task_id)
    return {"closed": closed}


# ---------- InpatientJourneyWorkflow ----------


def _current_bed(encounter: dict) -> tuple[str | None, str | None]:
    entries = encounter.get("location") or []
    active = [e for e in entries if e.get("status") == "active"] or entries[-1:]
    if not active:
        return None, None
    bed = (active[-1].get("location") or {}).get("reference", "").split("/", 1)[-1] or None
    return bed, (active[-1].get("period") or {}).get("start")


@step_activity("journey.context", "triageAssist")
def journey_context(args: dict) -> dict:
    fhir = _fhir()
    found = fhir.read("Encounter", args["encounter_id"])
    if found is None:
        return {"missing": f"Encounter/{args['encounter_id']} not found"}
    enc = found.to_fhir()
    bed, _ = _current_bed(enc)
    ed_visit = None
    for based in enc.get("basedOn") or []:
        rtype, _, rid = (based.get("reference") or "").partition("/")
        request = fhir.read(rtype, rid) if rtype == "ServiceRequest" else None
        if request is not None:
            ed_visit = access.encounter_of(request.to_fhir())
    return {"patient_id": access.patient_of(enc), "bed_id": bed, "unit_id": bed.split("-")[0] if bed else None,
            "admitted_at": (enc.get("period") or {}).get("start"), "ed_visit": ed_visit,
            "as_of": fhir_datetime(hospital_now(get_store()))}


@step_activity("journey.triageAssist", "triageAssist")
def triage_assist(args: dict) -> dict:
    """The ED's triage behind the admission: CTAS and the times (the Control Tower predicted the admission there)."""
    ed = args.get("ed_visit")
    if not ed:
        return {"not_applicable": "elective admission, no ED triage"}
    fhir = _fhir()
    visit = fhir.read("Encounter", ed)
    ctas = fhir.read("Observation", f"ctas-{ed}")
    level = ctas.to_fhir().get("valueInteger") if ctas is not None else None
    arrived = ((visit.to_fhir().get("period") or {}).get("start")) if visit is not None else None
    refs = [f"Encounter/{ed}"] + ([f"Observation/ctas-{ed}"] if ctas is not None else [])
    return {"detail": f"CTAS {level if level is not None else '?'} at ED triage (visit {ed}, arrived "
                      f"{_hhmm(arrived)}); admitted {_hhmm(args.get('admitted_at'))}", "refs": refs}


@step_activity("journey.orderReview", "orderReview")
def order_review(args: dict) -> dict:
    result = _agent_step("order_review", args["encounter_id"],
                         lambda run: stubs.order_review(run, args.get("as_of") or args.get("admitted_at")))
    return _signoff(args, "orderReview", result, "pharmacist", "Review the admission orders")


@step_activity("journey.medRecAdmission", "medRecAdmission")
def med_rec_admission(args: dict) -> dict:
    as_of = args.get("orders_as_of") or args.get("as_of") or args.get("admitted_at")
    result = _agent_step("medication_reconciliation", args["encounter_id"],
                         lambda run: stubs.medrec_admission(run, as_of))
    return _signoff(args, "medRecAdmission", result, "pharmacist",
                    "Reconcile the home medications against the admission orders by hand")


@step_activity("journey.bedAssignment", "bedAssignment")
def bed_assignment(args: dict) -> dict:
    found = _fhir().read("Encounter", args["encounter_id"])
    if found is None:
        raise LookupError(f"Encounter/{args['encounter_id']} not found")
    bed, since = _current_bed(found.to_fhir())
    if bed is None or "-" not in bed:
        return {"detail": "No bed yet: the patient is boarding (the Control Tower follows it)"}
    unit = UNIT_BY_ID.get(bed.split("-")[0])
    return {"detail": f"Bed {bed} on {unit.name if unit else bed.split('-')[0]} since {_hhmm(since)} "
                      f"(assigned by the EHR at admission)", "refs": [f"Location/{bed}"]}


@step_activity("journey.predictDuration", "predictDuration")
def predict_duration(args: dict) -> dict:
    """The Control Tower's surgical-duration model (WP5) on the patient's case on today's OR list."""
    from app.modules.control_tower.snapshot import cache

    snap = cache.get(get_store())
    cases = sorted((c for c in snap.or_cases if c.patient_id == args.get("patient_id")
                    and c.status in ("booked", "arrived")), key=lambda c: c.booked_start)
    if not cases:
        return {"not_applicable": "no operating-room case for this patient on today's list"}
    c = cases[0]
    return {"case": {"appointment_id": c.appointment_id, "room": c.room, "procedure": c.procedure,
                     "booked_minutes": c.booked_minutes, "predicted_minutes": c.predicted_minutes,
                     "overrun_minutes": c.overrun_minutes},
            "detail": f"{c.procedure or 'Surgery'} in {c.room}: predicted {c.predicted_minutes or '?'} min against "
                      f"{c.booked_minutes} booked", "refs": [f"Appointment/{c.appointment_id}"]}


@step_activity("journey.proposeSchedule", "proposeSchedule")
def propose_schedule(args: dict) -> dict:
    case = args.get("case") or {}
    overrun = case.get("overrun_minutes")
    if overrun is None or overrun < 30:
        return {"detail": "The booking fits the prediction; nothing to change"}
    task = _engine_task(args, "proposeSchedule", "signoff",
                        f"OR booking: {case.get('procedure') or 'the case'} in {case.get('room')} is predicted to run "
                        f"{overrun} min over its {case.get('booked_minutes')} min booking. Adjust the list or confirm "
                        f"it.", "operations_manager", priority="asap", focus=f"Appointment/{case['appointment_id']}")
    ref = f"Task/{task}"
    bridge.register_wait(ref, args["workflow_id"], "proposeSchedule")
    return {"refs": [ref, f"Appointment/{case['appointment_id']}"], "wait_for": [ref], "signer": "operations_manager",
            "detail": f"Predicted {overrun} min over the booking: the OR coordinator decides"}


@step_activity("journey.news2Alert", "wardMonitoring")
def news2_alert(args: dict) -> dict:
    flag = args.get("flag")
    task = _engine_task(args, "wardMonitoring", "news2", "NEWS2 alert on this patient: assess now and record the "
                        "response (score and observations in the flag).", "nurse", priority="urgent", focus=flag)
    return {"detail": f"nurse Task {task}", "refs": [f"Task/{task}"]}


@step_activity("journey.draftDischargeSummary", "draftDischargeSummary")
def draft_discharge_summary(args: dict) -> dict:
    result = _agent_step("discharge_summary", args["encounter_id"], stubs.discharge_summary)
    return _signoff(args, "draftDischargeSummary", result, "physician", "Write the discharge summary")


@step_activity("journey.draftPatientInstructions", "draftPatientInstructions")
def draft_patient_instructions(args: dict) -> dict:
    result = _agent_step("patient_instructions", args["encounter_id"], stubs.patient_instructions)
    return _signoff(args, "draftPatientInstructions", result, "nurse", "Write the patient's discharge instructions")


@step_activity("journey.requestFollowupCall", "scheduleFollowupCall")
def request_followup_call(args: dict) -> dict:
    """The follow-up agent's consent check, then the call proposed for 48 h after discharge (hospital time) and its
    booking requested: bookAppointment is privileged, so the gateway opens an approval Task for a clerk."""
    check = _agent_step("followup_calls", args["encounter_id"], stubs.followup_consent)
    if check.refused:
        task = _engine_task(args, "scheduleFollowupCall", "manual",
                            f"Follow up with the patient by hand ({check.refused[:200]}).", "nurse")
        return {"mode": "manual", "refs": [f"Task/{task}"], "detail": "No automated call: a nurse follows up by hand"}
    discharged = parse(args.get("discharged_at")) if args.get("discharged_at") else None
    when = (discharged or hospital_now(get_store())) + timedelta(hours=48)
    when = when.replace(minute=0, second=0, microsecond=0)
    with runtime.start(ENGINE, user=None, encounter_id=args["encounter_id"]) as run:
        proposed = run.tool("proposeAppointment", {
            "encounter_id": args["encounter_id"], "service": "followup-call", "start": fhir_datetime(when),
            "minutes": 15, "description": "Follow-up call 48 hours after discharge. Placeholder: WP8's call service "
                                          "places the call."})
        if not proposed.ok:
            raise StepError(f"proposeAppointment {proposed.status}: {proposed.detail}")
        appointment = proposed.output["appointment_id"]
        booking = run.tool("bookAppointment", {"appointment_id": appointment})
        run.finish(outcome="needs_human" if booking.status == "approval_required" else booking.status)
    if booking.status != "approval_required" or not booking.approval_task_id:
        raise StepError(f"bookAppointment did not ask for an approval: {booking.status} {booking.detail}")
    ref = f"Task/{booking.approval_task_id}"
    bridge.register_wait(ref, args["workflow_id"], "scheduleFollowupCall")
    return {"mode": "approval", "approval_task_id": booking.approval_task_id, "appointment_id": appointment,
            "run_id": run.run_id, "refs": [f"Appointment/{appointment}", ref],
            "detail": f"Call proposed for {when:%a %H:%M} (hospital time); waiting for a clerk to approve the booking"}


@step_activity("journey.bookFollowupCall", "scheduleFollowupCall", max_attempts=M.PRIVILEGED_MAX_ATTEMPTS)
def book_followup_call(args: dict) -> dict:
    """The approved privileged call (0 automatic retries). When the clerk approved through the agents API the call
    already ran there; the idempotency key then returns that result."""
    with runtime.resume(args["run_id"]) as run:
        result = run.tool("bookAppointment", {"appointment_id": args["appointment_id"],
                                              "approval_task_id": args["approval_task_id"]})
        run.finish(outcome="completed" if result.ok else "needs_human")
    if not result.ok:
        raise StepError(f"bookAppointment {result.status}: {result.reason}: {result.detail}")
    return {"detail": f"Booked ({'already booked when the clerk approved' if result.status == 'replayed' else 'now'})",
            "refs": [f"Appointment/{args['appointment_id']}"]}


@step_activity("journey.codingStub", "codingStub")
def coding_stub(args: dict) -> dict:
    """8.1 roadmap card (coding assistance): nothing is written."""
    return {"detail": "Coding suggestion is a roadmap card (8.1); nothing written"}


# ---------- CapacityExceptionWorkflow ----------


def _exception(exception_id: str):
    from app.modules.control_tower import exceptions as X

    exc = X.get(get_store(), exception_id)
    if exc is None:
        raise LookupError(f"No exception {exception_id}")
    return exc


@step_activity("capacity.detect", "detect")
def capacity_detect(args: dict) -> dict:
    from app.modules.control_tower import exceptions as X

    exc = X.get(get_store(), args["exception_id"])
    if exc is None:
        return {"missing": f"No exception {args['exception_id']}"}
    return {"unit_id": exc.unit_id, "rule": exc.rule, "severity": exc.severity, "subject_ref": exc.subject_ref,
            "detail": f"{exc.title} ({exc.severity})", "refs": [exc.subject_ref, *exc.evidence_refs[:5]]}


@step_activity("capacity.explainAndRecommend", "explainAndRecommend")
def capacity_explain(args: dict) -> dict:
    from app.modules.control_tower import service

    exc = service.narrate(get_store(), args["exception_id"])
    n = exc.narration or {}
    actions = len(n.get("recommended_actions") or [])
    return {"detail": f"{'AI narrative' if n.get('source') == 'model' else 'Engine template'} with {actions} "
                      f"recommended action(s)" + (f"; review Task {exc.review_task_id}" if exc.review_task_id else ""),
            "refs": [f"Task/{exc.review_task_id}"] if exc.review_task_id else []}


@step_activity("capacity.execute", "execute")
def capacity_execute(args: dict) -> dict:
    from app.modules.control_tower import service

    exc = service.execute_actions(get_store(), args["exception_id"])
    actions = (exc.decision or {}).get("actions") or []
    tasks = [a["task_id"] for a in actions if a.get("task_id")]
    if len(tasks) < len(actions):
        missing = [a["action_id"] for a in actions if not a.get("task_id")]
        raise StepError(f"No Task for {', '.join(missing)}")
    return {"detail": f"{len(tasks)} Task(s) for the owner roles", "refs": [f"Task/{t}" for t in tasks]}


@step_activity("capacity.verify", "verify")
def capacity_verify(args: dict) -> dict:
    from app.modules.control_tower import service

    v = service.verify(get_store(), args["exception_id"])
    occ = (f"occupancy {v['occupancy_before_pct']}% -> {v['occupancy_after_pct']}%; "
           if v.get("occupancy_after_pct") is not None else "")
    done = sum(1 for t in v["tasks"] if t["status"] == "completed")
    return {**v, "detail": f"{occ}condition {'still present' if v['condition_present'] else 'cleared'}; "
                           f"{done} of {len(v['tasks'])} Task(s) completed"}


@step_activity("capacity.recordOutcome", "recordOutcome")
def capacity_record_outcome(args: dict) -> dict:
    from app.modules.control_tower import service

    verified = {k: v for k, v in (args.get("verified") or {}).items() if k not in ("detail", "refs")}
    exc = service.record_outcome(get_store(), args["exception_id"], args["decision"], verified or None)
    resolved = exc.outcome.get("resolved")
    return {"detail": f"{args['decision'].capitalize()}" + ("" if resolved is None else
                                                            "; resolved" if resolved else "; not resolved yet")}


# ---------- LowConfidenceReviewWorkflow ----------


def _review(review_id: str) -> reviews.AgentReview:
    review = reviews.get(review_id)
    if review is None:
        raise LookupError(f"No review {review_id}")
    return review


@step_activity("review.createReviewTask", "createReviewTask")
def review_create_task(args: dict) -> dict:
    review = reviews.get(args["review_id"])
    if review is None:
        return {"missing": f"No review {args['review_id']}"}
    spec = registry.require_agent(review.agent_id)
    role = next((r for r in spec.required_signoff_role if r in access.ROLE_CODE), "nurse")
    focus = next((r for r in review.output_refs if r.startswith(("Communication/", "DocumentReference/", "Task/"))),
                 None)
    task = _engine_task({**args, "encounter_id": review.encounter_id}, "reviewed", "review",
                        f"Low AI confidence ({review.confidence:.2f}, threshold {review.threshold:.2f}) in "
                        f"{review.agent_id}: confirm or correct its output; the correction becomes an eval case.",
                        role, focus=focus, inputs={"review": review.id, "run": review.run_id,
                                                   "agent": review.agent_id})
    ref = f"Task/{task}"
    bridge.register_wait(ref, args["workflow_id"], "reviewed")
    reviews.save(review.model_copy(update=dict(task_id=task, workflow_id=args["workflow_id"])))
    return {"task_id": task, "role": role, "encounter_id": review.encounter_id, "patient_id": review.patient_id,
            "refs": [ref, f"AgentRun/{review.run_id}"],
            "detail": f"{review.agent_id} at {review.confidence:.2f} (< {review.threshold:.2f}); review Task {task} "
                      f"for the {role}"}


@step_activity("review.writeCorrectedOutput", "writeCorrectedOutput")
def review_write_corrected(args: dict) -> dict:
    """The corrected (or confirmed) output on the run's trace, and a note on the work item the run created."""
    review = _review(args["review_id"])
    corrected = reviews.corrected_output(review)
    trace = traces.get(review.run_id)
    if trace is not None:
        trace.human_action = {**(trace.human_action or {}), "role": review.reviewer_role,
                              "user_id": review.reviewed_by, "decision": "corrected" if review.correction else
                              "confirmed", "corrected_fields": sorted((review.correction or {}).keys()),
                              "task_id": review.task_id, "at": datetime.now().isoformat(timespec="seconds")}
        traces.save(trace)
    refs = [f"AgentRun/{review.run_id}"]
    work = next((r for r in review.output_refs if r.startswith("Task/") and r != f"Task/{review.task_id}"), None)
    if review.correction and work:
        task = _fhir().read("Task", work.split("/", 1)[1])
        if task is not None:
            note = "AI output corrected by the " + (review.reviewer_role or "reviewer") + ": " + ", ".join(
                f"{k} {v}" for k, v in sorted(review.correction.items()) if k != "summary")
            result = _engine("updateTask", {"task_id": task.id, "status": task.status, "note": note[:1000]},
                             encounter_id=review.encounter_id, context_id=args["workflow_id"])
            if result.ok:
                refs.append(work)
    reviews.save(review.model_copy(update=dict(status="reviewed")))
    changed = ", ".join(f"{k}: {review.output.get(k)} -> {v}" for k, v in sorted((review.correction or {}).items()))
    return {"detail": f"Corrected ({changed})" if changed else "Confirmed as it was", "refs": refs,
            "corrected": {k: corrected.get(k) for k in evalsets.AGENTS.get(review.agent_id, {}).get("judged", ())}}


@step_activity("review.appendToEvalSet", "appendToEvalSet")
def review_append_case(args: dict) -> dict:
    review = _review(args["review_id"])
    if not evalsets.supported(review.agent_id):
        return {"not_applicable": f"{review.agent_id} has no eval set yet", "detail": "no eval set"}
    case = evalsets.case_from_review(review)
    added = evalsets.append_case(review.agent_id, case)
    reviews.save(review.model_copy(update=dict(case_id=case["id"], status="learned")))
    path = f"evals/{review.agent_id}/{evalsets.CASES}"
    return {"case_id": case["id"], "detail": f"Case {case['id']} {'added to' if added else 'already in'} {path} "
                                             f"(source human_review)", "refs": [path]}


@step_activity("review.triggerRegression", "triggerRegression")
def review_regression(args: dict) -> dict:
    """The agent's eval over all its cases; heartbeats per case (a long run on a live model)."""
    review = _review(args["review_id"])
    if not evalsets.supported(review.agent_id):
        return {"not_applicable": f"{review.agent_id} has no eval set yet"}
    report = evalsets.regression(review.agent_id, heartbeat=lambda progress_: activity.heartbeat(progress_))
    summary = {k: report[k] for k in ("cases", "passed", "accuracy", "gate", "status", "run_at", "mode")}
    reviews.save(_review(args["review_id"]).model_copy(update=dict(regression=summary)))
    return {**summary, "detail": f"{report['passed']} of {report['cases']} cases pass (accuracy {report['accuracy']}, "
                                 f"gate {report['gate']}): {report['status']}",
            "refs": [f"evals/{review.agent_id}/report.md"]}


ACTIVITIES = [record_progress, open_task, close_tasks, journey_context, triage_assist, order_review,
              med_rec_admission, bed_assignment, predict_duration, propose_schedule, news2_alert,
              draft_discharge_summary, draft_patient_instructions, request_followup_call, book_followup_call,
              coding_stub, capacity_detect, capacity_explain, capacity_execute, capacity_verify,
              capacity_record_outcome, review_create_task, review_write_corrected, review_append_case,
              review_regression]
