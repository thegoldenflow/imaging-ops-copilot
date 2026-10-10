"""The timeline each workflow keeps for the workflow view (spec 6.5 UI), table `workflow_runs`.

The workflow is the source: at every change it sends its whole timeline through the activity
`workflows.recordProgress` (a newer version replaces an older one, so a retried call never goes back). Between
two of those an activity marks its own failed attempts ("failed, retrying"), so the view shows Temporal's
retries as they happen. Reading it needs neither Temporal nor the worker: the view still works while the worker
is down (and shows what was last recorded when Temporal is off).

A step that completes (or is skipped) publishes `workflow.step_completed` in the same transaction, so the
Control Tower's boards and anyone else on the bus follow the workflows.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.core.store import Store, get_store
from app.ehr.clock import hospital_now
from app.ehr.events import bus, platform_event
from app.workflows import model as M

TABLE = "workflow_runs"


class WorkflowRun(BaseModel):
    id: str  # the workflow id (journey-stay-00012, capacity-EXC-00003, review-RUN-00042)
    workflow_type: str
    status: str  # running, completed, failed (a workflow that could not start its work)
    current_step: str | None = None
    version: int = 0
    encounter_id: str | None = None
    patient_id: str | None = None
    unit_id: str | None = None
    exception_id: str | None = None
    run_id: str | None = None  # the agent run under review
    steps: list[dict] = Field(default_factory=list)  # encrypted at rest
    notes: list[dict] = Field(default_factory=list)  # encrypted at rest
    meta: dict = Field(default_factory=dict)
    temporal_run_id: str | None = None
    started_at: datetime | None = None  # wall clock (Temporal's)
    updated_at: datetime | None = None
    finished_at: datetime | None = None


def table(store: Store | None = None):
    return (store or get_store()).module(TABLE, dict)


def get(workflow_id: str, store: Store | None = None) -> WorkflowRun | None:
    return table(store).get(workflow_id)


def all_runs(store: Store | None = None) -> list[WorkflowRun]:
    return list(table(store).values())


def for_encounter(encounter_id: str, store: Store | None = None) -> WorkflowRun | None:
    return get(M.journey_id(encounter_id), store)


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed.astimezone().replace(tzinfo=None) if parsed.tzinfo else parsed  # local wall clock, like the app


def record(snapshot: dict) -> bool:
    """Store a workflow's timeline (newer versions only); publish `workflow.step_completed` for steps that just
    completed or were skipped. Returns False when the stored version is already newer."""
    store = get_store()
    current = get(snapshot["id"], store)
    if current is not None and current.version >= snapshot["version"]:
        return False
    meta = snapshot.get("meta") or {}
    before = {s["key"]: s["status"] for s in current.steps} if current else {}
    run = WorkflowRun(
        id=snapshot["id"], workflow_type=snapshot["workflow_type"], status=snapshot["status"],
        current_step=snapshot.get("current_step"), version=snapshot["version"],
        encounter_id=meta.get("encounter_id"), patient_id=meta.get("patient_id"), unit_id=meta.get("unit_id"),
        exception_id=meta.get("exception_id"), run_id=meta.get("run_id"), steps=snapshot["steps"],
        notes=snapshot.get("notes") or [], meta=meta, temporal_run_id=snapshot.get("temporal_run_id"),
        started_at=_dt(snapshot.get("started_at")), updated_at=_dt(snapshot.get("updated_at")),
        finished_at=_dt(snapshot.get("updated_at")) if snapshot["status"] != "running" else None)
    table(store)[run.id] = run
    done = [s for s in run.steps if s["status"] in M.FINISHED and before.get(s["key"]) not in M.FINISHED]
    if done:
        _publish(store, run, done)
    return True


def _publish(store: Store, run: WorkflowRun, steps: list[dict]) -> None:
    refs: dict = {}
    if run.encounter_id:
        refs["encounter"] = f"Encounter/{run.encounter_id}"
    if run.patient_id:
        refs["patient"] = f"Patient/{run.patient_id}"
    if run.unit_id:
        refs["location"] = f"Location/{run.unit_id}"
    now = hospital_now(store)
    bus.publish_many(platform_event(
        "workflow.step_completed", at=now, actor="system:workflows", refs=refs,
        attrs={"workflow_id": run.id, "workflow_type": run.workflow_type, "step": s["key"], "status": s["status"]},
        key=f"step|{run.id}|{s['key']}|{s['status']}|{run.temporal_run_id}") for s in steps)


def mark_attempt(workflow_id: str, step: str, attempt: int, error: str | None, *, max_attempts: int) -> None:
    """An activity attempt failed (error) or a retry started (no error): show it on the step, between two of
    the workflow's own records."""
    store = get_store()
    run = get(workflow_id, store)
    if run is None:
        return
    steps = [dict(s) for s in run.steps]
    s = next((x for x in steps if x["key"] == step), None)
    if s is None or s["status"] in M.FINISHED:
        return
    s["attempts"] = max(s.get("attempts") or 0, attempt)
    if error is not None:
        last = attempt >= max_attempts
        s["status"] = M.FAILED if last else M.RETRYING
        s["detail"] = f"Attempt {attempt} of {max_attempts} failed: {error[:200]}" + ("" if last else "; retrying")
    else:
        s["status"] = M.RETRYING
        s["detail"] = f"Attempt {attempt} of {max_attempts} running (the previous attempt failed)"
    table(store)[run.id] = run.model_copy(update={"steps": steps, "current_step": step})


