"""What the three workflows have in common (spec 6.5): the timeline, signals, steps, sign-off waits and escalation.

Workflow code runs in Temporal's sandbox and must be deterministic: time only from `workflow.now()`, no I/O
(activities do it), no randomness. The patterns ported from freight-arbiter (docs/audit-baseline.md §5):

- Signal handlers only record: a forwarded domain event goes into the inbox, a retry or skip into the controls
  (a repeated event id is ignored). The workflow thread takes them from there.
- A wait for a person races absolute deadlines: after `signoff_timeout_s` a high-priority Task for the signer,
  after another `escalation_timeout_s` a Task telling the operations manager; the wait goes on after escalating.
  The durations come from the start input.
- A step's activity is retried 3 times (1 s / 4 s / 16 s); an activity that calls a privileged tool is not
  retried. When the retries are used up the step fails, a Task asks a person, and the workflow waits for the
  operations manager (or admin) to retry or skip it. Nothing is polled. When the step's subject no longer exists
  (the encounter or exception is gone, e.g. after a demo reset) the workflow ends as failed instead.
- The timeline (`progress`) is recorded through an activity at every change, for the workflow view (a regular
  activity, not a local one: a local activity's thread is interrupted when the worker evicts the workflow, which
  could cut a database statement in half; recording is idempotent, a newer version wins).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from app.workflows import model as M

DEFAULT_RETRY = RetryPolicy(initial_interval=timedelta(seconds=M.RETRY_INITIAL_S), backoff_coefficient=M.RETRY_BACKOFF,
                            maximum_interval=timedelta(seconds=M.RETRY_MAX_INTERVAL_S),
                            maximum_attempts=M.RETRY_MAX_ATTEMPTS)
PRIVILEGED_RETRY = RetryPolicy(maximum_attempts=M.PRIVILEGED_MAX_ATTEMPTS)
PROGRESS_RETRY = RetryPolicy(initial_interval=timedelta(seconds=1), maximum_interval=timedelta(seconds=10))
ACTIVITY_TIMEOUT = timedelta(minutes=2)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value else None


def _cause(err: ActivityError) -> str:
    cause = err.cause
    while cause is not None and getattr(cause, "cause", None) is not None and not str(cause):
        cause = cause.cause
    return (str(cause) if cause is not None else str(err))[:300]


def _fmt(seconds: int) -> str:
    return f"{seconds // 3600} h" if seconds >= 3600 and seconds % 3600 == 0 else \
        f"{seconds // 60} min" if seconds >= 60 and seconds % 60 == 0 else f"{seconds} s"


class FlowBase:
    TYPE = ""
    STEPS: tuple[M.StepDef, ...] = ()

    def __init__(self) -> None:
        self.steps: list[dict] = M.timeline(self.STEPS)
        self.meta: dict = {}  # encounter_id, patient_id, unit_id, exception_id, run_id, ...
        self.status = "running"
        self.version = 0
        self.inbox: list[dict] = []  # forwarded domain events not taken yet
        self.controls: list[dict] = []  # retry / skip requests not taken yet
        self.seen: set[str] = set()
        self.notes: list[dict] = []
        self.timers = M.Timers()
        self.started_at: datetime | None = None
        self.updated_at: datetime | None = None

    # ---------- signals (handlers only record) ----------

    def _on_event(self, payload: dict) -> None:
        event_id = payload.get("event_id")
        if not event_id or event_id in self.seen:
            return
        self.seen.add(event_id)
        self.inbox.append(payload)

    def _on_control(self, payload: dict) -> None:
        event_id = payload.get("event_id")
        if not event_id or event_id in self.seen:
            return
        self.seen.add(event_id)
        self.controls.append(payload)

    # ---------- the timeline ----------

    def _snapshot(self) -> dict:
        info = workflow.info()
        current = next((s["key"] for s in self.steps if s["status"] not in (*M.FINISHED, M.PENDING)), None)
        return {"id": info.workflow_id, "workflow_type": self.TYPE, "status": self.status, "version": self.version,
                "current_step": current, "steps": self.steps, "notes": self.notes, "meta": self.meta,
                "started_at": _iso(self.started_at), "updated_at": _iso(self.updated_at),
                "temporal_run_id": info.run_id}

    def progress(self) -> dict:  # the `progress` query of each workflow
        return self._snapshot()

    async def _publish(self) -> None:
        self.version += 1
        self.updated_at = workflow.now()
        await workflow.execute_activity("workflows.recordProgress", self._snapshot(),
                                        start_to_close_timeout=timedelta(seconds=30), retry_policy=PROGRESS_RETRY)

    def _step(self, key: str) -> dict:
        return next(s for s in self.steps if s["key"] == key)

    def _note(self, text: str) -> None:
        self.notes.append({"at": _iso(workflow.now()), "text": text})

    async def _begin(self, key: str, status: str = M.RUNNING, detail: str | None = None) -> dict:
        s = self._step(key)
        s["status"] = status
        s["started_at"] = s["started_at"] or _iso(workflow.now())
        if detail is not None:
            s["detail"] = detail
        await self._publish()
        return s

    async def _finish(self, key: str, status: str = M.DONE, detail: str | None = None,
                      refs: list[str] | None = None) -> None:
        await self._close_tasks(key, status)
        s = self._step(key)
        s["status"] = status
        s["started_at"] = s["started_at"] or _iso(workflow.now())
        s["finished_at"] = _iso(workflow.now())
        s["waiting_for"] = None
        if detail is not None:
            s["detail"] = detail
        if refs:
            s["refs"] = list(dict.fromkeys([*s["refs"], *refs]))
        await self._publish()

    def _not_applicable(self, key: str, why: str) -> None:
        """A step that does not apply to this case (no publish of its own: the next change records it)."""
        s = self._step(key)
        s["status"], s["detail"] = M.SKIPPED, f"Not applicable: {why}"

    async def _complete(self, status: str = "completed") -> dict:
        self.status = status
        await self._publish()
        return {"status": status, "steps": {s["key"]: s["status"] for s in self.steps}}

    # ---------- activities ----------

    async def _activity(self, key: str, name: str, args: dict, *, privileged: bool = False,
                        timeout: timedelta = ACTIVITY_TIMEOUT, heartbeat: timedelta | None = None) -> dict | None:
        """Run a step's activity. When its retries are used up (or a privileged call failed once) the step fails,
        a Task asks a person and the workflow waits for a retry or a skip. Returns None when the step was skipped."""
        while True:
            try:
                result = await workflow.execute_activity(
                    name, args, start_to_close_timeout=timeout, heartbeat_timeout=heartbeat,
                    retry_policy=PRIVILEGED_RETRY if privileged else DEFAULT_RETRY)
                s = self._step(key)
                s["attempts"] = max(s["attempts"], int(result.pop("_attempt", 1)))
                return result
            except ActivityError as err:
                s = self._step(key)
                if isinstance(err.cause, ApplicationError) and err.cause.type == "SubjectGone":
                    # the encounter, exception or review is gone (e.g. a demo reset): nothing left to do or ask for
                    s["status"], s["detail"] = M.FAILED, f"The record is gone: {_cause(err)}"
                    self.status = "failed"
                    await self._publish()
                    raise ApplicationError(f"{key}: the workflow's subject is gone", non_retryable=True) from err
                s["status"] = M.FAILED
                s["attempts"] = M.PRIVILEGED_MAX_ATTEMPTS if privileged else M.RETRY_MAX_ATTEMPTS
                s["detail"] = f"Failed after {s['attempts']} attempt(s): {_cause(err)}"
                await self._publish()
                role = self._def(key).owner if privileged and self._def(key).owner != "system" else "operations_manager"
                await self._task(key, "failed", f"Workflow step '{self._def(key).label}' failed"
                                 + (" (a privileged call is never retried automatically)" if privileged else
                                    f" after {M.RETRY_MAX_ATTEMPTS} attempts")
                                 + f": {_cause(err)[:200]}. Retry or skip it in the workflow view.", role,
                                 priority="urgent")
                await self._publish()
                control = await self._await_control(key, ("retry", "skip"))
                if control["action"] == "skip":
                    await self._finish(key, M.SKIPPED, f"Skipped by {control.get('by', 'a person')} after the failure")
                    return None
                s["status"], s["detail"] = M.RUNNING, f"Retried by {control.get('by', 'a person')}"
                await self._publish()

    def _def(self, key: str) -> M.StepDef:
        return next(d for d in self.STEPS if d.key == key)

    def _target(self) -> dict:
        """Where a workflow Task points: the encounter, else the unit."""
        if self.meta.get("encounter_id"):
            return {"encounter_id": self.meta["encounter_id"]}
        return {"unit_id": self.meta["unit_id"]} if self.meta.get("unit_id") else {}

    async def _task(self, key: str, kind: str, description: str, role: str, *, priority: str = "routine",
                    focus: str | None = None, extra: dict | None = None) -> str | None:
        """Open a Task for a person (createWorkflowTask through the Tool Gateway); its id joins the step."""
        args = {"workflow_id": workflow.info().workflow_id, "step": key, "kind": kind, "description": description,
                "performer_role": role, "priority": priority, **self._target(), **(extra or {})}
        if focus:
            args["focus"] = focus
        try:
            out = await workflow.execute_activity("workflows.openTask", args, start_to_close_timeout=ACTIVITY_TIMEOUT,
                                                  retry_policy=DEFAULT_RETRY)
        except ActivityError as err:
            self._note(f"Could not open a {kind} task for {key}: {_cause(err)}")
            return None
        task_id = out.get("task_id")
        if task_id:
            self._step(key)["tasks"].append({"kind": kind, "task_id": task_id, "role": role,
                                             "at": _iso(workflow.now())})
        return task_id

    async def _close_tasks(self, key: str, status: str) -> None:
        """The step is over: its escalation, notice and failure Tasks are done (they would clutter worklists)."""
        s = self._step(key)
        open_tasks = [t for t in s["tasks"] if t["kind"] in ("escalation", "notify", "failed") and not t.get("closed")]
        if not open_tasks:
            return
        try:
            await workflow.execute_activity("workflows.closeTasks", {
                "workflow_id": workflow.info().workflow_id, "step": key, **self._target(),
                "task_ids": [t["task_id"] for t in open_tasks],
                "note": f"Resolved: '{self._def(key).label}' is {status}"}, start_to_close_timeout=ACTIVITY_TIMEOUT,
                retry_policy=DEFAULT_RETRY)
        except ActivityError as err:
            self._note(f"Could not close the Tasks of {key}: {_cause(err)}")
            return
        for t in open_tasks:
            t["closed"] = True

    # ---------- waiting for people ----------

    def _control_for(self, key: str, actions: tuple[str, ...]) -> dict | None:
        return next((c for c in self.controls if c.get("step") == key and c.get("action") in actions), None)

    async def _await_control(self, key: str, actions: tuple[str, ...]) -> dict:
        await workflow.wait_condition(lambda: self._control_for(key, actions) is not None)
        control = self._control_for(key, actions)
        self.controls.remove(control)
        self._note(f"{control['action'].capitalize()} of '{self._def(key).label}' by {control.get('by', 'a person')}")
        return control

    def _ready(self, key: str, refs: list[str]) -> bool:
        return self._control_for(key, ("skip",)) is not None or any(e.get("ref") in refs for e in self.inbox)

    def _take(self, key: str, refs: list[str]) -> dict | None:
        """The outcome of a sign-off, when the inbox or the controls hold one (consumed)."""
        control = self._control_for(key, ("skip",))
        if control is not None:
            self.controls.remove(control)
            self._note(f"Skip of '{self._def(key).label}' by {control.get('by', 'a person')}")
            return {"outcome": "skipped", "by": control.get("by")}
        for event in list(self.inbox):
            if event.get("ref") not in refs:
                continue
            self.inbox.remove(event)
            kind = event.get("kind")
            if kind == "signed" and not event.get("final"):
                self._step(key)["detail"] = f"Signed by the {event.get('role')}; waiting for the co-signature"
                continue
            if kind == "decided" and event.get("decision") == "deferred":
                self._note(f"'{self._def(key).label}' deferred by the {event.get('role') or 'decider'}")
                continue
            return {"outcome": {"signed": "signed", "cleared": "cleared"}.get(kind, event.get("decision")),
                    "by": event.get("role"), "event": event}
        return None

    async def _await_signoff(self, key: str, refs: list[str], *, role: str, focus: str | None = None) -> dict:
        """Wait for a person: a document signed final, a Task or exception decided (or a skip). Races two absolute
        deadlines and keeps waiting after escalating."""
        s = self._step(key)
        s["status"], s["waiting_for"] = M.WAITING, {"refs": refs, "role": role}
        s["started_at"] = s["started_at"] or _iso(workflow.now())
        await self._publish()
        start = workflow.now()
        t = self.timers
        deadlines = [start + timedelta(seconds=t.signoff_timeout_s),
                     start + timedelta(seconds=t.signoff_timeout_s + t.escalation_timeout_s)]
        while True:
            got = self._take(key, refs)
            if got is not None:
                return got
            level = s["escalation"]
            remaining = (deadlines[level] - workflow.now()).total_seconds() if level < len(deadlines) else None
            if remaining is not None and remaining <= 0:
                await self._escalate(key, level + 1, role, focus or (refs[0] if refs else None))
                continue
            try:
                await workflow.wait_condition(lambda: self._ready(key, refs), timeout=remaining)
            except asyncio.TimeoutError:
                await self._escalate(key, level + 1, role, focus or (refs[0] if refs else None))

    async def _escalate(self, key: str, level: int, role: str, focus: str | None) -> None:
        s = self._step(key)
        s["escalation"] = level
        label, t = self._def(key).label, self.timers
        if level == 1:
            await self._task(key, "escalation", f"Sign-off overdue: '{label}' has waited {_fmt(t.signoff_timeout_s)} "
                                                f"for the {role.replace('_', ' ')}.", role, priority="urgent",
                             focus=focus)
            self._note(f"'{label}' escalated to the {role.replace('_', ' ')} (high priority)")
        else:
            waited = t.signoff_timeout_s + t.escalation_timeout_s
            await self._task(key, "notify", f"Still not signed off after {_fmt(waited)}: '{label}' "
                                            f"(owner: {role.replace('_', ' ')}).", "operations_manager",
                             priority="stat", focus=focus)
            self._note(f"Operations manager told: '{label}' still waiting")
        await self._publish()
