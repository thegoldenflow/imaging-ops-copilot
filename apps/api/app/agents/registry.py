"""Agent and tool registries (spec 6.4): `config/agents/<agent_id>.yaml`, `config/tools/<tool_id>.yaml`.

Every AI function runs as a registered agent calling registered tools. The agent
registry is the single source of truth for what an agent (or a module that writes
to the EHR) may do; it replaced WP4's `config/modules.registry.json` (6.3), whose
fields moved here unchanged: tier -> `risk_tier`, `required_signoff_role`,
`cosign`, `consent_required`; `writes_allowed` became `allowed_tools`, and the
resources and statuses an entry may write are now derived from the `fhir_writes`
of its tools. FhirGateway (writes), the signing service (who signs) and the web
app (sign buttons, "not evaluated" flags) read this registry.

Kinds of entry:

- `runtime_agent`: hosted by the agent runtime (app/agents/runtime.py). Context
  only through its read tools, every side effect through the Tool Gateway, code
  in `entrypoint` (grep-checked: no FhirGateway, HTTP client or database). The
  7.1-7.4 agents are registered with their WP4 rights and get their code in WP5-WP8.
- `embedded_agent`: an AI step inside its module (the imaging systems): a model
  call through the LLM gateway, which refuses unregistered tasks, applies the
  eval gate and writes a trace. The phone agent's tool calls go through the Tool Gateway.
- `module`: a module without a model that writes to the EHR (registration desk,
  consent management, ...); registered so the gateway can check its writes.

Loading an unregistered agent raises `UnregisteredAgent`. `gate()` applies the
release rule: in `APP_MODE=prod` an agent runs only with eval_status passed and
deployment_status prod_ready; in demo mode an agent that has not passed runs and
its output carries a "not evaluated" flag.
"""

from __future__ import annotations

import contextlib
import os
import re
from collections.abc import Iterator
from functools import cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.agents import jsonschema
from app.core.config import settings

CONFIG = Path(__file__).resolve().parents[2] / "config"
AGENTS_DIR = Path(os.environ.get("AGENTS_REGISTRY", CONFIG / "agents"))
TOOLS_DIR = Path(os.environ.get("TOOLS_REGISTRY", CONFIG / "tools"))

CONSENT_CATEGORIES = ("ai_processing", "followup_call", "sms")
SIDE_EFFECT_LEVELS = ("read", "recommend", "action", "privileged")
# What a recommend tool may produce: a draft or a proposal for a person to review, nothing final.
DRAFT_STATUSES = frozenset({"preliminary", "proposed", "requested", "draft"})
RoleName = Literal["front_desk", "technologist", "radiologist", "operations_manager", "medical_director", "admin",
                   "referrer", "physician", "nurse", "pharmacist", "clerk"]
CallerRole = Literal["front_desk", "technologist", "radiologist", "operations_manager", "medical_director", "admin",
                     "referrer", "physician", "nurse", "pharmacist", "clerk", "system"]
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
TOOL_ID = re.compile(r"^[a-z][A-Za-z0-9]+$")
AGENT_ID = re.compile(r"^[a-z][a-z0-9_]*$")


class UnregisteredAgent(LookupError):
    """No registry entry for this agent: the runtime and the LLM gateway refuse to load it."""


class AgentNotDeployable(PermissionError):
    """Registered, but the release gate refuses it in this mode (prod: eval passed and prod_ready)."""


