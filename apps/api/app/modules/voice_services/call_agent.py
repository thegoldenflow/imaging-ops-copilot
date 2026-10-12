"""The `call_center` agent's model step (spec 7.4): what does the caller want?

Agent code (the registry entrypoint): it uses only the run. The dialog (callcenter.py) opens one run per caller
turn, does the identity check and every action through the run's tools, and says every sentence; this step only
asks the model for the intent of what the caller just said. The utterance goes in as untrusted text, de-identified
with the run's token map (the verified patient's names, birth date, card and phone are learned first); the prompt
lists what the model must never answer. Returns None when the model is unavailable, so the dialog falls back to
the keyword rules (intents.py).
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

from app.agents.prompts import load as load_prompt
from app.agents.runtime import AgentRun, Context
from app.llm.providers import mock_fixture
from app.modules.voice_services import intents

AGENT_ID = "call_center"


class IntentOutput(BaseModel):
    intent: Literal["reschedule", "cancel", "lookup", "faq", "prep", "clinical", "transfer", "goodbye", "other"]
    confidence: float = Field(ge=0, le=1)
    faq_topic: Literal["visiting_hours", "location", "parking", "general"] | None = None
    language: Literal["en", "zh"] = "en"
    distress: bool = False
    emergency: bool = False


def recognise(run: AgentRun, utterance: str, state: str, context: Context) -> intents.Recognised | None:
    """One structured call; None when the model is unavailable or its answer could not be validated."""
    outcome = run.model(load_prompt(run.spec.prompt_version), {"context": state}, IntentOutput, context=context,
                        untrusted={"utterance": utterance}, second_pass=False)
    if outcome.status != "ok":
        return None
    data = IntentOutput.model_validate(outcome.data)
    return intents.Recognised(data.intent, data.confidence, "model", data.faq_topic, data.language, data.distress,
                              data.emergency)


# ---------- mock model ----------
# The demo and the tests run without an API key. This stand-in recognises intents with the same keyword rules as
# the fallback (so the mock baseline of the evals is optimistic: say so), reading only the de-identified utterance.

_BLOCK = re.compile(r'<untrusted source="utterance">\n(.*?)\n</untrusted>', re.S)


@mock_fixture(AGENT_ID)
def _mock(text: str, images: list, attempt: int) -> dict:
    m = _BLOCK.search(text)
    r = intents.recognise(m.group(1) if m else "")
    return {"intent": r.intent, "confidence": 0.9 if r.intent != "other" else 0.3, "faq_topic": r.faq_topic,
            "language": r.language or "en", "distress": r.distress, "emergency": r.emergency}
