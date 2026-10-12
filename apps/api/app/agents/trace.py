"""Run traces, idempotency records and the FHIR Provenance of AI output (spec 6.4).

Every agent run leaves one trace (table `agent_traces`): run_id, agent and
version, model, prompt_version, input_refs, tool_calls (tool_id, args_hash,
result_ref, latency_ms, status), output_ref, confidence, human_action, outcome,
tokens and cost. A runtime agent's run is opened by app/agents/runtime.py; an
embedded agent's model call (the imaging systems) gets a one-call trace from the
LLM gateway. 6.5 workflows and the 6.6 AI Ops page read traces; there is no other log.

Traces hold references and hashes only (resource ids, args hashes), never
arguments or text. When a run writes FHIR resources, a Provenance
`prov-<run_id>` records them as targets with the agent (Device: agent_id and
version) as author, the on-behalf-of user, the input resources as entities and
the prompt version, model and run id as extensions; a later signature adds the
signer. Every resource an agent writes carries the extension `ai-run`, so any
output leads back to its trace and Provenance (`lineage`).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
from collections.abc import Iterator
from contextvars import ContextVar
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.ehr.codes import EXT

TRACES = "agent_traces"
IDEMPOTENCY = "tool_idempotency"
AI_RUN = EXT + "ai-run"  # on every resource an agent writes: the run id
AI_AGENT = EXT + "ai-agent"  # agent_id@version
PROVENANCE_EXT = {"prompt_version": EXT + "prompt-version", "model": EXT + "model", "run_id": EXT + "run-id"}
AGENT_SYSTEM = "urn:demo-hospital:agent"


class ToolCallRecord(BaseModel):
    tool_id: str
    side_effect_level: str
    args_hash: str
    status: str  # ok, replayed, refused, failed, fallback, approval_required
    reason: str | None = None  # refusal or failure code
    result_ref: list[str] = Field(default_factory=list)
    latency_ms: int = 0
    attempts: int = 0
    idempotency_key: str | None = None
    approval_task_id: str | None = None
    at: datetime


class LlmCallRecord(BaseModel):
    call_id: str
    agent_id: str  # a run can call another agent on its way (the de-identification second pass)
    model: str
    prompt_version: str
    status: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0


class AgentTrace(BaseModel):
    run_id: str
    agent_id: str
    agent_version: str
    kind: str  # run (runtime agent) or call (an embedded agent's model call)
    mode: str | None = None  # anthropic, gemini or mock
    model: str | None = None
    prompt_version: str | None = None
    eval_status: str
    evaluated: bool  # False: the output carries the "not evaluated" flag
    actor_id: str | None = None  # on whose behalf ("system" for background runs)
    actor_role: str | None = None
    encounter_id: str | None = None
    context_id: str | None = None  # a non-FHIR subject (the phone agent's call session)
    input_refs: list[str] = Field(default_factory=list)
    llm_calls: list[LlmCallRecord] = Field(default_factory=list)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    output_refs: list[str] = Field(default_factory=list)
    confidence: float | None = None
    human_action: dict | None = None  # {role, user_id, decision, at, task_id}
    outcome: str = "running"  # running, completed, needs_human, refused, failed, unavailable
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    provenance_id: str | None = None
    started_at: datetime
    finished_at: datetime | None = None

    @property
    def output_ref(self) -> str | None:  # the spec's singular name: the run's main output
        return self.output_refs[0] if self.output_refs else None

    def add_llm_call(self, record: LlmCallRecord) -> None:
        self.llm_calls.append(record)
        if record.agent_id == self.agent_id:
            self.model, self.prompt_version = record.model, record.prompt_version
        self.tokens_in += record.tokens_in
        self.tokens_out += record.tokens_out
        self.cost_usd = round(self.cost_usd + record.cost_usd, 6)


class IdempotencyRecord(BaseModel):
    key: str
    agent_id: str
    tool_id: str
    encounter_id: str | None = None
    run_id: str
    status: str  # done (result replayable) or awaiting_approval (an approval Task is open)
    result: dict[str, Any] = Field(default_factory=dict)  # encrypted at rest
    approval_task_id: str | None = None
    created_at: datetime
    expires_at: datetime


# ---------- hashing ----------


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def args_hash(args: dict) -> str:
    return hashlib.sha256(canonical(args).encode()).hexdigest()


def idempotency_key(agent_id: str, encounter_id: str | None, tool_id: str, args: dict) -> str:
    """sha256(agent_id, encounter_id, tool_id, canonical(args)) (6.4)."""
    return hashlib.sha256(canonical([agent_id, encounter_id or "", tool_id, args]).encode()).hexdigest()


# ---------- the run in progress ----------

_active: ContextVar[AgentTrace | None] = ContextVar("agent_trace", default=None)


def active() -> AgentTrace | None:
    """The trace of the agent run in progress in this context (model calls attach to it)."""
    return _active.get()


@contextlib.contextmanager
def activate(trace: AgentTrace) -> Iterator[AgentTrace]:
    token = _active.set(trace)
    try:
        yield trace
    finally:
        _active.reset(token)


def table(store=None):
    from app.core.store import get_store

    return (store or get_store()).module(TRACES, dict)


def save(trace: AgentTrace, store=None) -> None:
    try:
        table(store)[trace.run_id] = trace
    except RuntimeError:  # no unit of work (a script without a database): nothing to record into
        pass


def get(run_id: str, store=None) -> AgentTrace | None:
    return table(store).get(run_id)


def run_of(resource: dict | None) -> str | None:
    """The run that wrote a resource (its ai-run extension)."""
    return next((e.get("valueString") for e in (resource or {}).get("extension") or [] if e.get("url") == AI_RUN),
                None)


def stamp(resource: dict, run_id: str, agent_label: str) -> dict:
    """Mark a resource an agent writes: the run (for lineage) and the agent."""
    exts = [e for e in resource.get("extension") or [] if e.get("url") not in (AI_RUN, AI_AGENT)]
    resource["extension"] = [*exts, {"url": AI_RUN, "valueString": run_id}, {"url": AI_AGENT, "valueString": agent_label}]
    return resource


# ---------- Provenance ----------


def provenance_id(run_id: str) -> str:
    return "prov-" + run_id.lower()


def provenance(trace: AgentTrace, *, recorded: str, on_behalf_of: dict | None) -> dict:
    """The FHIR Provenance of a run's FHIR output."""
    targets = [r for r in trace.output_refs if "/" in r and not r.startswith("LlmCall/")]
    agent: dict = {"type": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/provenance-participant-type",
                                        "code": "assembler", "display": "Assembler"}]},
                   "who": {"type": "Device", "identifier": {"system": AGENT_SYSTEM,
                                                            "value": f"{trace.agent_id}@{trace.agent_version}"},
                           "display": f"AI agent {trace.agent_id} {trace.agent_version}"}}
    if on_behalf_of:
        agent["onBehalfOf"] = on_behalf_of
    exts = [{"url": PROVENANCE_EXT["run_id"], "valueString": trace.run_id}]
    if trace.prompt_version:
        exts.append({"url": PROVENANCE_EXT["prompt_version"], "valueString": trace.prompt_version})
    if trace.model:
        exts.append({"url": PROVENANCE_EXT["model"], "valueString": trace.model})
    return {"resourceType": "Provenance", "id": provenance_id(trace.run_id),
            "target": [{"reference": r} for r in targets], "recorded": recorded,
            "activity": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v3-DataOperation",
                                     "code": "CREATE"}]},
            "agent": [agent],
            "entity": [{"role": "source", "what": {"reference": r}} for r in trace.input_refs if "/" in r],
            "extension": exts}


def add_signer(prov: dict, signer: dict, role: str) -> dict:
    """The person who signed the output joins the Provenance as verifier."""
    prov.setdefault("agent", []).append({
        "type": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/provenance-participant-type",
                             "code": "verifier", "display": "Verifier"}], "text": role},
        "who": signer})
    return prov


def provenance_fields(prov: dict | None) -> dict:
    """prompt_version, model and run_id of a Provenance."""
    out = {}
    for e in (prov or {}).get("extension") or []:
        for key, url in PROVENANCE_EXT.items():
            if e.get("url") == url:
                out[key] = e.get("valueString")
    return out
