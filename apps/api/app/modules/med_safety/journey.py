"""WP6 in the inpatient journey (spec 6.5 and 7.2): the orderReview and medRecAdmission steps.

The journey's activities (app/workflows/activities.py) call these in their unit of work. They replace WP4c's
placeholders and return the same `StepResult` (what was written, what the workflow waits for):

- orderReview: the order review service reviews the stay's orders written by `as_of` (rules now; the AI commentary
  follows in the background or on demand, as for any order, so the step does not wait for a model). The
  order_review agent puts the result in the pharmacist's queue: a sign-off Task (createTask, workflow-signoff) that
  names the rule flags. The journey waits for that Task. The pharmacist confirms it in the review queue, or the
  workbench confirms it when the pharmacist decides the last flagged order of the stay (`confirm_signoff`).
- medRecAdmission: the admission reconciliation against the orders written by the order review's sign-off
  (`orders_as_of`), the agent's indication judgements and the draft record (draftMedRec). The journey waits for
  the record to be signed final: the pharmacist signs in the workbench when every discrepancy is handled, then a
  physician co-signs.

Retries. Temporal runs an activity again when it failed, also after its work committed. The order reviews and the
reconciliation are stored once per order and once per stay, so a retry finds them. Every side effect goes through
the Tool Gateway with arguments built only from that stored state, so the gateway's idempotency key returns the
first result: one sign-off Task, one MedRec record. The model is not asked again on a retry: its judgements are
stored with the reconciliation, and the retry sends the same draft again, which the gateway replays.
"""

from __future__ import annotations

import time

from app.agents import approvals, registry
from app.agents.library.journey_stubs import StepResult
from app.agents.runtime import AgentRun, runtime
from app.core.context import acting_as_system
from app.core.models import StaffUser
from app.core.store import Store
from app.fhir.dt import parse
from app.modules.med_safety import medrec_agent, rules, service
from app.modules.med_safety.models import WAITING, OrderReview, reviews

PRIORITY = {"blocking": "stat", "major": "urgent", "moderate": "asap"}
SHOWN = 6  # flagged orders named in the sign-off Task


# ---------- orderReview ----------


def review_orders(store: Store, encounter_id: str, as_of: str | None) -> list[OrderReview]:
    """The stay's orders written by `as_of` (hospital time), each reviewed once by the rules. Reviews are kept, so a
    retry gets the same list even when an order was stopped in between."""
    if service.module_fhir().read("Encounter", encounter_id) is None:
        raise LookupError(f"Encounter/{encounter_id} not found")
    service.review_encounter_orders(store, encounter_id)
    cutoff = parse(as_of)
    return sorted((r for r in reviews(store).find_by("encounter_id", encounter_id)
                   if cutoff is None or r.authored_at is None or r.authored_at <= cutoff), key=lambda r: r.order_ref)


def _rule_severity(r: OrderReview) -> str:
    """The rules' severity only: the review's own severity also counts the AI commentary, which arrives later."""
    return "blocking" if r.blocking else rules.most_severe([f["severity"] for f in r.findings])


def _label(r: OrderReview) -> str:
    name = r.order.get("name") or r.order.get("display") or r.order_ref
    top = max(r.findings, key=lambda f: rules.SEVERITY_RANK[f["severity"]])
    more = f" (+{len(r.findings) - 1} more)" if len(r.findings) > 1 else ""
    return f"{name}: {top['title']}{more}"


def signoff_text(found: list[OrderReview], as_of: str | None) -> tuple[str, str]:
    """(description, priority) of the sign-off Task, from the rule findings only (they never change after the review),
    so a retry builds the same text."""
    flagged = [r for r in found if r.findings]
    blocking = [r for r in flagged if r.blocking]
    when = parse(as_of)
    head = f"Pharmacist review of the stay's orders ({len(found)} written by {when:%a %H:%M} hospital time)" \
        if when else f"Pharmacist review of the stay's orders ({len(found)})"
    if not flagged:
        body = "none flagged by the rules. The AI commentary follows in Medication safety; confirm when reviewed."
    else:
        worst = sorted(flagged, key=lambda r: (-rules.SEVERITY_RANK[_rule_severity(r)], r.order_ref))
        names = "; ".join(_label(r) for r in worst[:SHOWN]) + ("; ..." if len(worst) > SHOWN else "")
        body = (f"{len(flagged)} flagged by the rules" + (f", {len(blocking)} blocking" if blocking else "")
                + f": {names}. Decide them in Medication safety: this Task is confirmed with the last decision "
                  f"(or confirm it here).")
    severity = rules.most_severe([_rule_severity(r) for r in flagged]) if flagged else "none"
    return f"{head}: {body}"[:1000], PRIORITY.get(severity, "routine")


def decided_before(found: list[OrderReview]) -> StepResult | None:
    """The pharmacist decided every flagged medication order of the stay in the workbench before the journey asked
    (WP9: the storyline's pharmacist works while the journey still waits for her bed): the sign-off those decisions
    give (`confirm_signoff`) is there already, so the step records it instead of asking again. None otherwise (a
    flagged order still pending, or nothing flagged: the pharmacist confirms the review)."""
    flagged = [r for r in found if r.findings and r.kind == "medication"]
    if not flagged or any(r.status == "pending" for r in flagged):
        return None
    return StepResult(refs=[r.order_ref for r in found],
                      detail=f"{len(found)} orders reviewed by the rules, {len(flagged)} flagged; every flagged order "
                             f"already decided by the pharmacist in Medication safety")


