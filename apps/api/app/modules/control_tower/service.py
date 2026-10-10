"""The Control Tower's services (spec 7.1): refresh, narration, the action drawer, views per role, the patient card.

- `refresh`: the snapshot (cached while nothing changed) through the rule engine
  into the exception stream. The API's hospital loop runs it after every event
  drain; the board request runs it too, so the stream is current without the loop.
- `narrate`: the narrator agent for an exception, as itself (not as whoever opens
  the drawer), then every evidence reference is checked against FHIR before it
  is stored for display.
- The action drawer: `approve` (the decision on the exception's review Task is the
  approval record, then one Task per approved action for its owner role, written
  by the agent through createFlowTask), `reject` (with a reason), `defer` (with a
  reminder time on the hospital clock). The AI never executes an action. Every
  decision is published as `exception.decided`; while the exception's
  CapacityExceptionWorkflow runs (6.5, app/workflows/capacity.py) the approved
  actions are queued and the workflow executes them (`execute_actions`, with
  Temporal's retries and the same idempotency keys), re-checks the occupancy an
  hour later (`verify`) and records the outcome (`record_outcome`); without
  Temporal they are executed in the request, as before.
- Views: the bed manager (operations manager) sees the boards hospital-wide, a
  patient as MRN and bed only; a nurse or physician sees MRNs only for patients of
  their units (or whom they attend) and the prediction values; the patient card
  is read through FhirGateway as the signed-in user, so the 6.3 rules apply.
"""

from __future__ import annotations

import threading
import zlib
from datetime import datetime, timedelta

from fastapi import Request
from sqlalchemy import text

from app.agents import approvals, registry
from app.agents.runtime import runtime
from app.core.context import acting_as_system
from app.core.models import StaffUser
from app.core.store import Store, get_store
from app.ehr import access
from app.ehr.clock import hospital_now
from app.ehr.gateway import Actor, FhirGateway
from app.ehr.reference import UNIT_BY_ID
from app.fhir.dt import parse, ref_id
from app.modules.control_tower import agent, rules, tools
from app.modules.control_tower import exceptions as X
from app.modules.control_tower import features as F
from app.modules.control_tower import fhirview as V
from app.modules.control_tower.snapshot import MODULE, Snapshot, cache, tower_fhir

VIEWERS = ("operations_manager", "nurse", "physician")
APPROVERS = ("operations_manager", "nurse")  # the bed manager and the charge nurses (7.1)
DEFER_MINUTES = (15, 720)
_processed = {"key": None}
_lock = threading.Lock()


class DecisionProblem(ValueError):
    """The decision cannot be made in the exception's current state (409)."""


# ---------- refresh ----------


def refresh(store: Store, *, force: bool = False) -> Snapshot:
    """The current snapshot; when it changed since the last refresh, the rules run and the stream is synced."""
    snap = cache.get(store)
    with _lock:
        if not force and _processed["key"] == snap.key:
            return snap
        _processed["key"] = snap.key
    X.sync(store, rules.evaluate(snap), snap.now)
    return snap


def reset_state() -> None:
    """Forget the cached snapshot (tests; a demo reset)."""
    cache.clear()
    _processed["key"] = None


def refresh_step(store: Store) -> None:
    """Background step of the API's hospital loop (after the event drain)."""
    refresh(store)


def narrate_step(store: Store, limit: int = 2) -> None:
    """Background step: narrate the most severe exceptions still waiting for a narrative."""
    for exc in X.pending_narration(store)[:limit]:
        narrate(store, exc.id)


# ---------- narration ----------


def _lock_exception(store: Store, exception_id: str) -> None:
    """One narration or execution per exception at a time (the narrator loop, a request and the workflow's
    activity may all try): the second waits, then finds it done."""
    if not store.detached:
        store.conn().execute(text("SELECT pg_advisory_xact_lock(:k)"),
                             {"k": zlib.crc32(f"flow-exception:{exception_id}".encode())})