class RegistryError(ValueError):
    """The registry files are inconsistent (raised at load, so a bad entry never goes live)."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------- agents ----------


class DataScope(_Strict):
    resources: list[str] = Field(default_factory=list)  # FHIR resource types the agent may read
    # encounter: only the run's encounter; own_unit: the patients of the caller's units (FhirGateway scope);
    # hospital: any patient the caller may see; none: no patient data
    scope: Literal["encounter", "own_unit", "hospital", "none"] = "none"


class ModelPolicy(_Strict):
    tier: Literal["reasoning", "fast", "voice"]  # model id from CLAUDE_MODEL_* / GEMINI_MODEL_* (never in the registry)
    max_tokens: int = Field(ge=1, le=64000)
    # None: provider default, nothing sent (Claude Sonnet 5.5 refuses non-default sampling values)
    temperature_max: float | None = Field(default=None, ge=0, le=1)


class EvalStatus(_Strict):
    status: Literal["pending", "passed", "failed", "not_applicable"]
    run_id: str | None = None
    note: str = ""


class AgentSpec(_Strict):
    agent_id: str
    version: str
    kind: Literal["runtime_agent", "embedded_agent", "module"]
    owner: str
    purpose: str
    risk_tier: Literal["ops", "documentation", "clinical_ds"]
    allowed_tools: list[str] = Field(default_factory=list)
    data_scope: DataScope = Field(default_factory=DataScope)
    model_policy: ModelPolicy | None = None
    prompt_version: str | None = None  # the prompt version pointer (6.6: a rollback is a one-line change here)
    required_signoff_role: list[RoleName] = Field(default_factory=list)
    cosign: bool = False  # every listed role signs (medication reconciliation); otherwise any one of them
    consent_required: list[Literal["ai_processing", "followup_call", "sms"]] = Field(default_factory=list)
    eval_status: EvalStatus
    deployment_status: Literal["dev", "demo", "prod_ready"]
    entrypoint: str | None = None  # runtime agents: the module with the agent's code
    confidence_threshold: float | None = Field(default=None, ge=0, le=1)  # below it a person reviews (6.5)

    @model_validator(mode="after")
    def _consistent(self) -> AgentSpec:
        if not AGENT_ID.match(self.agent_id):
            raise ValueError(f"agent_id {self.agent_id!r}: lower case letters, digits and underscores")
        if not SEMVER.match(self.version):
            raise ValueError(f"{self.agent_id}: version {self.version!r} is not a semantic version")
        if self.kind == "module":
            if self.model_policy is not None or self.eval_status.status != "not_applicable":
                raise ValueError(f"{self.agent_id}: a module has no model, so no model_policy and eval not_applicable")
        else:
            if self.model_policy is None:
                raise ValueError(f"{self.agent_id}: an agent needs a model_policy")
            if self.eval_status.status == "not_applicable":
                raise ValueError(f"{self.agent_id}: an agent's eval_status is pending, passed or failed")
        if self.kind != "runtime_agent" and self.entrypoint:
            raise ValueError(f"{self.agent_id}: only runtime agents have an entrypoint")
        if self.risk_tier != "ops" and not self.required_signoff_role:
            raise ValueError(f"{self.agent_id}: documentation and clinical_ds entries need a signer")
        if self.cosign and len(self.required_signoff_role) < 2:
            raise ValueError(f"{self.agent_id}: cosign needs at least two roles")
        return self

    @property
    def label(self) -> str:
        return f"{self.agent_id}@{self.version}"

    @property
    def tier(self) -> str:  # 6.3 name
        return self.risk_tier

    @property
    def is_agent(self) -> bool:
        return self.kind != "module"

    @property
    def evaluated(self) -> bool:
        """False: the output carries the "not evaluated" flag (demo) or the agent is refused (prod)."""
        return self.eval_status.status in ("passed", "not_applicable")

    def writes(self) -> dict[str, set[str]]:
        """FHIR resource type -> statuses this entry may write, from its tools' fhir_writes."""
        out: dict[str, set[str]] = {}
        for tool_id in self.allowed_tools:
            spec = tool(tool_id)
            for rtype, statuses in (spec.fhir_writes if spec else {}).items():
                out.setdefault(rtype, set()).update(statuses)
        return out

    def allows(self, resource_type: str, status: str | None) -> bool:
        statuses = self.writes().get(resource_type)
        return statuses is not None and ("*" in statuses or status in statuses)


# ---------- tools ----------


class PermissionPolicy(_Strict):
    roles: list[CallerRole]  # roles on whose behalf an agent may call the tool ("system": background runs)
    # privileged tools: roles whose recorded approval lets the call run ("signoff": the agent's signers)
    approver_roles: list[RoleName] | Literal["signoff"] = Field(default_factory=list)
    # which argument names the target, for the data-scope and consent checks
    target: dict[Literal["encounter", "patient", "document", "task", "appointment", "flag", "unit"], str] = Field(
        default_factory=dict)


class Idempotency(_Strict):
    required: bool = False
    window_hours: int = Field(default=24, ge=1, le=168)


class Retry(_Strict):
    max_attempts: int = Field(default=1, ge=1, le=5)  # 1 = no automatic retry
    backoff_ms: int = Field(default=0, ge=0, le=10000)  # first wait; doubles per attempt


