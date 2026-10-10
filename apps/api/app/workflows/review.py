"""LowConfidenceReviewWorkflow (spec 6.5): a person corrects an agent's low-confidence output, and the correction
becomes the agent's next eval case.

Started by `agent.low_confidence`: an agent run finished with a confidence below the agent's
`confidence_threshold` in the registry (the runtime records the de-identified model input and output,
app/agents/reviews.py).

    createReviewTask -> await reviewed (the review Task decided, with or without a correction)
    -> writeCorrectedOutput -> appendToEvalSet (evals/<agent>/cases.jsonl, source human_review)
    -> triggerRegression (the agent's eval over its cases, heartbeating)

A review the person dismisses (rejects) ends without an eval case.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from app.workflows import model as M
    from app.workflows.base import FlowBase

REGRESSION_TIMEOUT = timedelta(minutes=30)
REGRESSION_HEARTBEAT = timedelta(minutes=1)


@workflow.defn(name=M.REVIEW)
class LowConfidenceReviewWorkflow(FlowBase):
    TYPE = M.REVIEW
    STEPS = M.REVIEW_STEPS

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
    async def run(self, inp: M.ReviewInput) -> dict:
        self.timers = inp.timers
        self.started_at = workflow.now()
        self.meta = {"review_id": inp.review_id, "run_id": inp.run_id, "agent_id": inp.agent_id,
                     "started_by": inp.started_by}
        args = {"workflow_id": workflow.info().workflow_id, "review_id": inp.review_id}

        await self._begin("createReviewTask")
        created = await self._activity("createReviewTask", "review.createReviewTask", args)
        if created is None or created.get("missing"):
            self._step("createReviewTask")["detail"] = (created or {}).get("missing") or "Skipped"
            return await self._complete("failed")
        self.meta.update({k: created.get(k) for k in ("encounter_id", "patient_id", "unit_id")})
        await self._finish("createReviewTask", detail=created.get("detail"), refs=created.get("refs"))

        self._step("reviewed")["detail"] = f"Waiting for the {created['role']}"
        got = await self._await_signoff("reviewed", [f"Task/{created['task_id']}"], role=created["role"])
        if got["outcome"] in ("skipped", "reject"):
            why = "Dismissed by the reviewer" if got["outcome"] == "reject" else \
                f"Skipped by {got.get('by') or 'a person'}"
            await self._finish("reviewed", M.SKIPPED, why)
            for key in ("writeCorrectedOutput", "appendToEvalSet", "triggerRegression"):
                self._not_applicable(key, "no review to learn from")
            return await self._complete()
        await self._finish("reviewed", detail=f"Reviewed by the {got.get('by') or 'reviewer'}")

        for key, name in (("writeCorrectedOutput", "review.writeCorrectedOutput"),
                          ("appendToEvalSet", "review.appendToEvalSet")):
            await self._begin(key)
            out = await self._activity(key, name, args)
            if out is not None:
                await self._finish(key, detail=out.get("detail"), refs=out.get("refs"))

        await self._begin("triggerRegression")
        out = await self._activity("triggerRegression", "review.triggerRegression", args,
                                   timeout=REGRESSION_TIMEOUT, heartbeat=REGRESSION_HEARTBEAT)
        if out is not None:
            await self._finish("triggerRegression", detail=out.get("detail"), refs=out.get("refs"))
        return await self._complete()