def narrate(store: Store, exception_id: str) -> X.FlowException:
    _lock_exception(store, exception_id)
    exc = X.fresh(store, exception_id)  # another narrator may have finished while we waited for the lock
    if exc is None:
        raise LookupError(f"No exception {exception_id}")
    if exc.narration_status == "done":
        return exc
    payload = exc.model_dump(mode="json")
    narration: dict
    try:
        with acting_as_system(), runtime.start(agent.AGENT_ID, user=None, context_id=exc.id) as run:
            result = agent.narrate(run, payload)
        narration = {**result.output, "source": result.source, "ai_status": result.ai_status,
                     "attempts": result.attempts, "problems": result.problems, "run_id": result.run_id,
                     "evaluated": result.evaluated, "context_refs": result.context_refs}
        review_task = result.review_task_id
    except (registry.AgentNotDeployable, registry.UnregisteredAgent) as e:  # e.g. prod mode before the eval passed
        narration = {**agent.template(payload), "source": "template", "ai_status": "refused", "attempts": 0,
                     "problems": [str(e)], "run_id": None, "evaluated": False, "context_refs": []}
        review_task = None
    # every evidence reference must resolve in FHIR before anything shows it
    refs = narration["evidence_refs"]
    resolved = tower_fhir().resolve_refs(refs)
    narration["unresolved_refs"] = [r for r in refs if r not in resolved]
    narration["evidence_refs"] = [r for r in refs if r in resolved]
    if not narration["evidence_refs"]:  # nothing the model cited resolved: the engine's own references that do
        engine = tower_fhir().resolve_refs(exc.evidence_refs)
        narration["evidence_refs"] = [r for r in exc.evidence_refs if r in engine]
    narration["narrated_at"] = hospital_now(store).isoformat()
    narration["facts_as_of"] = exc.changed_at.isoformat()
    exc = exc.model_copy(update=dict(narration=narration, narration_status="done",
                                     review_task_id=review_task or exc.review_task_id))
    X.save(store, exc)
    return exc


# ---------- the action drawer ----------


def _approver(user: StaffUser, exc: X.FlowException) -> None:
    role = str(user.role)
    if role not in APPROVERS:
        raise approvals.DecisionDenied("Control Tower actions are approved by the bed manager or a charge nurse")
    if role == "nurse" and exc.unit_id not in user.unit_ids:
        raise approvals.DecisionDenied(f"{exc.unit_id} is not one of your units")


def can_decide(user: StaffUser, exc: X.FlowException) -> bool:
    try:
        _approver(user, exc)
    except approvals.DecisionDenied:
        return False
    return exc.status in X.LIVE and exc.cleared_at is None


def _decidable(store: Store, user: StaffUser, exception_id: str) -> X.FlowException:
    exc = X.get(store, exception_id)
    if exc is None:
        raise LookupError(f"No exception {exception_id}")
    _approver(user, exc)
    if exc.status not in X.LIVE:
        raise DecisionProblem(f"{exception_id} is already {exc.status}")
    if exc.cleared_at is not None:
        raise DecisionProblem(f"{exception_id} has cleared")
    if exc.narration_status != "done":
        exc = narrate(store, exception_id)
    return exc


def _decision(user: StaffUser, decision: str, now: datetime, **extra) -> dict:
    return {"decision": decision, "by": user.id, "name": user.name, "role": str(user.role), "at": now.isoformat(),
            **extra}


def _audit_without_review(store: Store, user: StaffUser, exc: X.FlowException, action: str, note: str,
                          source_ip: str | None) -> None:
    """The approval record when no review Task exists (the agent was refused, e.g. in prod mode)."""
    store.audit.record(user_id=user.id, user_name=user.name, role=str(user.role), action=action, event_type="approve",
                       resource_type="FlowException", resource_id=exc.id, outcome="allowed", source_ip=source_ip,
                       reason=note[:200], module=MODULE)


def approve(store: Store, user: StaffUser, exception_id: str, action_ids: list[str], note: str = "", *,
            source_ip: str | None = None) -> X.FlowException:
    exc = _decidable(store, user, exception_id)
    menu = {m["action_id"]: m for m in exc.menu}
    chosen = list(dict.fromkeys(action_ids))
    if not chosen:
        raise DecisionProblem("Choose at least one action to approve")
    unknown = [a for a in chosen if a not in menu]
    if unknown:
        raise DecisionProblem(f"Not actions of this exception: {', '.join(unknown)}")
    summary = f"approved {', '.join(chosen)}" + (f": {note}" if note else "")
    if exc.review_task_id:
        approvals.decide(user, exc.review_task_id, "approve", summary, source_ip=source_ip)
    else:
        _audit_without_review(store, user, exc, "approve", summary, source_ip)
    now = hospital_now(store)
    workflow_id = _running_workflow(store, exc)
    if workflow_id:  # the CapacityExceptionWorkflow executes them (6.5), with Temporal's retries
        done = [{"action_id": a, "label": menu[a]["label"], "owner_role": menu[a]["owner_role"], "task_id": None,
                 "status": "queued", "detail": "The workflow creates this Task"} for a in chosen]
        run_id = None
    else:
        done, run_id = _execute(exc, _action_calls(exc, chosen, user.id), str(user.role))
    exc = exc.model_copy(update=dict(status="approved", remind_at=None,
                                     decision=_decision(user, "approved", now, note=note, actions=done,
                                                        execution_run=run_id, workflow_id=workflow_id,
                                                        executed_by="workflow" if workflow_id else "request")))
    X.save(store, exc)
    X.publish(exc, "exception.decided", now, actor=f"user:{user.id}", key=f"exception.decided|{exc.id}|approved",
              attrs={"decision": "approved", "role": str(user.role)})
    return exc


