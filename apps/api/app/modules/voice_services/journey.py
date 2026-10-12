"""The journey's scheduleFollowupCall step (spec 6.5 -> 7.4), called by the activity `journey.scheduleFollowupCall`.

After the journey's 48-hour timer, WP8's scheduling: the four conditions (discharge time, followup_call consent,
a phone number, the signed instructions); with all of them the call is scheduled (the call record through the
`followup_calls` agent's writeCallRecord) and placed by the call service; without one, the workflow's own manual
follow-up Task for a nurse (createWorkflowTask, so the timeline shows it). Idempotent: the follow-up row is keyed by
the encounter and every write goes through the Tool Gateway with deterministic arguments, so a retried activity
finds the first result. The step does not wait for the call: the call's outcome goes to the nurse follow-up queue.
"""

from __future__ import annotations

from typing import Callable

from app.core.store import Store
from app.fhir.dt import parse
from app.modules.voice_services import followup


def schedule_step(store: Store, args: dict, *, manual_task: Callable[[str], str | None]) -> dict:
    call = followup.schedule(store, args["encounter_id"], source="journey", workflow_id=args.get("workflow_id"),
                             manual_task=manual_task, discharged_at=parse(args.get("discharged_at")))
    if call.status == "manual":
        return {"mode": "manual", "call_id": call.id, "refs": [f"Task/{call.task_id}"] if call.task_id else [],
                "detail": f"No automated call ({call.reason}): a nurse follows up by hand"}
    questions = max(0, len(call.script) - 1)
    return {"mode": "scheduled", "call_id": call.id,
            "refs": [f"Communication/{call.communication_id}"] if call.communication_id else [],
            "detail": f"Follow-up call {call.id} scheduled ({questions} questions from the signed instructions, "
                      f"{call.language}); its outcome goes to the nurse follow-up queue"}
