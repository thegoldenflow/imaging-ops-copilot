"""Front-desk call agents.

LlmAgent runs a tool-use loop through the LLM gateway (with
de-identification) on Claude or Gemini; either provider takes and returns
Anthropic-style messages and content blocks. It is the registered agent
`voice_agent` (config/agents): each caller turn is an agent run, and every tool
the model asks for goes through the Tool Gateway (allow-list, caller role,
idempotency of reschedule and cancel, audit, trace; spec 6.4). ScriptedAgent is a
deterministic fallback used when no API key is configured or the API is
unavailable, so the phone flow always works; it is plain code, not an agent, and
calls the same server-side tools directly. It answers in the caller's language
(English, French or Chinese; phrases.py), reading times and hours from the store
rather than the tools' English strings, so the tools the model sees are
unchanged. Either way the tools record the call's actions and its outcome. The agent's mock model (mock_agent.py) is for its eval
(evals.py): in mock mode the phone line keeps the scripted agent.
"""

import json
import re
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
from app.modules.frontdesk import phrases as P
from app.modules.frontdesk.intents import TASKS, has, language, ordinal
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
from app.modules.prep import service as prep

GREETING = ("Thank you for calling Lakeshore Imaging. I'm the automated assistant. "
            "To get started, please tell me your full name and date of birth.")


def _start(slot: dict) -> datetime:
    return datetime.fromisoformat(slot["slot_id"].split("|")[1])


def _said_time(start: datetime, text: str) -> bool:
    """The caller names an offered time: "10:30", "10h30", "10 h 30"."""
    return bool(re.search(rf"(?<!\d)0?{start.hour}\s*[h:]\s*{start:%M}(?!\d)", text, re.I))