def _running_workflow(store: Store, exc: X.FlowException) -> str | None:
    """The exception's CapacityExceptionWorkflow, when Temporal is on and the workflow runs."""
    from app.workflows import bridge

    return bridge.running_capacity_workflow(store, exc.id)


def _action_calls(exc: X.FlowException, action_ids: list[str], approved_by: str) -> list[tuple[dict, dict]]:
    """The createFlowTask arguments of each approved action. Built only from what the exception stores, so the
    request and the workflow (and every retry) send the same arguments and the gateway's idempotency key holds."""
    menu = {m["action_id"]: m for m in exc.menu}
    recommended = {a["action_id"]: a for a in (exc.narration or {}).get("recommended_actions", [])}
    priority = {"high": "urgent", "med": "asap", "low": "routine"}[exc.severity]
    calls = []
    for action_id in action_ids:
        item = menu[action_id]
        why = recommended.get(action_id, {}).get("rationale") or item["why"]
        args = {"exception_id": exc.id, "action_id": action_id, "unit_id": exc.unit_id,
                "description": f"{item['label']} ({why})"[:1000], "performer_role": item["owner_role"],
                "priority": priority, "review_task_id": exc.review_task_id or exc.id, "approved_by": approved_by}
        if item.get("encounter_id"):
            args["encounter_id"] = item["encounter_id"]
        calls.append((item, args))
    return calls


def _execute(exc: X.FlowException, calls: list[tuple[dict, dict]], approver_role: str | None,
             already: dict[str, str] | None = None) -> tuple[list[dict], str | None]:
    """One Task per approved action: by the agent through createFlowTask (idempotent per exception and action),
    or, without a deployable agent, by the module (skipping actions whose Task was written before)."""
    already = already or {}
    done: list[dict] = []
    run_id = None
    try:
        with acting_as_system(), runtime.start(agent.AGENT_ID, user=None, context_id=exc.id) as run:
            run_id = run.run_id
            for item, args in calls:
                result = run.tool("createFlowTask", args)
                done.append({"action_id": item["action_id"], "label": item["label"], "owner_role": item["owner_role"],
                             "task_id": (result.output or {}).get("task_id"), "status": result.status,
                             "detail": None if result.ok else (result.detail or result.reason)})
            run.trace.human_action = {"role": approver_role, "user_id": calls[0][1]["approved_by"] if calls else None,
                                      "decision": "approve", "task_id": exc.review_task_id,
                                      "at": datetime.now().isoformat(timespec="seconds")}
            run.finish(outcome="completed" if all(d["task_id"] for d in done) else "needs_human")
    except (registry.AgentNotDeployable, registry.UnregisteredAgent):  # no agent (prod mode before its eval passed)
        fhir = tower_fhir()
        for item, args in calls:
            if already.get(item["action_id"]):
                done.append({"action_id": item["action_id"], "label": item["label"], "owner_role": item["owner_role"],
                             "task_id": already[item["action_id"]], "status": "ok", "detail": "written before"})
                continue
            encounter = fhir.read("Encounter", args["encounter_id"]).to_fhir() if args.get("encounter_id") else None
            task = fhir.create(tools.flow_task(exc.id, item["action_id"], exc.unit_id, args["description"],
                                               item["owner_role"], args["priority"], exc.review_task_id,
                                               args["approved_by"], encounter=encounter))
            done.append({"action_id": item["action_id"], "label": item["label"], "owner_role": item["owner_role"],
                         "task_id": task.id, "status": "ok", "detail": "written without the agent"})
    return done, run_id


# ---------- the workflow's steps (6.5 CapacityExceptionWorkflow) ----------


