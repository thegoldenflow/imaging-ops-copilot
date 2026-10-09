"""The Control Tower's narrator: a runtime agent (spec 6.4, 7.1) that explains an exception and recommends actions.

For one exception the rule engine found (exceptions.py), acting as itself:

1. reads context through its read tools (the unit's bed board, up to three of
   the encounters the exception names), de-identified by the runtime;
2. asks the model for the spec's output: `{exception_id, severity, narrative,
   recommended_actions[{action, rationale, owner_role, expected_effect}],
   evidence_refs[]}` (plus each action's `action_id` from the menu);
3. checks it (the guards below); on a problem the model gets one more try with
   the problems listed, then the engine's own template is used;
4. puts the result into the review queue of the bed manager and the charge
   nurses through `explainException` (a proposal Task); a person approves,
   rejects or defers it. Nothing is executed here.

Guards (the warehouse control tower's pattern): actions only from the engine's
menu, with the menu's owner role; every number in the text must be one the
engine produced (facts or menu); no claim that anything was done; evidence only
from the engine's references; the engine's severity. The service then checks
that every evidence reference resolves in FHIR before anything is shown.

Agent code: it uses only the run (no FhirGateway, HTTP client or database).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from app.agents.prompts import load as load_prompt
from app.agents.runtime import AgentRun
from app.llm.providers import mock_fixture

AGENT_ID = "control_tower"
MAX_CONTEXT_ENCOUNTERS = 3
_REF = re.compile(r"\b[A-Z][A-Za-z]+/[A-Za-z0-9][A-Za-z0-9._-]*")
_IDS = re.compile(r"\b(?:[A-Z]{2,5}-\d{2}(?:-[A-Z])?|appt-\d+|stay-x?\d+|ed-x?\d+|EXC-\d+|task-\d+|surg-x?\d+)\b")
_NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?")
_DONE = [re.compile(p, re.I) for p in (
    r"\b(i|we)\s+(have\s+|'ve\s+)?(moved|assigned|transferred|booked|cancel+ed|notified|called|paged|created|opened|"
    r"discharged|executed|scheduled|sent|activated|escalated|re-?sequenced)\b",
    r"\b(has|have|had)\s+(now\s+|already\s+)?been\s+(moved|assigned|transferred|booked|cancel+ed|notified|called|"
    r"paged|created|opened|discharged|executed|scheduled|sent|activated|escalated|re-?sequenced|completed|done)\b",
    r"\b(was|were)\s+(moved|assigned|transferred|booked|cancel+ed|notified|discharged|executed|activated)\b",
    r"\balready\s+(moved|assigned|transferred|booked|notified|discharged|done|activated)\b",
    r"已(经)?(执行|安排|转移|通知|取消|预约|出院|分配|完成|启动)")]


class RecommendedAction(BaseModel):
    action_id: str
    action: str
    rationale: str
    owner_role: Literal["physician", "nurse", "operations_manager"]
    expected_effect: str


class NarratorOutput(BaseModel):
    exception_id: str
    severity: Literal["low", "med", "high"]
    narrative: str = Field(max_length=1200)
    recommended_actions: list[RecommendedAction] = Field(min_length=1, max_length=3)
    evidence_refs: list[str] = Field(min_length=1, max_length=20)


@dataclass
class Narration:
    run_id: str
    output: dict  # NarratorOutput as a dict (actions worded by the engine's menu)
    source: Literal["model", "template"]
    ai_status: str  # ok, needs_human, unavailable (the model call), or guard (rejected by the guards)
    attempts: int
    problems: list[str] = field(default_factory=list)  # what the guards found (all attempts)
    review_task_id: str | None = None
    evaluated: bool = True
    context_refs: list[str] = field(default_factory=list)


# ---------- guards ----------


def _norm(token: str) -> str:
    value = float(token)
    return str(int(value)) if value.is_integer() else repr(value)


def allowed_numbers(*sources) -> set[str]:
    text = " ".join(json.dumps(s, ensure_ascii=False, default=str) for s in sources)
    return {_norm(n) for n in _NUMBER.findall(_IDS.sub(" ", _REF.sub(" ", text)))}


def numbers_in(text: str) -> set[str]:
    return {_norm(n) for n in _NUMBER.findall(_IDS.sub(" ", _REF.sub(" ", text)))}


def check(output: NarratorOutput, exception: dict) -> list[str]:
    """What is wrong with a narrator output for this exception (empty: it may be shown after the evidence check)."""
    problems = []
    menu = {m["action_id"]: m for m in exception["menu"]}
    if output.exception_id != exception["id"]:
        problems.append(f"exception_id must be {exception['id']}")
    if output.severity != exception["severity"]:
        problems.append(f"severity must be the engine's: {exception['severity']}")
    seen = set()
    for a in output.recommended_actions:
        if a.action_id not in menu:
            problems.append(f"action {a.action_id!r} is not on the menu")
        elif a.owner_role != menu[a.action_id]["owner_role"]:
            problems.append(f"action {a.action_id} belongs to {menu[a.action_id]['owner_role']}, not {a.owner_role}")
        if a.action_id in seen:
            problems.append(f"action {a.action_id} recommended twice")
        seen.add(a.action_id)
    allowed = allowed_numbers(exception["facts"], exception["menu"])
    texts = [output.narrative] + [x for a in output.recommended_actions for x in (a.rationale, a.expected_effect)]
    for text in texts:
        extra = sorted(numbers_in(text) - allowed)
        if extra:
            problems.append(f"numbers the engine did not produce: {', '.join(extra)} (in: {text[:80]!r})")
        if any(p.search(text) for p in _DONE):
            problems.append(f"claims an action was taken (in: {text[:80]!r}); everything is a recommendation")
    unknown = [r for r in output.evidence_refs if r not in exception["evidence_refs"]]
    if unknown:
        problems.append(f"evidence not offered by the engine: {', '.join(unknown[:5])}")
    return problems


def finalise(output: NarratorOutput, exception: dict) -> dict:
    """The output as stored: each action worded by the engine's menu (the source of truth for what is done)."""
    menu = {m["action_id"]: m for m in exception["menu"]}
    data = output.model_dump()
    for a in data["recommended_actions"]:
        a["action"] = menu[a["action_id"]]["label"]
        a["owner_role"] = menu[a["action_id"]]["owner_role"]
        a["encounter_id"] = menu[a["action_id"]].get("encounter_id")
    data["evidence_refs"] = list(dict.fromkeys(data["evidence_refs"]))
    return data


