"""The mock model of the phone agent (`voice_agent`): its tool loop runs without an API key.

The phone eval (app/modules/frontdesk/evals.py) drives the registered agent's real path with it: each caller turn
de-identified, the model's tool calls re-identified and run through the Tool Gateway, one agent run (trace) per
turn. Like a model it sees only the de-identified conversation ([PERSON_1], [DATE_1]) and answers with one tool call
or a short reply; keyword rules in English, French and Chinese decide. Written alongside the eval's scripts, so it is
an optimistic stand-in that shows the pipeline works, not a model's quality. The phone line itself still answers
with the scripted fallback in mock mode (app/modules/frontdesk/agent.py).
"""

from __future__ import annotations

import json
import re

from app.llm.providers import TextBlock, ToolUseBlock, mock_tool_fixture

WORDS = {
    "human": r"\b(human|person|someone|agent|operator|representative|staff)\b|quelqu'un|une personne|人工|真人",
    "medical": r"\b(pain|hurt|symptom|bleed|dizzy|result|diagnos|cancer|tumou?r|safe|dangerous|medication|medicine|"
               r"pregnan|allerg|worried)\w*|douleur|résultat|dangereu|médicament|enceinte|allergi|inquiet|"
               r"疼|痛|结果|诊断|癌|肿瘤|安全|危险|药|怀孕|过敏|担心",
    "cancel": r"\bcancel\w*|\bannul\w*|取消",
    "reschedule": r"\b(reschedul\w*|move|change|another (day|time)|different (day|time))\b|déplacer|changer|reporter|"
                  r"改约|改期|改时间|换个时间",
    "confirm": r"\bconfirm\b|\bconfirmer\b|确认",
    "prep": r"\b(prep\w*|instruction\w*|eat|fast\w*|drink|bring|wear)\b|prépar\w*|consigne|须知|准备",
    "site": r"\b(where|address|direction\w*|hours|open|close|parking|located)\b|adresse|horaire|heures|stationnement|"
            r"地址|营业|停车|在哪",
    "yes": r"\b(yes|yeah|yep|correct|sure|please do|go ahead|ok(ay)?)\b|\boui\b|d'accord|是|好|对|可以",
    "no": r"\b(no|nope|don't|do not|never mind|keep)\b|\bnon\b|不|别",
    "done": r"\b(that's all|that is all|nothing else|bye|goodbye|thank you|thanks)\b|c'est tout|au revoir|merci|"
            r"再见|谢谢|没有了",
}
RULES = {k: re.compile(v, re.I) for k, v in WORDS.items()}
ORDINAL_WORDS = [(re.compile(r"\bfirst\b|premi[eè]re?|第一|第1"), 0), (re.compile(r"\bsecond\b|deuxi[eè]me|第二|第2"), 1),
                 (re.compile(r"\bthird\b|troisi[eè]me|第三|第3"), 2)]
ORDINAL_NUMBERS = [(re.compile(r"\b(1|one|un)\b"), 0), (re.compile(r"\b(2|two|deux)\b"), 1),
                   (re.compile(r"\b(3|three|trois)\b"), 2)]
PERSON = re.compile(r"\[PERSON_\d+\]")
DATE_TOKEN = re.compile(r"\[DATE_\d+\]")
FR_MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
             "novembre", "décembre"]
CJK_DATE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日?")
FR_DATE = re.compile(rf"\b(\d{{1,2}})(?:er)?\s+({'|'.join(FR_MONTHS)})\s+(\d{{4}})\b", re.I)
SITE_NAME = re.compile(r"(?<![A-Za-z])([A-Z][a-z]{3,})(?![a-z])")


def _has(rule: str, text: str) -> bool:
    return bool(RULES[rule].search(text))


def _date(text: str) -> str | None:
    """A date the caller said: a placeholder as given, or (like a model would) a date in another language as ISO."""
    if m := DATE_TOKEN.search(text):
        return m.group()
    if m := CJK_DATE.search(text):
        y, mo, d = map(int, m.groups())
        return f"{y:04d}-{mo:02d}-{d:02d}"
    if m := FR_DATE.search(text):
        return f"{int(m.group(3)):04d}-{FR_MONTHS.index(m.group(2).lower()) + 1:02d}-{int(m.group(1)):02d}"
    return None