class ScriptedAgent:
    """The phone line without the model: it understands the caller by keyword in English, French and Chinese
    (intents.py) and answers in the caller's language from a phrase table (phrases.py), with times, opening hours
    and the approved preparation instructions in that language. The tools record what was done and the call's
    outcome."""

    mode = "scripted"

    def respond(self, store: Store, session: CallSession, text: str) -> str:
        st = session.state
        tool = lambda name, **args: run_tool(store, session, name, args)  # noqa: E731
        lang = self._language(session, text)

        def say(key: str, **values) -> str:  # in the call's language as it stands when said
            return P.say(P.SAY[key], lang, **values)

        if has("human", text):
            tool("transfer_to_human", reason="Caller asked for a person")
            return say("transfer_person")
        if has("medical", text):
            tool("transfer_to_human", reason="Medical question")
            return say("transfer_medical")

        if not session.verified_patient_id:
            names = find_named_patients(store, text)
            if has("site", text) and not names:
                return self._say_site(store, session, text, lang)
            dob = parse_dob(text)
            if not names and not dob and has("done", text) and not any(has(k, text) for k in TASKS):
                st["ended"] = True
                return say("goodbye")
            if names:
                st["name"] = store.patients[names[0]].full_name
            if dob:
                st["dob"] = dob.isoformat()
            if "name" not in st and "dob" not in st:
                return say("not_caught")
            if "name" not in st:
                return say("ask_name")
            if "dob" not in st:
                return say("ask_dob")
            result = tool("verify_identity", full_name=st.pop("name"), date_of_birth=st.pop("dob"))
            if not result["verified"]:
                if result["attempts_left"] == 0:
                    tool("transfer_to_human", reason="Could not verify identity")
                    return say("identity_failed")
                return say("identity_retry")
            lang = self._record_language(store, session)
            appts = tool("lookup_appointment")["appointments"]
            st["appointments"] = appts
            if not appts:
                return say("verified_none", first=result["first_name"])
            return say("verified", first=result["first_name"], appointment=self._appt(store, appts[0], lang))

        appts = st.get("appointments") or tool("lookup_appointment")["appointments"]
        current = appts[0] if appts else None
        pending = st.get("pending")

        if pending == "cancel":
            st.pop("pending")
            if has("yes", text) and not has("no", text):
                tool("cancel", appointment_id=current["appointment_id"], reason="Patient request (phone)")
                st["appointments"] = tool("lookup_appointment")["appointments"]
                return say("cancelled")
            return say("kept")

        if pending == "pick_slot":
            slots = session.state.get("offered_slots", [])
            choice = ordinal(text)
            if choice is None:
                choice = next((i for i, s in enumerate(slots) if _said_time(_start(s), text)), None)
            if choice is None or choice >= len(slots):
                return say("pick_again")
            st["pending"], st["chosen_slot"] = "confirm_slot", slots[choice]
            return say("confirm_move", when=P.when(_start(slots[choice]), lang))

        if pending == "confirm_slot":
            st.pop("pending")
            slot = st.pop("chosen_slot")
            if has("yes", text) and not has("no", text):
                result = tool("reschedule", appointment_id=current["appointment_id"], slot_id=slot["slot_id"])
                st["appointments"] = tool("lookup_appointment")["appointments"]
                return say("moved", appointment=self._appt(store, result["new_appointment"], lang))
            return say("not_moved")

        intent = next((k for k in TASKS if has(k, text)), None)
        if current is None and intent not in (None, "site"):
            return say("no_appointment")
        if intent == "cancel":
            st["pending"] = "cancel"
            return say("confirm_cancel", appointment=self._appt(store, current, lang))
        if intent == "reschedule":
            slots = tool("find_open_slots", appointment_id=current["appointment_id"])["slots"]
            if not slots:
                return say("no_slots")
            st["pending"] = "pick_slot"
            items = [say("offer_item", n=i + 1, when=P.when(_start(s), lang)) for i, s in enumerate(slots)]
            return say("offer", options=P.options(items, lang))
        if intent == "prep":
            tool("get_prep_instructions", appointment_id=current["appointment_id"])
            # System 9: the approved text in the caller's language, else the approved English and why.
            exam = store.appointments[current["appointment_id"]].exam_code
            instructions, used, _ = prep.message_text(store, exam, lang)
            reply = say("prep", text=instructions)
            return reply if used == lang else f"{say('prep_in_english')} {reply}"
        if intent == "site":
            return self._say_site(store, session, text, lang)
        if intent == "confirm":
            if not tool("confirm_appointment", appointment_id=current["appointment_id"])["confirmed"]:
                return say("confirm_blocked")
            return say("confirmed")
        if has("done", text) or has("no", text):
            if session.outcome == "in_progress":
                session.outcome = "resolved"
            session.state["ended"] = True
            return say("goodbye")
        return say("menu")

    @staticmethod
    def _language(session: CallSession, text: str) -> str:
        """The language to answer in, kept for the call: the one this turn is in, else the one the caller spoke
        before (the model may have answered the earlier turns), else English."""
        st = session.state
        heard = language(text)
        if heard is None and st.get("lang_from") != "caller":
            heard = next((lang for t in reversed(session.transcript) if t.role == "caller"
                          and (lang := language(t.text))), None)
        if heard:
            st["lang"], st["lang_from"] = heard, "caller"
        return st.get("lang", "en")

    @staticmethod
    def _record_language(store: Store, session: CallSession) -> str:
        """Once the caller is verified: the patient's preferred language if the phone line speaks it, unless the
        caller has spoken one already (what the caller says wins)."""
        st = session.state
        preferred = store.patients[session.verified_patient_id].preferred_language
        if st.get("lang_from") != "caller" and preferred in P.LANGS:
            st["lang"], st["lang_from"] = preferred, "record"
        return st.get("lang", "en")

    @staticmethod
    def _appt(store: Store, a: dict, lang: str) -> str:
        start = store.appointments[a["appointment_id"]].start
        return P.say(P.SAY["appointment"], lang, exam=a["exam"], when=P.when(start, lang), **P.site_names(a["site"]))

    @staticmethod
    def _site_info(store: Store, session: CallSession, text: str) -> dict:
        """The site the caller names, else the one named earlier in the call, else theirs (or Lakeshore)."""
        site = find_named_site(store, text) or session.state.get("site", "")
        session.state["site"] = site
        return run_tool(store, session, "get_site_info", {"site": site})

    def _say_site(self, store: Store, session: CallSession, text: str, lang: str) -> str:
        info = self._site_info(store, session, text)
        site = next(s for s in store.sites.values() if s.name == info["site"])
        return P.say(P.SAY["site"], lang, **P.site_names(site.name), address=site.address,
                     hours=P.opening_hours(site, lang), parking=P.parking(site.parking, lang))


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
