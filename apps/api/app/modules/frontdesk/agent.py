"""Front-desk call agents.

LlmAgent runs a tool-use loop through the LLM gateway (with
de-identification) on Claude or Gemini; either provider takes and returns
Anthropic-style messages and content blocks. It is the registered agent
`voice_agent` (config/agents): each caller turn is an agent run, and every tool
the model asks for goes through the Tool Gateway (allow-list, caller role,
idempotency of reschedule and cancel, audit, trace; spec 6.4). ScriptedAgent is a
deterministic fallback used when no API key is configured or the API is
unavailable, so the phone flow always works; it is plain code, not an agent, and
calls the same server-side tools directly. Either way the tools record the call's
actions and its outcome. The agent's mock model (mock_agent.py) is for its eval
(evals.py): in mock mode the phone line keeps the scripted agent.
"""

import json
import time
from datetime import datetime

from app.agents.registry import AgentNotDeployable
from app.agents.runtime import runtime
from app.core.store import Store
from app.llm.deid import Pseudonymizer
from app.llm.gateway import get_gateway
from app.llm.prompts import VOICE_AGENT
from app.llm.providers import ProviderRefusal, ProviderUnavailable
from app.modules.frontdesk import mock_agent  # noqa: F401  (registers the agent's mock model)
from app.modules.frontdesk.intents import TASKS, has, ordinal
from app.modules.frontdesk.tools import (
    TOOL_DEFINITIONS,
    TOOL_IDS,
    CallSession,
    Turn,
    find_named_patients,
    find_named_site,
    parse_dob,
    run_tool,
)

GREETING = ("Thank you for calling Lakeshore Imaging. I'm the automated assistant. "
            "To get started, please tell me your full name and date of birth.")
GOODBYE = "Thank you for calling Lakeshore Imaging. Goodbye!"


def _say_appt(a: dict) -> str:
    return f"your {a['exam']} on {a['when']} at {a['site']}"


def _say_site(info: dict) -> str:
    return f"{info['site']} is at {info['address']}, open {info['hours']}. {info['parking']}"


class ScriptedAgent:
    """The phone line without the model: it understands the caller by keyword in English, French and Chinese
    (intents.py) and answers in English. The tools record what was done and the call's outcome."""

    mode = "scripted"

    def respond(self, store: Store, session: CallSession, text: str) -> str:
        st = session.state
        tool = lambda name, **args: run_tool(store, session, name, args)  # noqa: E731

        if has("human", text):
            tool("transfer_to_human", reason="Caller asked for a person")
            return "Of course. I'm transferring you to a member of our front desk team now."
        if has("medical", text):
            tool("transfer_to_human", reason="Medical question")
            return ("I'm not able to answer medical questions, but our staff can help. "
                    "I'm transferring you to a team member now.")

        if not session.verified_patient_id:
            names = find_named_patients(store, text)
            if has("site", text) and not names:
                return _say_site(self._site_info(store, session, text))
            dob = parse_dob(text)
            if not names and not dob and has("done", text) and not any(has(k, text) for k in TASKS):
                st["ended"] = True
                return GOODBYE
            if names:
                st["name"] = store.patients[names[0]].full_name
            if dob:
                st["dob"] = dob.isoformat()
            if "name" not in st and "dob" not in st:
                return "Sorry, I didn't catch that. Please tell me your full name and date of birth."
            if "name" not in st:
                return "Thank you. And your full name, please?"
            if "dob" not in st:
                return "Thank you. And your date of birth?"
            result = tool("verify_identity", full_name=st.pop("name"), date_of_birth=st.pop("dob"))
            if not result["verified"]:
                if result["attempts_left"] == 0:
                    tool("transfer_to_human", reason="Could not verify identity")
                    return "I couldn't verify those details. Let me transfer you to a team member."
                return "I couldn't match those details. Please repeat your full name and date of birth."
            appts = tool("lookup_appointment")["appointments"]
            st["appointments"] = appts
            if not appts:
                return f"Thank you, {result['first_name']}. I don't see any upcoming appointments. Can I help with anything else?"
            return (f"Thank you, {result['first_name']}, you're verified. I see {_say_appt(appts[0])}. "
                    "Would you like to reschedule, cancel or confirm it, hear the preparation instructions, or get "
                    "directions?")

        appts = st.get("appointments") or tool("lookup_appointment")["appointments"]
        current = appts[0] if appts else None
        pending = st.get("pending")

        if pending == "cancel":
            st.pop("pending")
            if has("yes", text) and not has("no", text):
                tool("cancel", appointment_id=current["appointment_id"], reason="Patient request (phone)")
                st["appointments"] = tool("lookup_appointment")["appointments"]
                return ("Done. Your appointment is cancelled and you'll receive a text confirmation. "
                        "Is there anything else I can help with?")
            return "Okay, I've left your appointment as it is. Anything else?"

        if pending == "pick_slot":
            slots = session.state.get("offered_slots", [])
            choice = ordinal(text)
            if choice is None:
                choice = next((i for i, s in enumerate(slots) if s["when"].split(" at ")[1] in text), None)
            if choice is None or choice >= len(slots):
                return "Sorry, which option would you like: the first, second, or third?"
            st["pending"], st["chosen_slot"] = "confirm_slot", slots[choice]
            return f"Just to confirm: move your appointment to {slots[choice]['when']}?"

        if pending == "confirm_slot":
            st.pop("pending")
            slot = st.pop("chosen_slot")
            if has("yes", text) and not has("no", text):
                result = tool("reschedule", appointment_id=current["appointment_id"], slot_id=slot["slot_id"])
                st["appointments"] = tool("lookup_appointment")["appointments"]
                return (f"You're all set for {_say_appt(result['new_appointment'])}. "
                        "You'll get a confirmation text. Anything else?")
            return "No problem, I haven't changed anything. Anything else?"

        intent = next((k for k in TASKS if has(k, text)), None)
        if current is None and intent not in (None, "site"):
            return "I don't see an upcoming appointment for you. Would you like me to transfer you to a team member?"
        if intent == "cancel":
            st["pending"] = "cancel"
            return f"Just to confirm, you'd like to cancel {_say_appt(current)}?"
        if intent == "reschedule":
            slots = tool("find_open_slots", appointment_id=current["appointment_id"])["slots"]
            if not slots:
                return "I couldn't find open times in the next week. Let me transfer you to a team member."
            st["pending"] = "pick_slot"
            options = ", ".join(f"option {i + 1}, {s['when']}" for i, s in enumerate(slots))
            return f"I have {options}. Which would you like?"
        if intent == "prep":
            info = tool("get_prep_instructions", appointment_id=current["appointment_id"])
            return f"{info['instructions']} I've also sent these instructions to you by text."
        if intent == "site":
            return _say_site(self._site_info(store, session, text))
        if intent == "confirm":
            if not tool("confirm_appointment", appointment_id=current["appointment_id"])["confirmed"]:
                return ("I can't confirm this appointment yet because a safety screening still needs staff review. "
                        "Our team will call you. Anything else?")
            return "Your appointment is confirmed. Anything else?"
        if has("done", text) or has("no", text):
            if session.outcome == "in_progress":
                session.outcome = "resolved"
            session.state["ended"] = True
            return GOODBYE
        return ("I can help you reschedule, cancel or confirm an appointment, give preparation instructions or "
                "directions, or transfer you to a person.")

    @staticmethod
    def _site_info(store: Store, session: CallSession, text: str) -> dict:
        """The site the caller names, else the one named earlier in the call, else theirs (or Lakeshore)."""
        site = find_named_site(store, text) or session.state.get("site", "")
        session.state["site"] = site
        return run_tool(store, session, "get_site_info", {"site": site})