def execute_actions(store: Store, exception_id: str) -> X.FlowException:
    """The approved actions of an exception, executed by its workflow (a retried activity replays the Tasks)."""
    _lock_exception(store, exception_id)
    exc = X.fresh(store, exception_id)
    if exc is None:
        raise LookupError(f"No exception {exception_id}")
    decision = exc.decision or {}
    if exc.status != "approved" or decision.get("decision") != "approved":
        raise DecisionProblem(f"{exception_id} is {exc.status}, not approved")
    actions = decision.get("actions") or []
    already = {a["action_id"]: a["task_id"] for a in actions if a.get("task_id")}
    calls = _action_calls(exc, [a["action_id"] for a in actions], decision["by"])
    done, run_id = _execute(exc, calls, decision.get("role"), already)
    exc = exc.model_copy(update=dict(decision={**decision, "actions": done,
                                               "execution_run": run_id or decision.get("execution_run"),
                                               "executed_at": hospital_now(store).isoformat()}))
    X.save(store, exc)
    return exc


def verify(store: Store, exception_id: str) -> dict:
    """An hour after execution: the unit's occupancy now against the facts the exception opened with, whether the
    condition still holds, and how far the approved Tasks have got."""
    exc = X.get(store, exception_id)
    if exc is None:
        raise LookupError(f"No exception {exception_id}")
    snap = cache.get(store)
    unit = next((u for u in snap.units if u.id == exc.unit_id), None)
    live = {d.key for d in rules.evaluate(snap)}
    fhir = tower_fhir()
    tasks = []
    for a in (exc.decision or {}).get("actions") or []:
        task = fhir.read("Task", a["task_id"]) if a.get("task_id") else None
        tasks.append({"action_id": a["action_id"], "task_id": a.get("task_id"),
                      "status": task.status if task is not None else None})
    return {"unit_id": exc.unit_id, "occupancy_before_pct": exc.facts.get("occupancy_pct"),
            "occupancy_after_pct": round(100 * unit.occupancy) if unit else None,
            "condition_present": exc.key in live, "checked_at": snap.now.isoformat(), "tasks": tasks}


def record_outcome(store: Store, exception_id: str, decision: str, verified: dict | None = None) -> X.FlowException:
    """The end of the loop (6.6 counts how often recommendations are taken and whether they worked)."""
    exc = X.get(store, exception_id)
    if exc is None:
        raise LookupError(f"No exception {exception_id}")
    outcome = {"decision": decision, "recorded_at": hospital_now(store).isoformat(),
               "narration_source": (exc.narration or {}).get("source"),
               "decided_by_role": (exc.decision or {}).get("role"), "verified": verified or None,
               "resolved": None if not verified else not verified.get("condition_present")}
    exc = exc.model_copy(update=dict(outcome=outcome))
    X.save(store, exc)
    return exc


def reject(store: Store, user: StaffUser, exception_id: str, reason: str, *,
           source_ip: str | None = None) -> X.FlowException:
    reason = " ".join((reason or "").split())
    if len(reason) < 5:
        raise DecisionProblem("Give a reason for rejecting (at least 5 characters)")
    exc = _decidable(store, user, exception_id)
    if exc.review_task_id:
        approvals.decide(user, exc.review_task_id, "reject", reason, source_ip=source_ip)
    else:
        _audit_without_review(store, user, exc, "reject", reason, source_ip)
    now = hospital_now(store)
    exc = exc.model_copy(update=dict(status="rejected", remind_at=None,
                                     decision=_decision(user, "rejected", now, note=reason)))
    X.save(store, exc)
    X.publish(exc, "exception.decided", now, actor=f"user:{user.id}", key=f"exception.decided|{exc.id}|rejected",
              attrs={"decision": "rejected", "role": str(user.role)})
    return exc


def defer(store: Store, user: StaffUser, exception_id: str, minutes: int, note: str = "", *,
          source_ip: str | None = None) -> X.FlowException:
    if not DEFER_MINUTES[0] <= minutes <= DEFER_MINUTES[1]:
        raise DecisionProblem(f"Defer by {DEFER_MINUTES[0]} to {DEFER_MINUTES[1]} minutes")
    exc = _decidable(store, user, exception_id)
    now = hospital_now(store)
    remind = now + timedelta(minutes=minutes)
    store.audit.record(user_id=user.id, user_name=user.name, role=str(user.role), action="defer", event_type="write",
                       resource_type="FlowException", resource_id=exc.id, outcome="allowed", source_ip=source_ip,
                       reason=f"deferred until {remind:%Y-%m-%d %H:%M} (hospital time)"
                              + (f"; review task {exc.review_task_id}" if exc.review_task_id else "")
                              + (f"; {note[:120]}" if note else ""), module=MODULE)
    exc = exc.model_copy(update=dict(status="deferred", remind_at=remind,
                                     decision=_decision(user, "deferred", now, note=note,
                                                        remind_at=remind.isoformat())))
    X.save(store, exc)
    X.publish(exc, "exception.decided", now, actor=f"user:{user.id}",
              key=f"exception.decided|{exc.id}|deferred|{exc.reminders}",
              attrs={"decision": "deferred", "role": str(user.role)})
    return exc