def _ordinal(text: str) -> int | None:
    for rules in (ORDINAL_WORDS, ORDINAL_NUMBERS):  # "the second one" is option 2
        for rx, index in rules:
            if rx.search(text.lower()):
                return index
    return None


class _Call:
    """What the conversation so far says: the caller's turns, the tool calls and their results."""

    def __init__(self, messages: list) -> None:
        self.events: list[tuple[int, str, dict, dict]] = []  # (message index, tool name, input, output)
        self.callers: list[tuple[int, str]] = []
        self.agent_texts: list[str] = []
        self.tool_uses = 0
        pending: dict[str, tuple[str, dict]] = {}
        for i, m in enumerate(messages):
            content = m["content"]
            if m["role"] == "assistant":
                for block in content:
                    if block.type == "tool_use":
                        pending[block.id] = (block.name, dict(block.input))
                        self.tool_uses += 1
                    elif block.type == "text" and block.text:
                        self.agent_texts.append(block.text)
            elif isinstance(content, str):
                self.callers.append((i, content))
            else:
                for result in content:
                    name, args = pending.pop(result["tool_use_id"], ("?", {}))
                    try:
                        output = json.loads(result["content"])
                    except (TypeError, ValueError):
                        output = {}
                    self.events.append((i, name, args, output if isinstance(output, dict) else {}))
        self.new_turn = isinstance(messages[-1]["content"], str)

    def last(self, name: str) -> dict | None:
        return next((out for _, n, _, out in reversed(self.events) if n == name), None)

    @property
    def verified(self) -> bool:
        return any(n == "verify_identity" and out.get("verified") for _, n, _, out in self.events)

    def said_since_last_check(self) -> str:
        since = max((i for i, n, _, _ in self.events if n == "verify_identity"), default=-1)
        return " ".join(text for i, text in self.callers if i > since)

    @property
    def appointment(self) -> dict | None:
        appts = (self.last("lookup_appointment") or {}).get("appointments") or []
        return appts[0] if appts else None


def _tool(call: _Call, name: str, **args) -> tuple[list, str]:
    return [ToolUseBlock(id=f"mock_tool_{call.tool_uses + 1}", name=name, input=args)], "tool_use"


def _say(text: str) -> tuple[list, str]:
    return [TextBlock(text)], "end_turn"


def _when(a: dict) -> str:
    return f"your {a['exam']} on {a['when']} at {a['site']}"


def _after_tool(call: _Call) -> tuple[list, str]:
    _, name, args, out = call.events[-1]
    if "error" in out:
        return _say("Sorry, I couldn't do that. Would you like me to transfer you to a team member?")
    if name == "verify_identity":
        if out.get("verified"):
            return _tool(call, "lookup_appointment")
        if out.get("attempts_left") == 0:
            return _tool(call, "transfer_to_human", reason="Could not verify the caller's identity")
        return _say("I couldn't match those details. Please repeat your full name and date of birth.")
    if name == "lookup_appointment":
        first = (call.last("verify_identity") or {}).get("first_name", "")
        appts = out.get("appointments") or []
        if not appts:
            return _say(f"Thank you, {first}. I don't see any upcoming appointments. Can I help with anything else?")
        return _say(f"Thank you, {first}, you're verified. I see {_when(appts[0])}. Would you like to reschedule, "
                    "cancel, confirm it, hear the preparation instructions, or get directions?")
    if name == "find_open_slots":
        slots = out.get("slots") or []
        if not slots:
            return _tool(call, "transfer_to_human", reason="No open times to offer")
        options = ", ".join(f"option {i + 1}, {s['when']}" for i, s in enumerate(slots))
        return _say(f"I have {options}. Which would you like?")
    if name == "reschedule":
        return _say(f"You're all set for {_when(out['new_appointment'])}. You'll get a confirmation text. "
                    "Anything else?")
    if name == "cancel":
        return _say("Done. Your appointment is cancelled and you'll receive a text confirmation. Anything else?")
    if name == "confirm_appointment":
        if out.get("confirmed"):
            return _say("Your appointment is confirmed. Anything else?")
        return _say("I can't confirm this appointment yet because a safety screening still needs staff review. "
                    "Our team will call you. Anything else?")
    if name == "get_prep_instructions":
        return _say(f"{out.get('instructions', '')} I've also sent these instructions to you by text.")
    if name == "get_site_info":
        return _say(f"{out['site']} is at {out['address']}, open {out['hours']}. {out['parking']}")
    if name == "transfer_to_human":
        if "medical" in args.get("reason", "").lower():
            return _say("I'm not able to answer medical questions, but our staff can help. "
                        "I'm transferring you to a team member now.")
        return _say("I'm transferring you to a member of our front desk team now.")
    return _say("Is there anything else I can help with?")