def view(run: WorkflowRun) -> dict:
    """The run as the workflow view shows it: each step with its definition, duration and the next step."""
    defs = {d.key: d for d in M.STEPS.get(run.workflow_type, ())}
    steps, nxt = [], None
    for s in run.steps:
        d = defs.get(s["key"])
        started, finished = _dt(s.get("started_at")), _dt(s.get("finished_at"))
        end = finished or ((datetime.now() if run.status == "running" else run.updated_at)
                           if s["status"] != M.PENDING else None)
        steps.append({**s, "label": d.label if d else s.get("label"), "owner": d.owner if d else s.get("owner"),
                      "kind": d.kind if d else s.get("kind"), "stub": d.stub if d else s.get("stub"),
                      "duration_s": int((end - started).total_seconds()) if started and end else None})
    current = next((i for i, s in enumerate(steps) if s["status"] not in (*M.FINISHED, M.PENDING)), None)
    if current is not None:
        nxt = next((s["key"] for s in steps[current + 1:] if s["status"] == M.PENDING), None)
    else:
        nxt = next((s["key"] for s in steps if s["status"] == M.PENDING), None) if run.status == "running" else None
    return {"id": run.id, "workflow_type": run.workflow_type, "status": run.status, "current_step": run.current_step,
            "next_step": nxt, "encounter_id": run.encounter_id, "unit_id": run.unit_id,
            "exception_id": run.exception_id, "run_id": run.run_id, "meta": {
                k: v for k, v in run.meta.items() if k not in ("patient_id",)},
            "steps": steps, "notes": run.notes, "started_at": run.started_at, "updated_at": run.updated_at,
            "finished_at": run.finished_at, "version": run.version}


def summary(run: WorkflowRun) -> dict:
    current = next((s for s in run.steps if s["key"] == run.current_step), None)
    return {"id": run.id, "workflow_type": run.workflow_type, "status": run.status, "current_step": run.current_step,
            "current_label": (current or {}).get("label"), "current_status": (current or {}).get("status"),
            "encounter_id": run.encounter_id, "unit_id": run.unit_id, "exception_id": run.exception_id,
            "run_id": run.run_id, "started_at": run.started_at, "updated_at": run.updated_at,
            "done": sum(1 for s in run.steps if s["status"] in M.FINISHED), "total": len(run.steps),
            "attention": any(s["status"] == M.FAILED or (s["status"] == M.WAITING and s.get("escalation"))
                             for s in run.steps)}