# ---------- views per role ----------


def _scope(user: StaffUser) -> tuple[bool, set[str] | None]:
    """(clinical role, the units whose patients the user may identify; None: the whole hospital)."""
    role = str(user.role)
    policy = access.POLICIES.get(role, access.NO_ACCESS)
    return role in ("physician", "nurse"), (set(user.unit_ids) if policy.scope == "unit" else None)


def _identify(units: set[str] | None, user: StaffUser, unit: str | None, attending: str | None) -> bool:
    return units is None or (unit in units) or (bool(attending) and attending == user.practitioner_id)


def _explained(value, clinical: bool) -> dict | None:
    if value is None:
        return None
    return {"value": value.value, "sentence": value.sentence if clinical else value.sentence_names}


def exception_view(exc: X.FlowException, *, detail: bool = False) -> dict:
    out = {"id": exc.id, "key": exc.key, "rule": exc.rule, "severity": exc.severity, "status": exc.status,
           "title": exc.title, "summary": exc.summary, "unit_id": exc.unit_id, "subject_ref": exc.subject_ref,
           "detected_at": exc.detected_at, "changed_at": exc.changed_at, "cleared_at": exc.cleared_at,
           "remind_at": exc.remind_at, "reminders": exc.reminders, "narration_status": exc.narration_status,
           "decision": exc.decision, "review_task_id": exc.review_task_id,
           "narrative": (exc.narration or {}).get("narrative"), "outcome": exc.outcome}
    if detail:
        n = exc.narration or {}
        out.update(facts=exc.facts, menu=exc.menu, engine_evidence=exc.evidence_refs, narration={
            k: n.get(k) for k in ("narrative", "recommended_actions", "evidence_refs", "unresolved_refs", "source",
                                  "ai_status", "attempts", "problems", "run_id", "evaluated", "narrated_at",
                                  "facts_as_of", "severity")} if n else None)
    return out


def board_view(store: Store, user: StaffUser, snap: Snapshot) -> dict:
    clinical, units = _scope(user)
    k = snap.kpis
    ed = []
    for row in snap.ed:
        ident = _identify(units, user, "ED", row.attending)
        ed.append({"encounter_id": row.encounter_id, "mrn": row.mrn if ident else None, "identified": ident,
                   "location": row.location, "status": row.status, "arrival": row.arrival, "ctas": row.ctas,
                   "minutes_in_ed": row.minutes_in_ed, "waiting_to_be_seen": row.waiting_to_be_seen,
                   "wait_minutes": row.wait_minutes, "admit": _explained(row.admit, clinical),
                   "orders_total": row.orders_total, "orders_open": row.orders_open,
                   "bed_requested_at": row.bed_requested_at, "boarding_minutes": row.boarding_minutes,
                   "target_unit": row.target_unit, "target_unit_source": row.target_unit_source,
                   "lwbs_risk": row.lwbs_risk})
    cases = []
    for c in snap.or_cases:
        ident = _identify(units, user, "OR", None)
        cases.append({**c.model_dump(mode="json", exclude={"predicted", "mrn", "patient_id", "preop_tasks"}),
                      "mrn": c.mrn if ident else None, "identified": ident,
                      "predicted": _explained(c.predicted, clinical)})
    return {
        "now": snap.now, "built_at": snap.built_at, "build_ms": snap.build_ms, "models": snap.models,
        "role": str(user.role), "can_decide": str(user.role) in APPROVERS,
        "kpis": {**k.model_dump(mode="json", exclude={"ed_predicted_wait"}),
                 "ed_predicted_wait": _explained(k.ed_predicted_wait, True)},
        "ed": ed, "units": [u.model_dump(mode="json") for u in snap.units], "or_cases": cases,
        "or_rooms": [r.model_dump(mode="json") for r in snap.or_rooms],
        "exceptions": [exception_view(e) for e in X.stream(store)],
    }


