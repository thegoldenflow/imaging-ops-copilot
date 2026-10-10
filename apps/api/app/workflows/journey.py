"""InpatientJourneyWorkflow (spec 6.5, 8.2): one durable workflow per inpatient Encounter.

Started by `patient.admitted` for an inpatient stay (encounter class IMP; the bridge, app/workflows/bridge.py).
Steps, each an activity (app/workflows/activities.py) whose side effects go through the Tool Gateway:

    triageAssist -> orderReview -> medRecAdmission -> bedAssignment -> [predictDuration -> proposeSchedule]
    -> wardMonitoring (NEWS2 signals, until the discharge) -> draftDischargeSummary -> draftPatientInstructions
    -> 48 h timer -> scheduleFollowupCall -> codingStub

A sign-off step waits for a signal (the document signed final, the Task decided) racing the escalation deadlines
(FlowBase._await_signoff). The steps whose modules come later (order review and medication reconciliation: WP6,
discharge documents: WP7, the follow-up call: WP8, NEWS2: WP9's 8.1 stub, coding: the 8.1 roadmap card) are
interfaces with placeholder implementations (app/agents/library/journey_stubs.py), clearly marked as such.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from app.workflows import model as M
    from app.workflows.base import FlowBase, _iso

CODING_PATCH = "journey-coding-step"  # version 2 (see the end of run)


@workflow.defn(name=M.JOURNEY)
class InpatientJourneyWorkflow(FlowBase):
    TYPE = M.JOURNEY
    STEPS = M.JOURNEY_STEPS

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
    async def run(self, inp: M.JourneyInput) -> dict:
        self.timers = inp.timers
        self.started_at = workflow.now()
        self.meta = {"encounter_id": inp.encounter_id, "patient_id": inp.patient_id, "started_by": inp.started_by}
        base = {"workflow_id": workflow.info().workflow_id, "encounter_id": inp.encounter_id}

        # The encounter as the EHR has it now: patient, unit and bed, admission time, the ED visit before it.
        await self._begin("triageAssist")
        context = await self._activity("triageAssist", "journey.context", base)
        if context is None or context.get("missing"):
            self._step("triageAssist")["detail"] = (context or {}).get("missing") or "No context"
            return await self._complete("failed")
        self.meta.update({k: context.get(k) for k in ("patient_id", "unit_id", "bed_id", "admitted_at", "ed_visit")})
        # Cut-offs come from the history, never from "now" inside an activity: a retried activity then reads the
        # same orders and sends the Tool Gateway the same arguments (the same idempotency key).
        args = {**base, "admitted_at": context.get("admitted_at"), "ed_visit": context.get("ed_visit"),
                "patient_id": context.get("patient_id"), "as_of": context.get("as_of")}

        await self._auto("triageAssist", "journey.triageAssist", args)
        review = await self._signoff("orderReview", "journey.orderReview", args)
        # MedRec reconciles against the orders as of the order review's sign-off (hospital time of the decision)
        await self._signoff("medRecAdmission", "journey.medRecAdmission",
                            {**args, "orders_as_of": (review or {}).get("decided_at") or args["as_of"]})
        await self._auto("bedAssignment", "journey.bedAssignment", args)

        await self._begin("predictDuration")
        prediction = await self._activity("predictDuration", "journey.predictDuration", args)
        if prediction is not None and prediction.get("not_applicable"):
            self._not_applicable("predictDuration", prediction["not_applicable"])
            self._not_applicable("proposeSchedule", "no operating-room case")
            await self._publish()
        elif prediction is not None:
            await self._finish("predictDuration", detail=prediction.get("detail"), refs=prediction.get("refs"))
            await self._signoff("proposeSchedule", "journey.proposeSchedule", {**args, "case": prediction.get("case")})
        else:
            self._not_applicable("proposeSchedule", "the prediction was skipped")

        await self._ward(args)
        await self._signoff("draftDischargeSummary", "journey.draftDischargeSummary", args)
        await self._signoff("draftPatientInstructions", "journey.draftPatientInstructions", args)

        delay = timedelta(seconds=self.timers.followup_delay_s)
        await self._begin("followupDelay", M.WAITING, f"Until {_iso(workflow.now() + delay)} (wall clock)")
        await workflow.sleep(delay)
        await self._finish("followupDelay")

        await self._followup(args)
        # Version 2 added the coding step (8.1). `patched` marks new executions; a journey started on version 1
        # replays its history without the marker and finishes as it began (tests/fixtures/workflows/journey_v1.json).
        if workflow.patched(CODING_PATCH):
            await self._auto("codingStub", "journey.codingStub", args)
        else:
            self._not_applicable("codingStub", "started before the coding step existed (version 1)")
        return await self._complete()

    # ---------- step kinds ----------

    async def _auto(self, key: str, activity: str, args: dict) -> dict | None:
        await self._begin(key)
        out = await self._activity(key, activity, args)
        if out is None:
            return None
        if out.get("not_applicable"):
            self._not_applicable(key, out["not_applicable"])
            await self._publish()
            return out
        await self._finish(key, detail=out.get("detail"), refs=out.get("refs"))
        return out

    async def _signoff(self, key: str, activity: str, args: dict) -> dict | None:
        """The activity prepares the work (a draft, a sign-off Task) and names what to wait for; then a person."""
        await self._begin(key)
        out = await self._activity(key, activity, args)
        if out is None:
            return None
        if out.get("not_applicable"):
            self._not_applicable(key, out["not_applicable"])
            await self._publish()
            return out
        s = self._step(key)
        s["detail"], s["refs"] = out.get("detail"), list(out.get("refs") or [])
        waits = list(out.get("wait_for") or [])
        if not waits:
            await self._finish(key)
            return out
        got = await self._await_signoff(key, waits, role=out.get("signer") or self._def(key).owner)
        if got["outcome"] == "skipped":
            await self._finish(key, M.SKIPPED, f"Skipped by {got.get('by') or 'a person'} while waiting")
        else:
            verb = {"signed": "Signed", "approve": "Approved", "accept": "Confirmed", "reject": "Rejected"}.get(
                got["outcome"], str(got["outcome"]).capitalize())
            await self._finish(key, detail=f"{verb} by the {got.get('by') or 'signer'}")
        return {**out, "outcome": got["outcome"], "decided_at": (got.get("event") or {}).get("at")}

    async def _ward(self, args: dict) -> None:
        """Until the discharge: every NEWS2 flag on the encounter becomes a nurse Task (WP9 plants the scores)."""
        key = "wardMonitoring"
        await self._begin(key, M.WAITING, "Until the discharge; NEWS2 flags go to the nurse")
        while True:
            await workflow.wait_condition(lambda: self._control_for(key, ("skip",)) is not None or any(
                e.get("kind") in ("discharged", "news2") for e in self.inbox))
            if (control := self._control_for(key, ("skip",))) is not None:
                self.controls.remove(control)
                await self._finish(key, M.SKIPPED, f"Skipped by {control.get('by') or 'a person'}")
                return
            for event in [e for e in self.inbox if e.get("kind") == "news2"]:
                self.inbox.remove(event)
                out = await self._activity(key, "journey.news2Alert", {**args, "flag": event.get("ref")})
                self._note(f"NEWS2 flag {event.get('ref')}: " + ((out or {}).get("detail") or "no task"))
                self._step(key)["refs"].append(event.get("ref"))
                self._step(key)["status"] = M.WAITING
                await self._publish()
            discharge = next((e for e in self.inbox if e.get("kind") == "discharged"), None)
            if discharge is not None:
                self.inbox.remove(discharge)
                self.meta["discharged_at"] = discharge.get("at")
                await self._finish(key, detail=f"Discharged at {discharge.get('at') or 'an unknown time'} (hospital "
                                               f"time); {len(self._step(key)['refs'])} NEWS2 flag(s)")
                return

    async def _followup(self, args: dict) -> None:
        """WP8 replaces the call itself. Here: consent check, then the follow-up call booked after a clerk's
        approval (bookAppointment is privileged: no automatic retry; a failure becomes a Task)."""
        key = "scheduleFollowupCall"
        await self._begin(key)
        req = await self._activity(key, "journey.requestFollowupCall",
                                   {**args, "discharged_at": self.meta.get("discharged_at")})
        if req is None:
            return
        s = self._step(key)
        s["detail"], s["refs"] = req.get("detail"), list(req.get("refs") or [])
        if req.get("mode") != "approval":
            await self._finish(key)
            return
        got = await self._await_signoff(key, [f"Task/{req['approval_task_id']}"], role="clerk")
        if got["outcome"] == "skipped":
            await self._finish(key, M.SKIPPED, f"Skipped by {got.get('by') or 'a person'} while waiting")
            return
        if got["outcome"] == "reject":
            await self._finish(key, detail="The clerk rejected the booking; nothing was booked")
            return
        await self._begin(key, M.RUNNING, "Approved by the clerk; booking")
        booked = await self._activity(key, "journey.bookFollowupCall", {**args, **{
            k: req.get(k) for k in ("approval_task_id", "appointment_id", "run_id")}}, privileged=True)
        if booked is not None:
            await self._finish(key, detail=booked.get("detail"), refs=booked.get("refs"))
