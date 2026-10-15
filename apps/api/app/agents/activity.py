"""What the AI did in this demo: every AI action since the last reset, read from the agent traces.

An action is one item's AI (the runs that share a cause's `action_id`: a requisition's three calls), or one run
when nothing grouped it (a voice turn, a discharge summary draft). For each: what the AI did, for which item, who
caused it and how (app/agents/cause.py), when, how long it took, its model calls and their cost, and the outcome.
`scope=session` keeps the actions of one browser's demo session (whatever roles it switched through), `all`
everyone's since the reset, including the background's own runs (BACKGROUND_AI=eager).

The panel shows ids and the labels the person saw, never patient details (traces hold references only).
"""

from __future__ import annotations

from datetime import datetime

from app.agents import cause as causes
from app.agents import ondemand, registry
from app.agents import trace as traces

# What a run of an agent did, for the agents that run without an item kind of app/agents/ondemand.py
AGENT_WHAT = {
    "order_review": "Reviewed an order",
    "medication_reconciliation": "Judged the new medications of a reconciliation",
    "control_tower": "Wrote an exception's narrative and recommended actions",
    "requisition_extract": "Extracted a requisition's fields",
    "requisition_triage": "Triaged a requisition",
    "protocol_suggest": "Suggested a protocol",
    "feedback_classify": "Labelled a feedback comment",
    "cxr_draft": "Drafted a chest X-ray report",
    "prep_translation": "Drafted a prep translation",
    "policy_qa": "Answered a policy question",
    "clinical_kg_parse": "Parsed a clinical question",
    "clinical_kg_answer": "Answered a clinical question",
    "referral_weekly_summary": "Wrote the referral weekly summary",
    "mri_implant_extract": "Extracted implants from an MRI questionnaire",
    "call_summary": "Summarised a phone call",
    "voice_agent": "Answered on the phone",
    "call_center": "Understood a caller",
    "followup_calls": "Classified a follow-up call's answers",
    "discharge_summary": "Drafted a discharge summary",
    "patient_instructions": "Drafted patient instructions",
    "nursing_handoff": "Drafted a shift handoff",
    "patient_message_triage": "Triaged a patient message",
    "deid_check": "Checked a text for identifiers",
}
KIND_WHAT = {
    "order_review": "Reviewed an order",
    "medrec": "Judged the new medications of a reconciliation",
    "exception": "Wrote an exception's narrative and recommended actions",
    "requisition": "Extracted, triaged and protocolled a requisition",
    "feedback": "Labelled a feedback comment's sentiment and themes",
}
FAILED = {"failed", "unavailable", "refused", "needs_human"}


def _what(agent_ids: list[str], kind: str | None) -> str:
    if kind in KIND_WHAT:
        return KIND_WHAT[kind]
    for agent_id in agent_ids:
        if agent_id in AGENT_WHAT:
            return AGENT_WHAT[agent_id]
    spec = registry.agents().get(agent_ids[0]) if agent_ids else None
    return spec.purpose.rstrip(".") if spec is not None else "AI call"


def _item(cause: dict, group: list[traces.AgentTrace]) -> dict | None:
    if cause.get("kind") or cause.get("label"):
        return {k: cause.get(k) for k in ("kind", "item_id", "label", "link")}
    first = group[0]
    ref = first.context_id or first.encounter_id or next((r for r in first.input_refs if "/" in r), None)
    return {"kind": None, "item_id": ref, "label": ref, "link": None} if ref else None