def unit_view(user: StaffUser, snap: Snapshot, unit_id: str) -> dict:
    if unit_id not in snap.beds:
        raise LookupError(f"No unit {unit_id}")
    clinical, units = _scope(user)
    cells = []
    for c in snap.beds[unit_id]:
        ident = _identify(units, user, unit_id, c.attending)
        cells.append({"id": c.id, "room": c.room, "status": c.status, "encounter_id": c.encounter_id,
                      "mrn": c.mrn if ident else None, "identified": ident, "since": c.since,
                      "admitted_at": c.admitted_at, "expected_discharge": c.expected_discharge, "days_in": c.days_in,
                      "alc": c.alc, "fall_risk": c.fall_risk if clinical else None,
                      "discharge": _explained(c.discharge, clinical)})
    unit = next((u for u in snap.units if u.id == unit_id), None)
    return {"unit": unit.model_dump(mode="json") if unit else {"id": unit_id, "name": UNIT_BY_ID[unit_id].name},
            "beds": cells, "now": snap.now}


def _journey(encounter_id: str) -> dict | None:
    """The encounter's InpatientJourneyWorkflow (6.5), for the card's link to the workflow view."""
    from app.workflows import progress

    run = progress.for_encounter(encounter_id)
    return progress.summary(run) if run is not None else None


CARD_FIELDS = ("mrn", "bed", "admitted_at", "expected_discharge", "name", "gender", "age", "reason", "attending",
               "flags", "vitals", "open_orders", "discharge")
OPS_FIELDS = ("mrn", "bed", "admitted_at", "expected_discharge")  # 7.1: the bed manager sees only these


def patient_card(user: StaffUser, request: Request | None, encounter_id: str, snap: Snapshot) -> dict:
    """The drill-down card, read through FhirGateway as the user (unit scope, reduced views, break-glass)."""
    fhir = FhirGateway(Actor.of(user, request), MODULE)
    encounter = fhir.read("Encounter", encounter_id)  # BreakGlassRequired / FhirAccessDenied -> 403
    if encounter is None:
        raise LookupError(f"No encounter {encounter_id}")
    enc = encounter.to_fhir()
    pid = access.patient_of(enc)
    patient = fhir.read("Patient", pid).to_fhir() if pid else {}
    started = V.start(enc)
    expected_days = V.ext(enc, "expected-los-days")
    card: dict = {"encounter_id": encounter_id, "class": (enc.get("class") or {}).get("code"),
                  "status": enc.get("status"), "mrn": V.mrn(patient), "bed": V.current_location(enc),
                  "admitted_at": started,
                  "expected_discharge": started + timedelta(days=float(expected_days))
                  if started and expected_days else None, "journey": _journey(encounter_id)}
    role = str(user.role)
    if role == "operations_manager":
        card["hidden"] = [f for f in CARD_FIELDS if f not in OPS_FIELDS]
        card["role"] = role
        return card
    name = next((n.get("text") or " ".join([*(n.get("given") or []), n.get("family") or ""]).strip()
                 for n in patient.get("name") or [] if n.get("use") in (None, "official")), None)
    now = hospital_now(get_store())
    flags = [V.code(f.get("code")) for f in (r.to_fhir() for r in fhir.get_encounter_resources("Flag", encounter_id))
             if f.get("status") == "active"]
    panels = fhir.get_observations(encounter_id, [F.VITAL_PANEL])
    latest = V.vitals(panels[-1].to_fhir()) if panels else {}
    orders = [o for o in fhir.get_orders(encounter_id) if o.status == "active"]
    cell = next((c for cells in snap.beds.values() for c in cells if c.encounter_id == encounter_id), None)
    card.update(
        role=role, hidden=[], name=name, gender=patient.get("gender"), age=V.age(patient, now),
        reason=((enc.get("reasonCode") or [{}])[0]).get("text") if enc.get("reasonCode") else None,
        attending=ref_id((enc.get("participant") or [{}])[0].get("individual")) if enc.get("participant") else None,
        flags=sorted(f for f in flags if f), vitals={k: latest.get(code) for k, code in (
            ("heart_rate", "8867-4"), ("systolic_bp", "8480-6"), ("resp_rate", "9279-1"), ("temperature", "8310-5"),
            ("spo2", "59408-5"))} if latest else None,
        vitals_at=parse(panels[-1].to_fhir().get("effectiveDateTime")) if panels else None,
        open_orders=len(orders), discharge=_explained(cell.discharge, True) if cell else None)
    return card