class ToolSpec(_Strict):
    tool_id: str
    description: str
    side_effect_level: Literal["read", "recommend", "action", "privileged"]
    handler: str  # "package.module:function" (a domain binding in app/agents/handlers.py)
    domain: Literal["fhir", "signing", "consent", "events", "imaging_frontdesk", "runtime"]
    reads: list[str] = Field(default_factory=list)  # FHIR resource types a read tool returns
    fhir_writes: dict[str, list[str]] = Field(default_factory=dict)  # resource type -> statuses it writes
    input_schema: dict
    output_schema: dict
    permission_policy: PermissionPolicy
    idempotency: Idempotency = Field(default_factory=Idempotency)
    timeout_ms: int = Field(ge=100, le=120000)
    retry: Retry = Field(default_factory=Retry)
    audit_level: Literal["standard", "detailed", "double"]
    internal: bool = False  # used by the runtime itself, never on an agent's allow-list

    @model_validator(mode="after")
    def _consistent(self) -> ToolSpec:
        level = self.side_effect_level
        if not TOOL_ID.match(self.tool_id):
            raise ValueError(f"tool_id {self.tool_id!r}: lower camel case")
        for name, schema in (("input_schema", self.input_schema), ("output_schema", self.output_schema)):
            if problems := jsonschema.check_schema(schema):
                raise ValueError(f"{self.tool_id}.{name}: {'; '.join(problems)}")
            if schema.get("type") != "object":
                raise ValueError(f"{self.tool_id}.{name}: the top level is an object")
        if level == "read" and self.fhir_writes:
            raise ValueError(f"{self.tool_id}: a read tool writes nothing")
        if level == "recommend":
            for rtype, statuses in self.fhir_writes.items():
                if not set(statuses) <= DRAFT_STATUSES:
                    raise ValueError(f"{self.tool_id}: a recommend tool writes drafts or proposals only "
                                     f"({rtype}: {statuses})")
        if level in ("action", "privileged") and not self.idempotency.required:
            raise ValueError(f"{self.tool_id}: {level} tools need an idempotency key")
        if level == "privileged":
            if self.retry.max_attempts != 1:
                raise ValueError(f"{self.tool_id}: privileged tools get 0 automatic retries")
            if self.audit_level != "double":
                raise ValueError(f"{self.tool_id}: privileged tools are audited twice (audit_level double)")
            if not self.permission_policy.approver_roles:
                raise ValueError(f"{self.tool_id}: privileged tools name who approves (approver_roles)")
            if "approval_task_id" not in (self.input_schema.get("required") or []):
                raise ValueError(f"{self.tool_id}: privileged tools take the approval record (approval_task_id)")
        elif self.permission_policy.approver_roles:
            raise ValueError(f"{self.tool_id}: only privileged tools have approver_roles")
        return self


# ---------- loading ----------


class Registries(BaseModel):
    agents: dict[str, AgentSpec]
    tools: dict[str, ToolSpec]


