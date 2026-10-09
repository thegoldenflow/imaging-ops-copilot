"""Safety-tier registry (spec 6.3): `apps/api/config/modules.registry.json`.

Every module that writes to the hospital EHR declares its risk tier, the roles
that sign its output, the resources (and statuses) it may write and the consents
it depends on. `FhirGateway` refuses writes from a module without an entry, or
outside its `writes_allowed`; the signing service reads `required_signoff_role`
(with `cosign`, every listed role signs; otherwise any one of them); the web app
renders sign buttons from `GET /api/hospital/registry`.

Kept deliberately small: WP4b (6.4) replaces it with the agent and tool
registries and carries tier, signoff roles and consents over unchanged.
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Iterator
from functools import cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

REGISTRY_PATH = Path(os.environ.get("MODULES_REGISTRY", Path(__file__).resolve().parents[2] / "config" /
                                    "modules.registry.json"))
CONSENT_CATEGORIES = ("ai_processing", "followup_call", "sms")
SIGNOFF_ROLES = ("physician", "nurse", "pharmacist", "clerk", "operations_manager")


class ModuleEntry(BaseModel):
    tier: Literal["ops", "documentation", "clinical_ds"]
    purpose: str = ""
    required_signoff_role: list[Literal["physician", "nurse", "pharmacist", "clerk", "operations_manager"]]
    cosign: bool = False  # every listed role signs (medication reconciliation); otherwise any one of them
    writes_allowed: dict[str, list[str]] = Field(default_factory=dict)  # resource type -> statuses ("*" = any)
    consent_required: list[Literal["ai_processing", "followup_call", "sms"]] = Field(default_factory=list)

    def allows(self, resource_type: str, status: str | None) -> bool:
        statuses = self.writes_allowed.get(resource_type)
        if statuses is None:
            return False
        return "*" in statuses or status in statuses


class Registry(BaseModel):
    version: int
    description: str = ""
    modules: dict[str, ModuleEntry]


_overrides: dict[str, ModuleEntry | None] = {}


@cache
def _load(path: Path = REGISTRY_PATH) -> Registry:
    return Registry.model_validate(json.loads(path.read_text(encoding="utf-8")))


def registry() -> Registry:
    return _load()


def entry(module: str) -> ModuleEntry | None:
    if module in _overrides:
        return _overrides[module]
    return registry().modules.get(module)


def modules() -> dict[str, ModuleEntry]:
    out = dict(registry().modules)
    for name, value in _overrides.items():
        if value is None:
            out.pop(name, None)
        else:
            out[name] = value
    return out


@contextlib.contextmanager
def temporary(module: str, value: ModuleEntry | dict | None) -> Iterator[None]:
    """Tests: add, replace or (None) remove one entry for the duration of the block."""
    previous = _overrides.get(module, ...)
    _overrides[module] = ModuleEntry.model_validate(value) if isinstance(value, dict) else value
    try:
        yield
    finally:
        if previous is ...:
            _overrides.pop(module, None)
        else:
            _overrides[module] = previous