class LlmAgent:
    MAX_STEPS = 6

    def __init__(self) -> None:
        self._history: dict[str, list] = {}
        self._pseudo: dict[str, Pseudonymizer] = {}

    def respond(self, store: Store, session: CallSession, text: str) -> str:
        gateway = get_gateway()
        pseudo = self._pseudo.setdefault(session.id, Pseudonymizer(list(store.patients.values())))
        history = self._history.setdefault(session.id, [])
        redacted = pseudo.redact(text, spoken_dates=True)  # a birth date in any form the caller says it
        history.append({"role": "user", "content": redacted})
        with runtime.start("voice_agent", context_id=f"CallSession/{session.id}",
                           attachments={"store": store, "session": session}) as run:
            for _ in range(self.MAX_STEPS):
                result = gateway.tool_turn(task="voice_agent", prompt=VOICE_AGENT, messages=history,
                                           tools=TOOL_DEFINITIONS, redacted_text=redacted)
                history.append({"role": "assistant", "content": result.content})
                tool_uses = [b for b in result.content if b.type == "tool_use"]
                if result.stop_reason != "tool_use" or not tool_uses:
                    return pseudo.restore(" ".join(b.text for b in result.content if b.type == "text").strip())
                results = []
                for block in tool_uses:
                    done = run.tool(TOOL_IDS.get(block.name, block.name), pseudo.restore(dict(block.input)))
                    output = done.for_model()
                    session.transcript.append(Turn(role="tool", text=f"{block.name} -> {json.dumps(output)[:300]}"))
                    results.append({"type": "tool_result", "tool_use_id": block.id,
                                    "content": pseudo.redact(json.dumps(output, default=str))})
                history.append({"role": "user", "content": results})
            return "Let me transfer you to a team member who can help."


_scripted = ScriptedAgent()
_llm = LlmAgent()

# Gateway mode -> agent mode for providers with a tool-use agent.
LLM_AGENT_MODES = {"anthropic": "claude", "gemini": "gemini"}


def agent_reply(store: Store, session: CallSession, text: str) -> tuple[str, str]:
    """Returns (reply, mode used). Falls back to the scripted agent if the LLM is unavailable."""
    started = time.monotonic()
    mode = session.agent_mode
    if mode != "scripted":
        try:
            reply = _llm.respond(store, session, text)
        except (ProviderUnavailable, ProviderRefusal, AgentNotDeployable):  # not deployable: prod mode, not evaluated
            mode = "scripted"
            reply = _scripted.respond(store, session, text)
    else:
        reply = _scripted.respond(store, session, text)
    session.latencies_ms.append(int((time.monotonic() - started) * 1000))
    return reply, mode


def new_session(store: Store) -> CallSession:
    mode = LLM_AGENT_MODES.get(get_gateway().mode, "scripted")
    session = CallSession(id=store.next_id("CALL"), started_at=datetime.now(), agent_mode=mode)
    session.transcript.append(Turn(role="agent", text=GREETING))
    return session
