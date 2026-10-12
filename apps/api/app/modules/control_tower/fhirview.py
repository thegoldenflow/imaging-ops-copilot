"""Small readers over FHIR resource dicts (the hospital generator's and simulator's shapes, docs/data-model.md)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.ehr import codes as C
from app.fhir.dt import parse, ref_id


def unit_of(location_id: str | None) -> str | None:
    """The unit of a bed or room: ids are unit `ORTH`, room `ORTH-05`, bed `ORTH-05-A` (docs/data-model.md)."""
    return location_id.split("-", 1)[0] if location_id else None


def ext(resource: dict | None, name: str) -> Any:
    """The value of extension `urn:demo-hospital:ext:<name>`."""
    for e in (resource or {}).get("extension") or []:
        if e.get("url") == C.EXT + name:
            return next((v for k, v in e.items() if k.startswith("value")), None)
    return None


def code(concept: dict | list | None) -> str | None:
    if isinstance(concept, list):
        concept = concept[0] if concept else None
    codings = (concept or {}).get("coding") or []
    return codings[0].get("code") if codings else None


def codes(concepts: list | dict | None) -> set[str]:
    items = concepts if isinstance(concepts, list) else [concepts] if concepts else []
    return {c.get("code") for cc in items if isinstance(cc, dict) for c in cc.get("coding") or []}


def start(resource: dict) -> datetime | None:
    return parse((resource.get("period") or {}).get("start"))


def end(resource: dict) -> datetime | None:
    return parse((resource.get("period") or {}).get("end"))


def status_since(encounter: dict, status: str) -> datetime | None:
    """When the encounter entered a status of its statusHistory (ED: arrived, triaged, in-progress)."""
    for item in encounter.get("statusHistory") or []:
        if item.get("status") == status:
            return parse((item.get("period") or {}).get("start"))
    return None


def current_location(encounter: dict) -> str | None:
    """The bed (or the unit, while waiting for a bed) the encounter is at now."""
    loc = next((loc for loc in reversed(encounter.get("location") or []) if loc.get("status") == "active"), None)
    return ref_id(loc["location"]) if loc else None


def patient_id(resource: dict) -> str | None:
    for key in ("subject", "patient", "for"):
        value = resource.get(key)
        if isinstance(value, dict) and (value.get("reference") or "").startswith("Patient/"):
            return ref_id(value)
    for participant in resource.get("participant") or []:
        actor = (participant.get("actor") or {}).get("reference") or ""
        if actor.startswith("Patient/"):
            return actor.split("/", 1)[1]
    return None


def encounter_id(resource: dict) -> str | None:
    enc = resource.get("encounter") or (resource.get("context") or {}).get("encounter")
    if isinstance(enc, list):
        enc = enc[0] if enc else None
    return ref_id(enc) if isinstance(enc, dict) else None


def participant(resource: dict, rtype: str) -> str | None:
    for p in resource.get("participant") or []:
        actor = (p.get("actor") or p.get("individual") or {}).get("reference") or ""
        if actor.startswith(rtype + "/"):
            return actor.split("/", 1)[1]
    return None


def age(patient: dict | None, at: datetime) -> int | None:
    born = parse((patient or {}).get("birthDate"))
    if born is None:
        return None
    return at.year - born.year - ((at.month, at.day) < (born.month, born.day))


def mrn(patient: dict | None) -> str | None:
    return next((i.get("value") for i in (patient or {}).get("identifier") or [] if i.get("system") == C.MRN_SYSTEM),
                None)


def vitals(observation: dict | None) -> dict[str, float]:
    """LOINC code -> value of a vital-sign panel's components."""
    out: dict[str, float] = {}
    for comp in (observation or {}).get("component") or []:
        value = (comp.get("valueQuantity") or {}).get("value")
        c = code(comp.get("code"))
        if c and value is not None:
            out[c] = float(value)
    return out


def value(observation: dict | None) -> float | None:
    obs = observation or {}
    if "valueInteger" in obs:
        return float(obs["valueInteger"])
    q = (obs.get("valueQuantity") or {}).get("value")
    return float(q) if q is not None else None


def category(resource: dict) -> str | None:
    return code(resource.get("category"))