def order_review_signoff(run: AgentRun, found: list[OrderReview], as_of: str | None) -> StepResult:
    """The order_review agent puts the review in the pharmacist's queue (createTask through the Tool Gateway)."""
    description, priority = signoff_text(found, as_of)
    task = run.tool("createTask", {"encounter_id": run.encounter_id, "code": approvals.WORKFLOW_SIGNOFF,
                                   "performer_role": "pharmacist", "priority": priority, "description": description})
    if not task.ok:
        return StepResult(refused=f"{task.reason}: {task.detail}")
    ref = f"Task/{task.output['task_id']}"
    flagged = sum(1 for r in found if r.findings)
    return StepResult(refs=[ref, *(r.order_ref for r in found)], wait_for=[ref], signer="pharmacist",
                      detail=f"{len(found)} orders reviewed by the rules, {flagged} flagged; the pharmacist signs off "
                             f"in Medication safety")


def confirm_signoff(store: Store, user: StaffUser, review: OrderReview, *, source_ip: str | None = None) -> list[str]:
    """When the pharmacist has decided the last flagged medication order of a stay, the journey's open order review
    sign-off for it is confirmed too (approvals.decide: an `approve` audit event, task.decided for the workflow).
    Flagged lab and imaging orders stay with the physician. The Task is the agent's, or the workflow's own when the
    agent was refused (no consent, not deployable)."""
    if review.encounter_id is None or str(user.role) != "pharmacist":
        return []
    if any(r.status == "pending" and r.kind == "medication"
           for r in reviews(store).find_by("encounter_id", review.encounter_id)):
        return []
    _RECHECK[review.encounter_id] = (review, user.id, time.monotonic() + RECHECK_S)
    done = []
    for task in approvals.queue(user, limit=200):
        if task["kind"] == approvals.WORKFLOW_SIGNOFF and task["encounter_id"] == review.encounter_id \
                and (task["agent_id"] == service.ORDER_REVIEW or task["step"] == "orderReview"):
            try:
                approvals.decide(user, task["task_id"], "approve", "Every flagged order of the stay decided in "
                                 "Medication safety", source_ip=source_ip)
            except (approvals.DecisionError, approvals.DecisionDenied):  # decided meanwhile: the order decision stands
                continue
            done.append(task["task_id"])
    return done


# The journey's sign-off Task can be written while the pharmacist's last decision is being saved: the activity read
# the order as still pending, and the decision looked for a Task not yet committed, so neither saw the other (WP9's
# Temporal run hit it). For two minutes after such a decision the API's background loop asks again.
RECHECK_S, RECHECK_EVERY_S = 120.0, 5.0
_RECHECK: dict[str, tuple[OrderReview, str, float]] = {}  # encounter -> (the last decided review, user id, until)
_LAST = {"at": 0.0}


def recheck_signoffs(store: Store) -> list[str]:
    """Background step (after the intake): `confirm_signoff` again for the stays decided in the last two minutes."""
    if not _RECHECK or time.monotonic() - _LAST["at"] < RECHECK_EVERY_S:
        return []
    _LAST["at"] = time.monotonic()
    done: list[str] = []
    for encounter_id, (review, user_id, until) in list(_RECHECK.items()):
        user = store.staff.get(user_id)
        if time.monotonic() > until or user is None:
            _RECHECK.pop(encounter_id, None)
            continue
        found = confirm_signoff(store, user, review)
        if found:
            _RECHECK.pop(encounter_id, None)
            done += found
    return done


# ---------- medRecAdmission ----------


def _redraft(session) -> str | None:
    """A retry after the draft was written: the same draftMedRec call again (the gateway returns the first result)."""
    payload = session.model_dump(mode="json")
    try:
        with acting_as_system(), runtime.start(medrec_agent.AGENT_ID, user=None, encounter_id=session.encounter_id,
                                               context_id=session.id) as run:
            document_id = medrec_agent.write_draft(run, payload, service._items(session))
            run.finish(outcome="completed" if document_id else "needs_human")
    except (registry.AgentNotDeployable, registry.UnregisteredAgent):
        return session.document_id
    return document_id or session.document_id


def medrec_admission(store: Store, encounter_id: str, orders_as_of: str | None) -> StepResult:
    """The admission reconciliation (once per stay), settled by the agent, and its record for the signatures. Without
    the AI (no consent, not deployable, the model down) the module writes the draft itself and the step still
    waits for the signed record."""
    session = service.reconcile(store, encounter_id, "admission", orders_as_of=parse(orders_as_of))
    if session.ai_status in WAITING:  # the background has not run it, or leaves it to whoever opens it
        session = service.settle(store, session.id, wait=True)
    elif session.ai_status == "done" and session.status == "open":
        document_id = _redraft(session)
        if document_id != session.document_id:
            raise RuntimeError(f"draftMedRec returned {document_id}, the reconciliation has {session.document_id}")
    if not session.document_id:
        raise RuntimeError(f"Reconciliation {session.id} has no record yet")
    ref = f"DocumentReference/{session.document_id}"
    items = service._items(session)
    refs = [ref, *sorted({r for i in items for r in i.evidence_refs})][:30]
    if session.status == "final":  # signed in the workbench before the journey got here (the intake opened it)
        return StepResult(refs=refs, signer="pharmacist", detail=f"Reconciliation {session.id} already signed final "
                                                                 f"in Medication safety")
    discrepancies = sum(1 for i in items if i.discrepancy)
    return StepResult(refs=refs, wait_for=[ref],
                      signer="pharmacist",
                      detail=f"Reconciliation {session.id}: {len(items)} medications, {discrepancies} discrepancies "
                             f"for the pharmacist ({'AI judgements' if session.ai_status == 'done' else 'rules only'}"
                             f"); signed by the pharmacist, co-signed by a physician")
