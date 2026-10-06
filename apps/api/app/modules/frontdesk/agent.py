"""Front-desk call agents.

ClaudeAgent runs a tool-use loop through the LLM gateway (with
de-identification). ScriptedAgent is a deterministic fallback used when no API
key is configured or the API is unavailable, so the phone flow always works.
Both call the same server-side tools.
"""

import json
import re
import time
from datetime import datetime

from app.core.store import Store
from app.llm.deid import Pseudonymizer
from app.llm.gateway import get_gateway
from app.llm.prompts import VOICE_AGENT
from app.llm.providers import ProviderRefusal, ProviderUnavailable
from app.modules.frontdesk.tools import (
    TOOL_DEFINITIONS,
    CallSession,
    Turn,
    find_named_patients,
    parse_dob,
    run_tool,
)

GREETING = ("Thank you for calling Lakeshore Imaging. I'm the automated assistant. "
            "To get started, please tell me your full name and date of birth.")

MEDICAL = re.compile(r"\b(pain|hurt|symptom|bleed|dizzy|result|diagnos|cancer|tumou?r|safe|dangerous|"
                     r"medication|medicine|pregnan|allerg|is it normal|worried)\w*", re.I)
HUMAN = re.compile(r"\b(human|person|someone|agent|operator|representative|staff)\b|人工", re.I)
CANCEL = re.compile(r"\bcancel\w*|取消", re.I)
RESCHEDULE = re.compile(r"\b(reschedul\w*|move|change|another (day|time)|different (day|time))\b|改约|改期", re.I)
PREP = re.compile(r"\b(prep\w*|instruction\w*|eat|fast\w*|drink|bring|wear)\b|须知|准备", re.I)
SITE = re.compile(r"\b(where|address|direction\w*|hours|open|close|parking|located)\b|地址|营业", re.I)
CONFIRM_APPT = re.compile(r"\bconfirm\b", re.I)
YES = re.compile(r"\b(yes|yeah|yep|correct|sure|please do|go ahead|ok(ay)?)\b|是|好|对", re.I)
NO = re.compile(r"\b(no|nope|don't|do not|never mind)\b|不", re.I)
DONE = re.compile(r"\b(that's all|that is all|nothing else|bye|goodbye|thank you|thanks)\b|再见|谢谢", re.I)
ORDINALS = {"first": 0, "1": 0, "one": 0, "second": 1, "2": 1, "two": 1, "third": 2, "3": 2, "three": 2}


def _say_appt(a: dict) -> str:
    return f"your {a['exam']} on {a['when']} at {a['site']}"