def template(exception: dict) -> dict:
    """The engine's own narrative and the first menu actions (the model is unavailable or kept failing the guards)."""
    actions = [{"action_id": m["action_id"], "action": m["label"], "rationale": m["why"],
                "owner_role": m["owner_role"], "expected_effect": m["effect"], "encounter_id": m.get("encounter_id")}
               for m in exception["menu"][:3]]
    return {"exception_id": exception["id"], "severity": exception["severity"], "narrative": exception["summary"],
            "recommended_actions": actions, "evidence_refs": exception["evidence_refs"][:8]}


# ---------- the run ----------


def _context_calls(exception: dict) -> list[tuple[str, dict]]:
    calls: list[tuple[str, dict]] = []
    if exception["unit_id"] not in ("OR",):
        calls.append(("getBedBoard", {"unit_id": exception["unit_id"]}))
    encounters = [r.split("/", 1)[1] for r in exception["evidence_refs"] if r.startswith("Encounter/")]
    calls += [("getEncounterContext", {"encounter_id": e}) for e in encounters[:MAX_CONTEXT_ENCOUNTERS]]
    return calls


def narrate(run: AgentRun, exception: dict) -> Narration:
    """`exception`: the stored FlowException as a dict (facts, menu, evidence_refs, summary, ...)."""
    ctx = run.gather(*_context_calls(exception))
    prompt = load_prompt(run.spec.prompt_version)
    menu = [{k: m[k] for k in ("action_id", "label", "owner_role", "why", "effect")} for m in exception["menu"]]
    variables = {"facts": json.dumps({"exception_id": exception["id"], "rule": exception["rule"],
                                      "severity": exception["severity"], "title": exception["title"],
                                      **exception["facts"]}, ensure_ascii=False, default=str),
                 "menu": json.dumps(menu, ensure_ascii=False), "evidence": json.dumps(exception["evidence_refs"]),
                 "context": ctx.json(), "feedback": ""}
    problems: list[str] = []
    status, attempts = "unavailable", 0
    for attempt in range(2):
        attempts += 1
        outcome = run.model(prompt, variables, NarratorOutput, context=ctx)
        status = outcome.status
        if outcome.status != "ok":
            break
        output = NarratorOutput.model_validate(outcome.data)
        found = check(output, exception)
        if not found:
            data = finalise(output, exception)
            return _record(run, exception, Narration(run.run_id, data, "model", "ok", attempts, problems,
                                                     evaluated=run.spec.evaluated, context_refs=ctx.refs))
        problems += [f"attempt {attempt + 1}: {p}" for p in found]
        status = "guard"
        variables["feedback"] = ("\nYour previous answer was rejected for these reasons; fix them:\n- "
                                 + "\n- ".join(found))
    return _record(run, exception, Narration(run.run_id, template(exception), "template", status, attempts, problems,
                                             evaluated=run.spec.evaluated, context_refs=ctx.refs))