def _caller_turn(call: _Call, text: str) -> tuple[list, str]:
    if _has("human", text):
        return _tool(call, "transfer_to_human", reason="Caller asked for a person")
    if _has("medical", text):
        return _tool(call, "transfer_to_human", reason="Medical question")
    if not call.verified:
        if _has("site", text) and not PERSON.search(text):
            names = SITE_NAME.findall(text)
            return _tool(call, "get_site_info", site=names[0] if names else "")
        said = call.said_since_last_check()
        name, dob = PERSON.search(said), _date(said)
        if name and dob:
            return _tool(call, "verify_identity", full_name=name.group(), date_of_birth=dob)
        if _has("done", text) and not (name or dob):
            return _say("Thank you for calling. Goodbye!")
        if name:
            return _say("Thank you. And your date of birth?")
        if dob:
            return _say("Thank you. And your full name, please?")
        return _say("I can help with that. First, please tell me your full name and date of birth.")

    current = call.appointment
    asked = call.agent_texts[-1] if call.agent_texts else ""
    if asked.startswith("Just to confirm, you'd like to cancel"):
        if _has("yes", text) and not _has("no", text):
            return _tool(call, "cancel", appointment_id=current["appointment_id"], reason="Patient request (phone)")
        return _say("Okay, I've left your appointment as it is. Anything else?")
    if asked.endswith("Which would you like?"):
        slots = (call.last("find_open_slots") or {}).get("slots") or []
        choice = _ordinal(text)
        if choice is None or choice >= len(slots):
            return _say("Sorry, which option would you like: the first, second, or third?")
        return _say(f"Just to confirm: move your appointment to {slots[choice]['when']}?")
    if asked.startswith("Just to confirm: move your appointment to"):
        slots = (call.last("find_open_slots") or {}).get("slots") or []
        slot = next((s for s in slots if s["when"] in asked), None)
        if slot and _has("yes", text) and not _has("no", text):
            return _tool(call, "reschedule", appointment_id=current["appointment_id"], slot_id=slot["slot_id"])
        return _say("No problem, I haven't changed anything. Anything else?")

    intent = next((k for k in ("cancel", "reschedule", "confirm", "prep") if _has(k, text)), None)
    if intent and current is None:
        return _say("I don't see an upcoming appointment for you. Would you like me to transfer you to a team member?")
    if intent == "cancel":
        return _say(f"Just to confirm, you'd like to cancel {_when(current)}?")
    if intent == "reschedule":
        return _tool(call, "find_open_slots", appointment_id=current["appointment_id"])
    if intent == "confirm":
        return _tool(call, "confirm_appointment", appointment_id=current["appointment_id"])
    if intent == "prep":
        return _tool(call, "get_prep_instructions", appointment_id=current["appointment_id"])
    if _has("site", text):
        names = SITE_NAME.findall(text)
        return _tool(call, "get_site_info", site=names[0] if names else "")
    if _has("done", text) or _has("no", text):
        return _say("Thank you for calling Lakeshore Imaging. Goodbye!")
    return _say("I can help you reschedule, cancel or confirm an appointment, give preparation instructions or "
                "directions, or transfer you to a person.")


@mock_tool_fixture("voice_agent")
def mock_voice_agent(messages: list, tools: list) -> tuple[list, str]:
    call = _Call(messages)
    if not call.new_turn:
        return _after_tool(call)
    return _caller_turn(call, call.callers[-1][1])
