"""Low-confidence agent output for a person to review (spec 6.5 LowConfidenceReviewWorkflow), table `agent_reviews`.

When a run finishes with a confidence below its agent's `confidence_threshold` (registry), the runtime keeps
what the model saw and answered, de-identified exactly as it went to the model (`input`: the prompt variables;
`output`: the answer with the identifiers put back into tokens), and publishes `agent.low_confidence`. The bridge
starts a LowConfidenceReviewWorkflow for it; its review Task goes to the agent's signer, who confirms the output
or corrects it (`correct`). The corrected output then becomes the agent's next eval case (app/agents/evalsets.py).
Input and output never leave the server in identifiable form: they hold tokens like [PERSON_1], not names.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from app.core.models import StaffUser
from app.core.store import Store, get_store
from app.ehr.clock import hospital_now
from app.ehr.events import bus, platform_event

if TYPE_CHECKING:
    from app.agents.runtime import AgentRun

TABLE = "agent_reviews"


class AgentReview(BaseModel):
    id: str  # REV-00001
    run_id: str
    agent_id: str
    agent_version: str
    prompt_version: str | None = None
    encounter_id: str | None = None
    patient_id: str | None = None
    confidence: float
    threshold: float
    input: dict = Field(default_factory=dict)  # the prompt variables as the model got them (de-identified; encrypted)
    output: dict = Field(default_factory=dict)  # the model's output, de-identified (encrypted)
    output_refs: list[str] = Field(default_factory=list)  # what the run wrote
    status: Literal["open", "reviewed", "dismissed", "learned"] = "open"
    task_id: str | None = None  # the review Task the workflow opened
    workflow_id: str | None = None
    correction: dict | None = None  # the reviewer's corrected fields (encrypted)
    note: str | None = None
    reviewed_by: str | None = None
    reviewer_role: str | None = None
    reviewed_at: datetime | None = None
    case_id: str | None = None  # the eval case it became
    regression: dict | None = None  # the regression eval it triggered (summary)
    created_at: datetime


class ReviewProblem(ValueError):
    """The correction does not fit (unknown field or value, already reviewed)."""


def table(store: Store | None = None):
    return (store or get_store()).module(TABLE, dict)


def get(review_id: str, store: Store | None = None) -> AgentReview | None:
    return table(store).get(review_id)


def save(review: AgentReview, store: Store | None = None) -> None:
    table(store)[review.id] = review


def for_task(task_id: str, store: Store | None = None) -> AgentReview | None:
    return next((r for r in table(store).values() if r.task_id == task_id), None)


def maybe_open(run: AgentRun, confidence: float) -> AgentReview | None:
    """Called by the runtime when a run finishes: below the threshold, keep the case and tell the bus."""
    threshold = run.spec.confidence_threshold
    model = getattr(run, "last_model", None)
    if threshold is None or confidence >= threshold or not model:
        return None
    store = get_store()
    review = AgentReview(id=store.next_id("REV"), run_id=run.run_id, agent_id=run.spec.agent_id,
                         agent_version=run.spec.version, prompt_version=model.get("prompt_version"),
                         encounter_id=run.encounter_id, patient_id=run.patient_id, confidence=round(confidence, 4),
                         threshold=threshold, input=model.get("variables") or {}, output=model.get("output") or {},
                         output_refs=list(run.trace.output_refs), created_at=datetime.now())
    save(review, store)
    refs: dict = {}
    if run.encounter_id:
        refs["encounter"] = f"Encounter/{run.encounter_id}"
    if run.patient_id:
        refs["patient"] = f"Patient/{run.patient_id}"
    bus.publish(platform_event("agent.low_confidence", at=hospital_now(store), actor=f"agent:{run.spec.agent_id}",
                               refs=refs, key=f"low-confidence|{run.run_id}",
                               attrs={"agent_id": run.spec.agent_id, "run_id": run.run_id, "review_id": review.id,
                                      "confidence": f"{confidence:.2f}", "threshold": f"{threshold:.2f}"}))
    return review


def correct(user: StaffUser, review_id: str, correction: dict, note: str = "", *, source_ip: str | None = None) -> AgentReview:
    """The reviewer's corrected output; deciding the review Task tells the workflow (task.decided)."""
    from app.agents import approvals, evalsets, registry

    store = get_store()
    review = get(review_id, store)
    if review is None:
        raise LookupError(f"No review {review_id}")
    if review.status != "open" or review.task_id is None:
        raise ReviewProblem(f"{review_id} is {review.status}" + ("" if review.task_id else " without a review task"))
    spec = registry.require_agent(review.agent_id)
    if str(user.role) not in spec.required_signoff_role:
        raise approvals.DecisionDenied(f"{review.agent_id} output is reviewed by "
                                       f"{' or '.join(spec.required_signoff_role)}")
    fields = evalsets.correctable(review.agent_id)
    unknown = [k for k in correction if k not in fields]
    if unknown:
        raise ReviewProblem(f"Not correctable for {review.agent_id}: {', '.join(unknown)}")
    for key, value in correction.items():
        allowed = fields[key]
        if allowed and value not in allowed:
            raise ReviewProblem(f"{key} must be one of {', '.join(allowed)}")
        if not allowed and (not isinstance(value, str) or len(value) > 1000):
            raise ReviewProblem(f"{key} must be text of at most 1000 characters")
    changed = {k: v for k, v in correction.items() if review.output.get(k) != v}
    summary = ("corrected " + ", ".join(f"{k}: {review.output.get(k)} -> {v}" for k, v in changed.items())
               if changed else "confirmed as it was")
    approvals.decide(user, review.task_id, "approve", "; ".join(x for x in (summary, note) if x)[:1000],
                     source_ip=source_ip)
    review = get(review_id, store).model_copy(update=dict(
        correction=changed or None, note=note or None, status="reviewed", reviewed_by=user.id,
        reviewer_role=str(user.role), reviewed_at=datetime.now()))
    save(review, store)
    return review


def decided(task_id: str, decision: str, user: StaffUser) -> None:
    """A review Task decided in the general queue (no correction form): accept = confirmed, reject = dismissed."""
    store = get_store()
    review = for_task(task_id, store)
    if review is None or review.status != "open":
        return
    save(review.model_copy(update=dict(status="reviewed" if decision != "reject" else "dismissed",
                                       reviewed_by=user.id, reviewer_role=str(user.role),
                                       reviewed_at=datetime.now())), store)


def corrected_output(review: AgentReview) -> dict:
    return {**review.output, **(review.correction or {})}