def _record(run: AgentRun, exception: dict, narration: Narration) -> Narration:
    out = narration.output
    review = run.tool("explainException", {
        "exception_id": exception["id"], "unit_id": exception["unit_id"], "severity": out["severity"],
        "title": exception["title"][:200], "narrative": out["narrative"][:2000],
        "recommended_actions": [{k: a[k] for k in ("action_id", "action", "rationale", "owner_role",
                                                    "expected_effect")} for a in out["recommended_actions"]],
        "evidence_refs": out["evidence_refs"], "source": narration.source})
    narration.review_task_id = (review.output or {}).get("task_id")
    run.finish(outcome="completed" if narration.source == "model" else "needs_human",
               confidence=1.0 if narration.source == "model" else 0.0)
    return narration


# ---------- mock model ----------
# The demo runs without an API key. This stand-in writes the narrative from the facts in the prompt, chooses the
# first menu actions and cites the first evidence references, so it passes the same guards a real model must.

def _section(text: str, start: str, end: str) -> str:
    return text.split(start, 1)[-1].split(end, 1)[0].strip()


def _mock_narrative(f: dict) -> str:
    rule = f.get("rule")
    if rule == "unit_occupancy":
        gap = f["net_gap"]
        lead = (f"{f['unit_name']} is at {f['occupancy_pct']}% with {f['free_beds']} free beds: "
                f"{f['expected_admissions_24h']} admissions and {f['expected_discharges_24h']} discharges are expected "
                f"in the next {f['window_hours']} hours.")
        if gap > 0:
            return lead + f" That leaves the unit short of {gap} beds unless patients leave earlier."
        return lead + " Expected discharges cover the demand, but there is no slack for a surge."
    if rule == "ed_boarding":
        return (f"{f['boarders_over_2h']} admitted patients have waited in the ED for a bed for more than "
                f"{f['threshold_minutes']} minutes; the longest has waited {f['longest_minutes']} minutes. "
                "Their target units need beds freed or overflow opened.")
    if rule == "or_overrun":
        return (f"{f['procedure']} in {f['room']} is predicted to take {f['predicted_minutes']} minutes against "
                f"{f['booked_minutes']} booked, {f['overrun_minutes']} minutes over. "
                f"The room's list is predicted to end at {f['room_predicted_end']}.")
    if rule == "preop_gap":
        return (f"{f['procedure']} in {f['room']} is booked for {f['booked_start']}, {f['minutes_to_start']} minutes "
                f"from now, with {f['open_count']} pre-op items still open. The case may start late unless they are "
                "completed.")
    return f.get("title") or "Exception found by the rule engine."


@mock_fixture(AGENT_ID)
def _mock(text: str, images: list, attempt: int) -> dict:
    facts = json.loads(_section(text, "the only numbers you may use):\n", "\n\nAction menu"))
    menu = json.loads(_section(text, "(choose by action_id):\n", "\n\nEvidence you may cite"))
    evidence = json.loads(_section(text, "Evidence you may cite:\n", "\n\nContext"))
    actions = [{"action_id": m["action_id"], "action": m["label"], "rationale": m["why"],
                "owner_role": m["owner_role"], "expected_effect": m["effect"]} for m in menu[:3]]
    return {"exception_id": facts["exception_id"], "severity": facts["severity"], "narrative": _mock_narrative(facts),
            "recommended_actions": actions, "evidence_refs": evidence[:6]}