def _read_dir(folder: Path, model: type[BaseModel], key: str) -> dict:
    out = {}
    for path in sorted(folder.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            spec = model.model_validate(data)
        except (yaml.YAMLError, ValidationError) as e:
            raise RegistryError(f"{path.name}: {e}") from e
        if getattr(spec, key) != path.stem:
            raise RegistryError(f"{path.name}: {key} {getattr(spec, key)!r} does not match the file name")
        out[path.stem] = spec
    return out


def _cross_check(agents: dict[str, AgentSpec], tools: dict[str, ToolSpec]) -> None:
    for spec in agents.values():
        for tool_id in spec.allowed_tools:
            t = tools.get(tool_id)
            if t is None:
                raise RegistryError(f"{spec.agent_id}: allowed tool {tool_id!r} is not registered")
            if t.internal and spec.kind != "module":
                raise RegistryError(f"{spec.agent_id}: {tool_id} is internal to the runtime")
            if t.permission_policy.approver_roles == "signoff" and not spec.required_signoff_role:
                raise RegistryError(f"{spec.agent_id}: {tool_id} is approved by the signers, and there are none")
        if spec.kind == "runtime_agent" and spec.deployment_status != "dev" and not spec.entrypoint:
            raise RegistryError(f"{spec.agent_id}: a runtime agent past dev needs its entrypoint")


@cache
def _load(agents_dir: Path = AGENTS_DIR, tools_dir: Path = TOOLS_DIR) -> Registries:
    tools = _read_dir(tools_dir, ToolSpec, "tool_id")
    agents = _read_dir(agents_dir, AgentSpec, "agent_id")
    _cross_check(agents, tools)
    return Registries(agents=agents, tools=tools)


_agent_overrides: dict[str, AgentSpec | None] = {}
_tool_overrides: dict[str, ToolSpec | None] = {}
_mode_override: list[str] = []


def reload() -> None:
    _load.cache_clear()


def agents() -> dict[str, AgentSpec]:
    out = dict(_load().agents)
    for name, value in _agent_overrides.items():
        if value is None:
            out.pop(name, None)
        else:
            out[name] = value
    return out


def agent(agent_id: str | None) -> AgentSpec | None:
    if not agent_id:
        return None
    if agent_id in _agent_overrides:
        return _agent_overrides[agent_id]
    return _load().agents.get(agent_id)


def require_agent(agent_id: str) -> AgentSpec:
    spec = agent(agent_id)
    if spec is None:
        raise UnregisteredAgent(f"Agent {agent_id!r} is not in the agent registry (config/agents); "
                                f"unregistered agents cannot be loaded")
    return spec


def tools() -> dict[str, ToolSpec]:
    out = dict(_load().tools)
    for name, value in _tool_overrides.items():
        if value is None:
            out.pop(name, None)
        else:
            out[name] = value
    return out


def tool(tool_id: str) -> ToolSpec | None:
    if tool_id in _tool_overrides:
        return _tool_overrides[tool_id]
    return _load().tools.get(tool_id)


# ---------- the release gate ----------


def app_mode() -> str:
    return _mode_override[-1] if _mode_override else settings.app_mode


def gate(spec: AgentSpec, mode: str | None = None) -> str | None:
    """Why the agent may not run in this mode (None: it may). Modules have no model and are not gated."""
    if not spec.is_agent or (mode or app_mode()) != "prod":
        return None
    if spec.eval_status.status != "passed":
        return (f"{spec.label} has not passed its evaluation (eval_status {spec.eval_status.status}); "
                f"prod mode refuses it")
    if spec.deployment_status != "prod_ready":
        return f"{spec.label} is {spec.deployment_status}, not prod_ready; prod mode refuses it"
    return None


def require_deployable(agent_id: str) -> AgentSpec:
    spec = require_agent(agent_id)
    if why := gate(spec):
        raise AgentNotDeployable(why)
    return spec


# ---------- tests ----------


def _agent_defaults(agent_id: str, value: dict) -> dict:
    kind = value.get("kind", "module")
    base: dict = {"agent_id": agent_id, "version": "0.0.1", "kind": kind, "owner": "tests", "purpose": "test entry",
                  "risk_tier": "ops", "deployment_status": "demo",
                  "eval_status": {"status": "not_applicable" if kind == "module" else "pending"}}
    if kind != "module":
        base["model_policy"] = {"tier": "fast", "max_tokens": 2000}
    return base | value


@contextlib.contextmanager
def temporary(agent_id: str, value: AgentSpec | dict | None) -> Iterator[AgentSpec | None]:
    """Tests: add, replace or (None) remove one agent entry; a dict may leave out fields that have test defaults."""
    previous = _agent_overrides.get(agent_id, ...)
    spec = AgentSpec.model_validate(_agent_defaults(agent_id, value)) if isinstance(value, dict) else value
    _agent_overrides[agent_id] = spec
    try:
        yield spec
    finally:
        if previous is ...:
            _agent_overrides.pop(agent_id, None)
        else:
            _agent_overrides[agent_id] = previous


@contextlib.contextmanager
def temporary_tool(tool_id: str, value: ToolSpec | dict | None) -> Iterator[ToolSpec | None]:
    previous = _tool_overrides.get(tool_id, ...)
    spec = ToolSpec.model_validate({"tool_id": tool_id} | value) if isinstance(value, dict) else value
    _tool_overrides[tool_id] = spec
    try:
        yield spec
    finally:
        if previous is ...:
            _tool_overrides.pop(tool_id, None)
        else:
            _tool_overrides[tool_id] = previous


@contextlib.contextmanager
def mode(value: str) -> Iterator[None]:
    """Tests: run as APP_MODE=value."""
    _mode_override.append(value)
    try:
        yield
    finally:
        _mode_override.pop()