def _action(group: list[traces.AgentTrace]) -> dict:
    group = sorted(group, key=lambda t: t.started_at)
    cause = next((t.cause for t in group if t.cause), None) or {}
    started = group[0].started_at
    finished = max((t.finished_at or t.started_at) for t in group)
    calls = [c for t in group for c in t.llm_calls]
    agent_ids = list(dict.fromkeys(t.agent_id for t in group))
    outcomes = [t.outcome for t in group]
    outcome = "running" if "running" in outcomes else next((o for o in outcomes if o in FAILED), "completed")
    if any(c.status in ("unavailable", "refused") for c in calls) and outcome == "completed" and \
            all(c.status != "ok" and c.status != "retried_ok" for c in calls):
        outcome = "unavailable"
    trigger = cause.get("trigger") or "background"
    priced = [_cost(group[0].mode, c) for c in calls]
    return {
        "id": cause.get("action_id") or group[0].run_id, "run_ids": [t.run_id for t in group],
        "what": _what(agent_ids, cause.get("kind")), "agents": agent_ids, "item": _item(cause, group),
        "trigger": trigger, "trigger_text": causes.TRIGGER_TEXT.get(trigger, trigger), "note": cause.get("note"),
        "by": {"id": cause.get("user_id"), "name": cause.get("user_name"), "role": cause.get("role")}
        if cause.get("user_id") else None,
        "session": cause.get("session"), "started_at": started, "finished_at": finished,
        "duration_ms": int((finished - started).total_seconds() * 1000), "calls": len(calls),
        "cost_usd": round(sum(usd for usd, _ in priced), 6), "cost_estimated": any(est for _, est in priced),
        "tokens": sum(c.tokens_in + c.tokens_out for c in calls),
        "outcome": outcome, "mode": group[0].mode, "models": sorted({c.model for c in calls}),
    }


def _cost(mode: str | None, call: traces.LlmCallRecord) -> tuple[float, bool]:
    """What a call cost; a mock call's list-price estimate (as AI Ops shows it: nothing is billed)."""
    from app.aiops import cost

    return cost.call_cost(mode, call.model, call.cost_usd, call.tokens_in, call.tokens_out)


def _calls_only(t: traces.AgentTrace) -> bool:
    """A run that called no model (a module acting, the AI Ops alerts) is not AI work to show."""
    return bool(t.llm_calls) or t.outcome == "running"


SCOPE_RUNS = 1500  # the panel polls: it reads at most this many of the newest runs (an eager demo's day has fewer)


def _scoped(store, session: str | None, scope: str) -> list[traces.AgentTrace]:
    """The runs that called a model, of one browser's session or everyone's, newest first."""
    t = traces.table(store)
    if scope == "session":
        if not session:
            return []
        found = t.where(t.c.cause["session"].astext == session, newest_first=True, limit=SCOPE_RUNS)
    else:
        found = t.where(newest_first=True, limit=SCOPE_RUNS)
    return [x for x in found if _calls_only(x)]


def actions(found: list[traces.AgentTrace], *, limit: int = 150) -> list[dict]:
    groups: dict[str, list[traces.AgentTrace]] = {}
    for trace in found:
        key = (trace.cause or {}).get("action_id") or trace.run_id
        groups.setdefault(key, []).append(trace)
    out = [_action(g) for g in groups.values()]
    out.sort(key=lambda a: a["started_at"], reverse=True)
    return out[:limit]


def totals(found: list[traces.AgentTrace], acts: list[dict]) -> dict:
    """Over the whole scope (also the actions past the listed ones)."""
    priced = [_cost(t.mode, c) for t in found for c in t.llm_calls]
    return {"runs": len(found), "calls": len(priced), "cost_usd": round(sum(usd for usd, _ in priced), 6),
            "cost_estimated": any(est for _, est in priced), "actions": len(acts),
            "seconds": round(sum(a["duration_ms"] for a in acts) / 1000, 1)}


def view(store, *, session: str | None, scope: str = "session") -> dict:
    from app.core import config
    from app.llm.gateway import get_gateway

    live = ondemand.snapshot()
    if scope == "session":
        keep = (lambda e: (e.get("cause") or {}).get("session") == session) if session else (lambda e: False)
        live = {k: [e for e in v if keep(e)] for k, v in live.items()}
    found = _scoped(store, session, scope)
    acts = actions(found, limit=10_000)
    return {"scope": scope, "session": session, "mode": get_gateway().mode,
            "background_ai": config.settings.background_ai, "since": store.modules.get("seed_time"),
            "now": datetime.now(), "totals": totals(found, acts),
            "running": live["running"], "queued": live["queued"], "actions": acts[:150],
            "kinds": {name: {"noun": k.noun, "calls": k.calls, "typical_s": ondemand.typical_s(name)}
                      for name, k in ondemand.KINDS.items()}}