class ScriptedAgent:
    mode = "scripted"

    def respond(self, store: Store, session: CallSession, text: str) -> str:
        st = session.state
        tool = lambda name, **args: run_tool(store, session, name, args)  # noqa: E731

        if HUMAN.search(text):
            tool("transfer_to_human", reason="Caller asked for a person")
            return "Of course. I'm transferring you to a member of our front desk team now."
        if MEDICAL.search(text):
            tool("transfer_to_human", reason="Medical question")
            return ("I'm not able to answer medical questions, but our staff can help. "
                    "I'm transferring you to a team member now.")

        if not session.verified_patient_id:
            if SITE.search(text) and not find_named_patients(store, text):
                info = tool("get_site_info", site="")
                return f"{info['site']} is at {info['address']}, open {info['hours']}. {info['parking']}"
            names = find_named_patients(store, text)
            dob = parse_dob(text)
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
                    "Would you like to reschedule, cancel, hear the preparation instructions, or get directions?")

        appts = st.get("appointments") or tool("lookup_appointment")["appointments"]
        current = appts[0] if appts else None
        pending = st.get("pending")

        if pending == "cancel":
            st.pop("pending")
            if YES.search(text) and not NO.search(text):
                tool("cancel", appointment_id=current["appointment_id"], reason="Patient request (phone)")
                session.outcome = "resolved"
                st["appointments"] = tool("lookup_appointment")["appointments"]
                return ("Done. Your appointment is cancelled and you'll receive a text confirmation. "
                        "Is there anything else I can help with?")
            return "Okay, I've left your appointment as it is. Anything else?"

        if pending == "pick_slot":
            slots = session.state.get("offered_slots", [])
            choice = next((ORDINALS[w] for w in re.findall(r"[a-z0-9]+", text.lower()) if w in ORDINALS), None)
            if choice is None:
                choice = next((i for i, s in enumerate(slots) if s["when"].split(" at ")[1] in text), None)
            if choice is None or choice >= len(slots):
                return "Sorry, which option would you like: the first, second, or third?"
            st["pending"], st["chosen_slot"] = "confirm_slot", slots[choice]
            return f"Just to confirm: move your appointment to {slots[choice]['when']}?"

        if pending == "confirm_slot":
            st.pop("pending")
            slot = st.pop("chosen_slot")
            if YES.search(text) and not NO.search(text):
                result = tool("reschedule", appointment_id=current["appointment_id"], slot_id=slot["slot_id"])
                session.outcome = "resolved"
                st["appointments"] = tool("lookup_appointment")["appointments"]
                return (f"You're all set for {_say_appt(result['new_appointment'])}. "
                        "You'll get a confirmation text. Anything else?")
            return "No problem, I haven't changed anything. Anything else?"

        if current is None and (CANCEL.search(text) or RESCHEDULE.search(text) or PREP.search(text)):
            return "I don't see an upcoming appointment for you. Would you like me to transfer you to a team member?"
        if CANCEL.search(text):
            st["pending"] = "cancel"
            return f"Just to confirm, you'd like to cancel {_say_appt(current)}?"
        if RESCHEDULE.search(text):
            slots = tool("find_open_slots", appointment_id=current["appointment_id"])["slots"]
            if not slots:
                return "I couldn't find open times in the next week. Let me transfer you to a team member."
            st["pending"] = "pick_slot"
            options = ", ".join(f"option {i + 1}, {s['when']}" for i, s in enumerate(slots))
            return f"I have {options}. Which would you like?"
        if PREP.search(text):
            info = tool("get_prep_instructions", appointment_id=current["appointment_id"])
            session.outcome = "resolved"
            return f"{info['instructions']} I've also sent these instructions to you by text."
        if SITE.search(text):
            info = tool("get_site_info", site="")
            return f"{info['site']} is at {info['address']}, open {info['hours']}. {info['parking']}"
        if CONFIRM_APPT.search(text):
            appt = store.appointments[current["appointment_id"]]
            appt.reminder_confirmed = True
            session.actions.append({"tool": "confirm", "appointment": appt.id})
            session.outcome = "resolved"
            return "Your appointment is confirmed. Anything else?"
        if DONE.search(text) or NO.search(text):
            if session.outcome == "in_progress":
                session.outcome = "resolved"
            session.state["ended"] = True
            return "Thank you for calling Lakeshore Imaging. Goodbye!"
        return ("I can help you reschedule, cancel, hear preparation instructions, or get directions. "
                "I can also transfer you to a person.")


class ClaudeAgent:
    mode = "claude"
    MAX_STEPS = 6

    def __init__(self) -> None:
        self._history: dict[str, list] = {}
        self._pseudo: dict[str, Pseudonymizer] = {}

    def respond(self, store: Store, session: CallSession, text: str) -> str:
        gateway = get_gateway()
        pseudo = self._pseudo.setdefault(session.id, Pseudonymizer(list(store.patients.values())))
        history = self._history.setdefault(session.id, [])
        redacted = pseudo.redact(text)
        history.append({"role": "user", "content": redacted})
        for _ in range(self.MAX_STEPS):
            result = gateway.tool_turn(task="voice_agent", prompt=VOICE_AGENT, messages=history,
                                       tools=TOOL_DEFINITIONS, redacted_text=redacted)
            history.append({"role": "assistant", "content": result.content})
            tool_uses = [b for b in result.content if b.type == "tool_use"]
            if result.stop_reason != "tool_use" or not tool_uses:
                return pseudo.restore(" ".join(b.text for b in result.content if b.type == "text").strip())
            results = []
            for block in tool_uses:
                output = run_tool(store, session, block.name, pseudo.restore(dict(block.input)))
                session.transcript.append(Turn(role="tool", text=f"{block.name} -> {json.dumps(output)[:300]}"))
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": pseudo.redact(json.dumps(output, default=str))})
            history.append({"role": "user", "content": results})
        return "Let me transfer you to a team member who can help."


_scripted = ScriptedAgent()
_claude = ClaudeAgent()


def agent_reply(store: Store, session: CallSession, text: str) -> tuple[str, str]:
    """Returns (reply, mode used). Falls back to the scripted agent if Claude is unavailable."""
    started = time.monotonic()
    mode = session.agent_mode
    if mode == "claude":
        try:
            reply = _claude.respond(store, session, text)
        except (ProviderUnavailable, ProviderRefusal):
            mode = "scripted"
            reply = _scripted.respond(store, session, text)
    else:
        reply = _scripted.respond(store, session, text)
    session.latencies_ms.append(int((time.monotonic() - started) * 1000))
    return reply, mode


def new_session(store: Store) -> CallSession:
    mode = "claude" if get_gateway().mode == "anthropic" else "scripted"
    session = CallSession(id=store.next_id("CALL"), started_at=datetime.now(), agent_mode=mode)
    session.transcript.append(Turn(role="agent", text=GREETING))
    return session
