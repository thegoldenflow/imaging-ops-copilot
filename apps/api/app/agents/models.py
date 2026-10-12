"""Model registry (spec 6.6): `config/models.yaml`.

Every model an agent may use is registered with its provider, tier, approved tasks, the risk tiers it may serve,
a cost ceiling per case and what data it may receive. Model ids stay in the environment (CLAUDE.md): an entry
names the setting that holds its id (`model_id_env`), read from the settings, so the registry approves models
without hard-coding them.

`resolve(spec, mode)` gives the entry an agent's model_policy points at for the active provider (`tier`, or a pinned
`model`); the mock provider stands in for Anthropic. `problems(spec)` lists why a policy may not be used; the agent
registry raises them at load, so an agent can reference registered models only.
"""

from __future__ import annotations

import os
from functools import cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

CONFIG = Path(__file__).resolve().parents[2] / "config"
MODELS_FILE = Path(os.environ.get("MODELS_REGISTRY", CONFIG / "models.yaml"))

PROVIDERS = ("anthropic", "gemini")
# The settings that hold model ids (app/core/config.py) by their environment variable.
SETTINGS_BY_ENV = {"CLAUDE_MODEL_REASONING": "model_reasoning", "CLAUDE_MODEL_FAST": "model_fast",
                   "CLAUDE_MODEL_VOICE": "model_voice", "GEMINI_MODEL_REASONING": "gemini_model_reasoning",
                   "GEMINI_MODEL_FAST": "gemini_model_fast", "GEMINI_MODEL_VOICE": "gemini_model_voice"}
CLINICAL_TIERS = ("documentation", "clinical_ds")


class ModelRegistryError(ValueError):
    pass


class ModelEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = ""
    provider: Literal["anthropic", "gemini"]
    tier: Literal["reasoning", "fast", "voice"]
    model_id_env: str
    approved_tasks: list[str] = Field(min_length=1)  # agent ids, or "*" for every registered agent
    risk_tiers_allowed: list[Literal["ops", "documentation", "clinical_ds"]] = Field(min_length=1)
    max_cost_per_case: float = Field(gt=0)
    data_handling: Literal["deidentified_clinical_text", "no_clinical_text"]
    note: str = ""

    @property
    def model_id(self) -> str:
        """The id from the environment (through the settings where one exists)."""
        from app.core.config import settings

        return self.id_from(settings)

    def id_from(self, settings) -> str:
        attr = SETTINGS_BY_ENV.get(self.model_id_env)
        value = getattr(settings, attr) if attr else os.environ.get(self.model_id_env, "")
        if not value:
            raise ModelRegistryError(f"{self.name}: {self.model_id_env} is not set")
        return value

    def approves(self, agent_id: str) -> bool:
        return "*" in self.approved_tasks or agent_id in self.approved_tasks

    def public(self) -> dict:
        return {**self.model_dump(), "model_id": self.model_id}


@cache
def _load(path: Path = MODELS_FILE) -> dict[str, ModelEntry]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        out = {name: ModelEntry.model_validate({**value, "name": name})
               for name, value in (data.get("models") or {}).items()}
    except (OSError, yaml.YAMLError, ValidationError) as e:
        raise ModelRegistryError(f"{path.name}: {e}") from e
    for provider in PROVIDERS:
        for tier in ("reasoning", "fast", "voice"):
            if sum(1 for m in out.values() if m.provider == provider and m.tier == tier) > 1:
                raise ModelRegistryError(f"{path.name}: two {provider} models for the {tier} tier")
    return out


def reload() -> None:
    _load.cache_clear()


def models() -> dict[str, ModelEntry]:
    return dict(_load())


def provider_of(mode: str) -> str:
    """The registry provider behind a gateway mode (the mock stands in for Anthropic)."""
    return "gemini" if mode == "gemini" else "anthropic"


def resolve(spec, mode: str) -> ModelEntry | None:
    """The registered model an agent's model_policy names for this provider mode (None: no such entry)."""
    policy = spec.model_policy
    if policy is None:
        return None
    registry = _load()
    if policy.model:
        entry = registry.get(policy.model)
        return entry if entry is not None and entry.provider == provider_of(mode) else None
    provider = provider_of(mode)
    return next((m for m in registry.values() if m.provider == provider and m.tier == policy.tier), None)


def problems(spec) -> list[str]:
    """Why an agent's model_policy may not be used: it must name a registered model for every provider, approved
    for the agent, for its risk tier and for the clinical text it reads."""
    if spec.model_policy is None:
        return []
    out = []
    registry = _load()
    if spec.model_policy.model and spec.model_policy.model not in registry:
        return [f"{spec.agent_id}: model {spec.model_policy.model!r} is not in config/models.yaml"]
    reads_clinical = spec.risk_tier in CLINICAL_TIERS or bool(spec.data_scope.resources)
    modes = [m for m in ("anthropic", "gemini")
             if not spec.model_policy.model or registry[spec.model_policy.model].provider == provider_of(m)]
    for mode in modes:
        entry = resolve(spec, mode)
        if entry is None:
            out.append(f"{spec.agent_id}: no {provider_of(mode)} model registered for the "
                       f"{spec.model_policy.tier} tier (config/models.yaml)")
            continue
        if not entry.approves(spec.agent_id):
            out.append(f"{spec.agent_id}: model {entry.name} is not approved for this agent (approved_tasks)")
        if spec.risk_tier not in entry.risk_tiers_allowed:
            out.append(f"{spec.agent_id}: model {entry.name} is not approved for the {spec.risk_tier} tier")
        if reads_clinical and entry.data_handling != "deidentified_clinical_text":
            out.append(f"{spec.agent_id}: model {entry.name} may not receive clinical text")
    return out


def by_model_id(model_id: str | None) -> ModelEntry | None:
    """The registry entry whose configured id is this one (traces record ids)."""
    if not model_id:
        return None
    model_id = model_id.removeprefix("mock:")
    for entry in _load().values():
        try:
            if entry.model_id == model_id:
                return entry
        except ModelRegistryError:
            continue
    return None
