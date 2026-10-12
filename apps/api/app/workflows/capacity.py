"""CapacityExceptionWorkflow (spec 6.5, 7.1): one durable workflow per Control Tower exception.

Started by `exception.opened` (the rule engine found a condition; app/modules/control_tower/exceptions.py):

    detect -> explainAndRecommend (the narrator agent, recommend-level tool) -> await approved / rejected
    -> execute (createFlowTask, action level, idempotency keys) -> 1 h timer -> verify (occupancy re-checked)
    -> recordOutcome

A rejected recommendation, and a condition that clears before anyone decided, also end in recordOutcome, so
6.6 can count how often recommendations are taken and whether they worked. A deferral keeps the wait going (the
exception comes back as a reminder). The decision itself is made in the Control Tower's action drawer; while
this workflow runs, the approved actions are executed here (with Temporal's retries) rather than in the request.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from app.workflows import model as M
    from app.workflows.base import FlowBase, _iso


@workflow.defn(name=M.CAPACITY)
class CapacityExceptionWorkflow(FlowBase):
    TYPE = M.CAPACITY
    STEPS = M.CAPACITY_STEPS

    @workflow.signal(name=M.SIGNAL_EVENT)
    def event(self, payload: dict) -> None:
        self._on_event(payload)

    @workflow.signal(name=M.SIGNAL_CONTROL)
    def control(self, payload: dict) -> None:
        self._on_control(payload)

    @workflow.query(name="progress")
    def query_progress(self) -> dict:
        return self.progress()

    @workflow.run
    async def run(self, inp: M.CapacityInput) -> dict:
        self.timers = inp.timers
        self.started_at = workflow.now()
        self.meta = {"exception_id": inp.exception_id, "started_by": inp.started_by}
        args = {"workflow_id": workflow.info().workflow_id, "exception_id": inp.exception_id}

        await self._begin("detect")
        found = await self._activity("detect", "capacity.detect", args)
        if found is None or found.get("missing"):
            self._step("detect")["detail"] = (found or {}).get("missing") or "Skipped"
            return await self._complete("failed")
        self.meta.update({k: found.get(k) for k in ("unit_id", "rule", "severity", "subject_ref")})
        await self._finish("detect", detail=found.get("detail"), refs=found.get("refs"))

        await self._begin("explainAndRecommend")
        explained = await self._activity("explainAndRecommend", "capacity.explainAndRecommend", args)
        if explained is not None:
            await self._finish("explainAndRecommend", detail=explained.get("detail"), refs=explained.get("refs"))

        ref = f"FlowException/{inp.exception_id}"
        self._step("decision")["detail"] = "Waiting for the bed manager or the unit's charge nurse"
        got = await self._await_signoff("decision", [ref], role="operations_manager")
        outcome = got["outcome"]
        if outcome == "skipped":
            await self._finish("decision", M.SKIPPED, f"Skipped by {got.get('by') or 'a person'}")
            for key in ("execute", "verifyDelay", "verify"):
                self._not_applicable(key, "the decision was skipped")
            await self._record(args, "skipped")
            return await self._complete()
        if outcome == "cleared":
            await self._finish("decision", M.SKIPPED, "The condition cleared before anyone decided")
            for key in ("execute", "verifyDelay", "verify"):
                self._not_applicable(key, "the condition cleared")
            await self._record(args, "cleared")
            return await self._complete()
        if outcome != "approved":
            await self._finish("decision", detail=f"Rejected by the {got.get('by') or 'decider'}")
            for key in ("execute", "verifyDelay", "verify"):
                self._not_applicable(key, "the recommendation was rejected")
            await self._record(args, "rejected")
            return await self._complete()
        await self._finish("decision", detail=f"Approved by the {got.get('by') or 'decider'}")

        await self._begin("execute")
        executed = await self._activity("execute", "capacity.execute", args)
        if executed is not None:
            await self._finish("execute", detail=executed.get("detail"), refs=executed.get("refs"))

        delay = timedelta(seconds=self.timers.verify_after_s)
        await self._begin("verifyDelay", M.WAITING, f"Until {_iso(workflow.now() + delay)} (wall clock)")
        await workflow.sleep(delay)
        await self._finish("verifyDelay")

        await self._begin("verify")
        verified = await self._activity("verify", "capacity.verify", args)
        if verified is not None:
            await self._finish("verify", detail=verified.get("detail"), refs=verified.get("refs"))
        await self._record(args, "approved", verified)
        return await self._complete()

    async def _record(self, args: dict, decision: str, verified: dict | None = None) -> None:
        await self._begin("recordOutcome")
        out = await self._activity("recordOutcome", "capacity.recordOutcome", {
            **args, "decision": decision, "verified": verified or {}})
        if out is not None:
            await self._finish("recordOutcome", detail=out.get("detail"))
