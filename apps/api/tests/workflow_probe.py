"""A probe workflow for 6.5's rule on privileged activities (tests/test_workflows.py).

Until WP8 the journey's follow-up call was booked after a clerk's approval, the one privileged step a workflow ran,
and WP4c's test of the rule went through it. 7.4 places the call without a booking, so the rule (a privileged
activity runs once; a failure becomes an urgent Task for the step's owner and the step waits for an operations
manager to retry or skip it) is tested here, on the same FlowBase machinery, with one step whose activity makes a
real privileged tool call that the Tool Gateway refuses. Its own module, importing only Temporal and the workflow
base: Temporal's sandbox imports it for every workflow task.
"""

from __future__ import annotations

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from app.workflows import model as M
    from app.workflows.base import FlowBase

PROBE = "PrivilegedStepProbe"
STEP = "privilegedCall"
ACTIVITY = "probe.privilegedCall"
STEPS = (M.StepDef(STEP, "A booking approved by a clerk (probe)", "clerk", "privileged"),)


@workflow.defn(name=PROBE)
class PrivilegedStepProbe(FlowBase):
    TYPE = M.JOURNEY  # the workflow view and its retry and skip treat it as a journey
    STEPS = STEPS

    @workflow.signal(name=M.SIGNAL_EVENT)
    def event(self, payload: dict) -> None:
        self._on_event(payload)

    @workflow.signal(name=M.SIGNAL_CONTROL)
    def control(self, payload: dict) -> None:
        self._on_control(payload)

    @workflow.run
    async def run(self, inp: dict) -> dict:  # a plain dict input: the probe needs no timers
        self.started_at = workflow.now()
        self.meta = {"encounter_id": inp["encounter_id"], "patient_id": inp.get("patient_id"), "started_by": "test"}
        await self._begin(STEP)
        out = await self._activity(STEP, ACTIVITY, {"workflow_id": workflow.info().workflow_id,
                                                    "encounter_id": inp["encounter_id"]}, privileged=True)
        if out is not None:
            await self._finish(STEP, detail=out.get("detail"))
        return await self._complete()
