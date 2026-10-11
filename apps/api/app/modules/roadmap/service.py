"""Roadmap cards (spec 8.1) and the safety tiers (spec 8.3), built from the registries so they never drift.

Two small YAML files hold only the words: `config/roadmap.yaml` (one card per row of the 8.1 table) and
`config/safety.yaml` (the tiers' definitions, the side-effect levels' preconditions, the Level 0-4 mapping, the
guardrails, the honest boundaries and the 6.7 positioning sentences). Everything else is read when a view is built:

- a card's registry entry (apps/api/config/agents/<id>.yaml) and its storyline step (app/modules/storyline/script.py);
- each tier's registered agents and modules with their sign-off roles, and each side-effect level's registered tools
  (app/agents/registry.py), plus which levels each tier's entries can reach;
- the guardrails' latest eval results and the number of live-model runs (app/aiops/runs.py).

`check()` lists every inconsistency between the words and the registries (a card's tier differing from its registry
entry, a tier or level no YAML describes, a storyline step that is not a stub); the tests and the safety doc's
generator call it. The web pages (/roadmap, /safety) and docs/safety-tiers.md read the same views.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from app.agents import registry
from app.aiops import runs

CONFIG = Path(__file__).resolve().parents[3] / "config"
TIERS = ("ops", "documentation", "clinical_ds")
LEVELS = ("read", "recommend", "action", "privileged")
KIND_ORDER = {"runtime_agent": 0, "embedded_agent": 1, "module": 2}


class RoadmapError(ValueError):
    pass


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Card(_Strict):
    id: str
    title: str
    pain: str
    data_source: str
    risk_tier: Literal["ops", "documentation", "clinical_ds"]
    reuses: str
    reuse_refs: list[str] = Field(default_factory=list)
    demo: Literal["card", "preset"]
    demo_note: str
    registry: str | None = None
    storyline: str | None = None
    spec_ref: str | None = None


class Roadmap(_Strict):
    footer: str
    cards: list[Card]


class Tier(_Strict):
    id: Literal["ops", "documentation", "clinical_ds"]
    name: str
    definition: str
    spec_examples: str
    allowed_ai_actions: list[str]
    never: list[str] = Field(default_factory=list)
    spec_signoff: str
    regulatory: str


class Level(_Strict):
    id: Literal["read", "recommend", "action", "privileged"]
    meaning: str
    precondition: str


class Mapping(_Strict):
    level: str
    tiers: list[Literal["ops", "documentation", "clinical_ds"]]
    tools: list[Literal["read", "recommend", "action", "privileged"]]
    note: str | None = None


class Live(_Strict):
    agent: str | None = None
    gates: list[str] = Field(default_factory=list)
    platform: str | None = None


class Guardrail(_Strict):
    id: str
    title: str
    rule: str
    spec: str
    code: list[str]
    evidence: str
    live: Live | None = None


class Boundary(_Strict):
    id: str
    text: str
    text_when_live: str | None = None  # said instead once a run on a real model is committed
    live: Literal["runs"] | None = None


class Safety(_Strict):
    tiers: list[Tier]
    side_effect_levels: list[Level]
    level_mapping: list[Mapping]
    guardrails: list[Guardrail]
    honest_boundaries: list[Boundary]
    positioning: list[str]
    definition_of_done: str


def _yaml(name: str) -> dict:
    return yaml.safe_load((CONFIG / name).read_text(encoding="utf-8")) or {}


@lru_cache(maxsize=1)
def roadmap() -> Roadmap:
    return Roadmap.model_validate(_yaml("roadmap.yaml"))


@lru_cache(maxsize=1)
def safety() -> Safety:
    return Safety.model_validate(_yaml("safety.yaml"))


def _steps() -> dict:
    from app.modules.storyline import script

    return script.BY_KEY


def registry_entry(agent_id: str) -> dict | None:
    """A registry entry as the cards and tiers show it (the effective eval status, as the runtime sees it)."""
    spec = registry.agent(agent_id)
    if spec is None:
        return None
    return {"agent_id": spec.agent_id, "version": spec.version, "kind": spec.kind, "owner": spec.owner,
            "purpose": spec.purpose, "risk_tier": spec.risk_tier, "allowed_tools": spec.allowed_tools,
            "required_signoff_role": spec.required_signoff_role, "cosign": spec.cosign,
            "consent_required": spec.consent_required, "deployment_status": spec.deployment_status,
            "eval_status": spec.eval_status.status, "path": f"apps/api/config/agents/{spec.agent_id}.yaml"}


def step_view(key: str) -> dict | None:
    step = _steps().get(key)
    if step is None:
        return None
    return {"n": step.n, "key": step.key, "title": step.title, "stub": step.stub, "href": f"/storyline#step-{step.n}"}


def cards_view() -> dict:
    data = roadmap()
    cards = []
    for card in data.cards:
        cards.append({**card.model_dump(), "registry_entry": registry_entry(card.registry) if card.registry else None,
                      "step": step_view(card.storyline) if card.storyline else None, "footer": data.footer})
    return {"footer": data.footer, "cards": cards,
            "counts": {"cards": len(cards), "preset": sum(c.demo == "preset" for c in data.cards),
                       "by_tier": {t: sum(c.risk_tier == t for c in data.cards) for t in TIERS}}}


_platform_cache: dict = {}


def _latest_platform(name: str) -> dict | None:
    """The newest run with this platform suite (run summaries leave the platform suites out, so the files are read;
    cached until the runs change)."""
    found = runs.summaries()
    key = (name, tuple(r["run_id"] for r in found))
    if key in _platform_cache:
        return _platform_cache[key]
    result = None
    for run in found:
        try:
            data = runs.load(run["run_id"])
        except LookupError:
            continue
        suite = (data.get("platform") or {}).get(name)
        if suite:
            result = {"run_id": run["run_id"], "mode": run.get("mode"), "started_at": run.get("started_at"),
                      "passed": suite.get("passed"), "cases": suite.get("cases"),
                      "cases_passed": suite.get("cases_passed"),
                      "unauthorised_tool_calls": suite.get("unauthorised_tool_calls")}
            break
    _platform_cache.clear()
    _platform_cache[key] = result
    return result


def _latest_agent(agent_id: str, gates: list[str]) -> dict | None:
    """The newest run that judged the agent (a run that found no eval set for it is skipped)."""
    for run in runs.summaries():
        entry = run["agents"].get(agent_id)
        if not entry or entry.get("variant") or run.get("variant") or entry.get("status") not in ("met", "below"):
            continue
        found = {g: entry["gates"][g] for g in gates if g in (entry.get("gates") or {})}
        return {"run_id": run["run_id"], "mode": entry.get("mode") or run.get("mode"),
                "started_at": run.get("started_at"), "gates": found}
    return None


def _live(guard: Guardrail) -> dict | None:
    if guard.live is None:
        return None
    if guard.live.platform:
        return _latest_platform(guard.live.platform)
    if guard.live.agent:
        return _latest_agent(guard.live.agent, guard.live.gates)
    return None


def runs_view() -> dict:
    found = runs.summaries()
    live = [r for r in found if runs.is_live(r.get("mode"))]
    return {"runs": len(found), "live_runs": len(live), "latest": found[0]["run_id"] if found else None,
            "latest_live": live[0]["run_id"] if live else None}


def safety_view() -> dict:
    data = safety()
    agents = registry.agents()
    tools = registry.tools()
    tiers = []
    for tier in data.tiers:
        entries = sorted((s for s in agents.values() if s.risk_tier == tier.id),
                         key=lambda s: (KIND_ORDER[s.kind], s.agent_id))
        reach = {lvl: sorted(s.agent_id for s in entries
                             if any((t := tools.get(name)) is not None and t.side_effect_level == lvl
                                    for name in s.allowed_tools)) for lvl in LEVELS}
        tiers.append({**tier.model_dump(),
                      "entries": [registry_entry(s.agent_id) for s in entries],
                      "signoff_roles": sorted({r for s in entries for r in s.required_signoff_role}),
                      "counts": {k: sum(s.kind == k for s in entries) for k in KIND_ORDER},
                      "reach": reach})
    levels = []
    for level in data.side_effect_levels:
        found = sorted((t for t in tools.values() if t.side_effect_level == level.id), key=lambda t: t.tool_id)
        levels.append({**level.model_dump(), "tools": [
            {"tool_id": t.tool_id, "description": t.description, "internal": t.internal,
             "idempotent": t.idempotency.required, "max_attempts": t.retry.max_attempts, "audit_level": t.audit_level,
             "approver_roles": t.permission_policy.approver_roles if isinstance(t.permission_policy.approver_roles,
                                                                                list) else ["signoff"]}
            for t in found]})
    run_info = runs_view()
    boundaries = []
    for b in data.honest_boundaries:
        live = b.live == "runs"
        text = b.text_when_live if live and run_info["live_runs"] and b.text_when_live else b.text
        boundaries.append({"id": b.id, "text": text, "facts": run_info if live else None})
    return {
        "tiers": tiers, "side_effect_levels": levels,
        "level_mapping": [m.model_dump() for m in data.level_mapping],
        "guardrails": [{**g.model_dump(exclude={"live"}), "live": _live(g)} for g in data.guardrails],
        "honest_boundaries": boundaries,
        "positioning": data.positioning, "definition_of_done": data.definition_of_done,
        "platform": {"entries": len(agents), "by_kind": {k: sum(s.kind == k for s in agents.values())
                                                         for k in KIND_ORDER},
                     "tools": len(tools), "by_level": {lvl: sum(t.side_effect_level == lvl for t in tools.values())
                                                       for lvl in LEVELS},
                     "runs": run_info},
    }


def check(repo_root: Path | None = None) -> list[str]:
    """Every inconsistency between the two YAML files and the registries (empty: consistent). With the repository
    root, the cards' reuse references and the guardrails' code paths must exist too."""
    problems: list[str] = []
    data, words = roadmap(), safety()
    agents, tools = registry.agents(), registry.tools()
    seen: set[str] = set()
    for card in data.cards:
        if card.id in seen:
            problems.append(f"card {card.id}: duplicate id")
        seen.add(card.id)
        if card.registry:
            spec = agents.get(card.registry)
            if spec is None:
                problems.append(f"card {card.id}: registry entry {card.registry} does not exist")
            elif spec.risk_tier != card.risk_tier:
                problems.append(f"card {card.id}: risk tier {card.risk_tier} but the registry says {spec.risk_tier}")
        if card.storyline:
            step = _steps().get(card.storyline)
            if step is None:
                problems.append(f"card {card.id}: storyline step {card.storyline} does not exist")
            elif card.registry and not step.stub:
                problems.append(f"card {card.id}: storyline step {card.storyline} is not marked as a roadmap stub")
        if card.demo == "preset" and not card.storyline:
            problems.append(f"card {card.id}: preset data but no storyline step")
        if repo_root is not None:
            problems += [f"card {card.id}: {p} does not exist" for p in card.reuse_refs if not (repo_root / p).exists()]
    stubs = {s.key for s in _steps().values() if s.stub}
    linked = {c.storyline for c in data.cards if c.registry}
    problems += [f"storyline stub step {k} has no roadmap card with a registry entry" for k in sorted(stubs - linked)]
    tier_ids = [t.id for t in words.tiers]
    if sorted(tier_ids) != sorted(TIERS):
        problems.append(f"safety.yaml tiers {tier_ids} are not exactly {list(TIERS)}")
    problems += [f"registry entry {s.agent_id}: tier {s.risk_tier} is not described in safety.yaml"
                 for s in agents.values() if s.risk_tier not in tier_ids]
    level_ids = [lv.id for lv in words.side_effect_levels]
    if sorted(level_ids) != sorted(LEVELS):
        problems.append(f"safety.yaml side-effect levels {level_ids} are not exactly {list(LEVELS)}")
    problems += [f"tool {t.tool_id}: level {t.side_effect_level} is not described in safety.yaml"
                 for t in tools.values() if t.side_effect_level not in level_ids]
    mapped = sorted(m.level for m in words.level_mapping)
    if mapped != ["0-1", "2", "3", "4"]:
        problems.append(f"level mapping covers {mapped}, not Level 0-4")
    for guard in words.guardrails:
        if guard.live and guard.live.agent and guard.live.agent not in agents:
            problems.append(f"guardrail {guard.id}: agent {guard.live.agent} is not registered")
        if repo_root is not None:
            problems += [f"guardrail {guard.id}: {p} does not exist" for p in guard.code if not (repo_root / p).exists()]
    return problems
