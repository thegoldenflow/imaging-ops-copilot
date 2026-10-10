"""The deterministic rule layer of the order review (spec 7.2): five rules over plain facts.

| Rule | Fires when | Severity |
| --- | --- | --- |
| duplicate_test | the same lab or imaging code was ordered within 72 h and already has a result | info ("already resulted", with the result) |
| renal_dosing | the latest eGFR is below 30 and the drug is on the renal list | the list's major / moderate, with the suggested dose |
| interaction | the drug pairs with another active order or home medication in the interaction table | the pair's major / moderate, with the mechanism |
| allergy | the drug or its therapeutic class matches an active AllergyIntolerance | blocking: a pharmacist must handle it |
| dose_bounds | the single, daily or weekly dose is outside the limits for the patient's age and weight | the table's severity above a limit, moderate below a minimum, with the range |

The tables are `apps/api/config/med_rules/*.yaml` (demo simplifications, each entry
with its source). Rules never call a model and never read the EHR: the service
builds the facts (facts.py) and the rules answer, so they work when the AI is down.
Only these rules can block; the AI commentary never does.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from functools import cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.med_safety.facts import Med, PatientFacts, ServiceFact

RULES_DIR = Path(__file__).resolve().parents[3] / "config" / "med_rules"
Severity = Literal["blocking", "major", "moderate", "minor", "info"]
SEVERITY_RANK: dict[str, int] = {"none": -1, "info": 0, "minor": 1, "moderate": 2, "major": 3, "blocking": 4}
RULE_NAMES = {"duplicate_test": "Duplicate test", "renal_dosing": "Renal dosing", "interaction": "Drug interaction",
              "allergy": "Allergy", "dose_bounds": "Dose bounds"}


# ---------- the tables ----------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RenalDrug(_Strict):
    rxcui: str
    name: str
    severity: Literal["major", "moderate"]
    suggested: str
    source: str = Field(min_length=10)


class RenalTable(_Strict):
    threshold_egfr: float
    drugs: list[RenalDrug]


class PairSide(_Strict):
    rxcui: str | list[str] | None = None
    drug_class: str | None = Field(default=None, alias="class")
    name: str

    @model_validator(mode="after")
    def _one(self) -> PairSide:
        if (self.rxcui is None) == (self.drug_class is None):
            raise ValueError(f"{self.name}: a side is an rxcui or a class")
        return self

    def matches(self, med: Med) -> bool:
        if self.drug_class is not None:
            return med.drug_class == self.drug_class
        codes = self.rxcui if isinstance(self.rxcui, list) else [self.rxcui]
        return med.rxcui in codes


class InteractionPair(_Strict):
    id: str
    a: PairSide
    b: PairSide
    severity: Literal["major", "moderate"]
    mechanism: str = Field(min_length=10)
    source: str = Field(min_length=10)


class InteractionTable(_Strict):
    pairs: list[InteractionPair]


class Limits(_Strict):
    min_dose: float | None = None
    max_dose: float | None = None
    max_daily: float | None = None
    max_weekly: float | None = None


class OlderLimits(_Strict):
    age_from: int
    max_dose: float | None = None
    max_daily: float | None = None


class WeightLimits(_Strict):
    below_kg: float
    max_dose: float | None = None
    max_daily: float | None = None
    max_dose_per_kg: float | None = None
    max_daily_per_kg: float | None = None


class PerKg(_Strict):
    min_dose_per_kg: float | None = None
    max_dose_per_kg: float | None = None


class DoseBound(_Strict):
    rxcui: str
    name: str
    unit: str
    adult: Limits = Field(default_factory=Limits)
    older: OlderLimits | None = None
    low_weight: WeightLimits | None = None
    per_kg: PerKg | None = None
    severity: Literal["major", "moderate"]
    source: str = Field(min_length=10)


class DoseTable(_Strict):
    drugs: list[DoseBound]


class DuplicateTest(_Strict):
    window_hours: int
    categories: list[str]
    source: str = Field(min_length=10)


class DuplicateTherapy(_Strict):
    classes: list[str]
    source: str = Field(min_length=10)


class MedRecSettings(_Strict):
    duplicate_test: DuplicateTest
    duplicate_therapy: DuplicateTherapy
    high_risk_classes: list[str]
    high_risk_source: str = Field(min_length=10)
    hospital_use_routes: list[str]


@dataclass(frozen=True)
class Tables:
    renal_threshold: float
    renal: dict[str, RenalDrug]
    pairs: tuple[InteractionPair, ...]
    dose: dict[str, DoseBound]
    medrec: MedRecSettings


def _yaml(folder: Path, name: str) -> dict:
    return yaml.safe_load((folder / name).read_text(encoding="utf-8"))


@cache
def load_tables(folder: Path = RULES_DIR) -> Tables:
    """The rule tables, validated (a malformed or unsourced entry fails at load, never at review time)."""
    renal = RenalTable.model_validate(_yaml(folder, "renal_drugs.yaml"))
    pairs = InteractionTable.model_validate(_yaml(folder, "interactions.yaml"))
    dose = DoseTable.model_validate(_yaml(folder, "dose_bounds.yaml"))
    medrec = MedRecSettings.model_validate(_yaml(folder, "medrec.yaml"))
    return Tables(renal_threshold=renal.threshold_egfr, renal={d.rxcui: d for d in renal.drugs},
                  pairs=tuple(pairs.pairs), dose={d.rxcui: d for d in dose.drugs}, medrec=medrec)


# ---------- findings ----------


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: str
    title: str
    detail: str
    recommendation: str
    evidence_refs: tuple[str, ...]
    entry: str  # the table entry that fired, e.g. "interactions:warfarin-nsaid"
    source: str  # the entry's source annotation

    @property
    def blocking(self) -> bool:
        return self.severity == "blocking"

    def as_dict(self) -> dict:
        return {**asdict(self), "evidence_refs": list(self.evidence_refs), "blocking": self.blocking,
                "rule_name": RULE_NAMES[self.rule], "origin": "rule"}


def most_severe(severities: Iterable[str]) -> str:
    return max(severities, key=lambda s: SEVERITY_RANK[s], default="none")


def _num(value: float) -> str:
    return f"{value:g}"


def _ago(earlier: datetime | None, later: datetime | None) -> str:
    if earlier is None or later is None:
        return "earlier"
    seconds = (later - earlier).total_seconds()
    return f"{seconds / 3600:.0f} h before" if seconds >= 3600 else f"{max(0, int(seconds // 60))} min before"


# ---------- 1. duplicate test ----------


def duplicate_test(order: ServiceFact, facts: PatientFacts, tables: Tables) -> list[Finding]:
    """A lab or imaging order whose code already has a result from an order in the last 72 hours."""
    settings = tables.medrec.duplicate_test
    if order.category not in settings.categories or order.authored is None:
        return []
    since = order.authored - timedelta(hours=settings.window_hours)
    priors = [s for s in facts.services
              if s.ref != order.ref and s.code == order.code and s.result_ref and s.authored is not None
              and since <= s.authored <= order.authored and (s.result_at is None or s.result_at <= order.authored)]
    if not priors:
        return []
    prior = max(priors, key=lambda s: s.authored)
    resulted = f", resulted {_ago(prior.result_at, order.authored)} this order" if prior.result_at else ""
    return [Finding(
        rule="duplicate_test", severity="info", title=f"Duplicate test: {order.display or order.code} already resulted",
        detail=(f"The same test was ordered {_ago(prior.authored, order.authored)} this order{resulted} "
                f"(within {settings.window_hours} h)."),
        recommendation="Look at the result first; cancel the repeat unless the clinical picture has changed.",
        evidence_refs=(prior.ref, prior.result_ref), entry=f"medrec:duplicate_test:{settings.window_hours}h",
        source=settings.source)]


# ---------- 2. renal dosing ----------


def renal_dosing(order: Med, facts: PatientFacts, tables: Tables) -> list[Finding]:
    """The latest eGFR is below the threshold and the drug is on the renal list."""
    entry = tables.renal.get(order.rxcui)
    egfr = facts.egfr
    if entry is None or egfr is None or egfr.value >= tables.renal_threshold:
        return []
    when = f" on {egfr.at:%Y-%m-%d}" if egfr.at else ""
    return [Finding(
        rule="renal_dosing", severity=entry.severity, title=f"Renal dosing: {order.name} with eGFR {_num(egfr.value)}",
        detail=(f"Latest eGFR {_num(egfr.value)} {egfr.unit or ''}{when} is below {_num(tables.renal_threshold)}; "
                f"{order.name} needs a renal adjustment."),
        recommendation=entry.suggested, evidence_refs=(egfr.ref,), entry=f"renal_drugs:{entry.rxcui}",
        source=entry.source)]


# ---------- 3. interactions ----------


def interactions(order: Med, facts: PatientFacts, tables: Tables) -> list[Finding]:
    """Pairs of the new order with the patient's other active orders and home medications (either way round).
    One finding per pair and partner ingredient; a partner both ordered and on the home list cites both."""
    found: dict[tuple[str, str], tuple[InteractionPair, list[Med]]] = {}
    for other in facts.meds:
        if other.ref == order.ref or other.rxcui == order.rxcui:
            continue
        for pair in tables.pairs:
            if (pair.a.matches(order) and pair.b.matches(other)) or (pair.b.matches(order) and pair.a.matches(other)):
                found.setdefault((pair.id, other.rxcui), (pair, []))[1].append(other)
    out = []
    for (pair_id, _), (pair, partners) in found.items():
        partners.sort(key=lambda m: m.source != "order")  # an active order first, then the home list
        where = " and ".join(sorted({"an active order" if m.source != "home" else "the home medication list"
                                     for m in partners}))
        partner = partners[0]
        advice = ("Avoid the combination: agree an alternative with the prescriber, or document why both are needed "
                  "and how they will be monitored." if pair.severity == "major" else
                  "Monitor (and adjust if needed); document the plan.")
        out.append(Finding(
            rule="interaction", severity=pair.severity,
            title=f"Interaction ({pair.severity}): {order.name} + {partner.name}",
            detail=f"{pair.mechanism} {partner.name.capitalize()} is on {where}.", recommendation=advice,
            evidence_refs=tuple(m.ref for m in partners), entry=f"interactions:{pair_id}", source=pair.source))
    return out


# ---------- 4. allergy ----------


def allergy(order: Med, facts: PatientFacts, tables: Tables) -> list[Finding]:
    """The drug itself (RxNorm code) or its therapeutic class matches one of the patient's active allergies."""
    out = []
    for a in facts.allergies:
        by_code = a.code == order.rxcui
        by_class = bool(a.drug_class) and a.drug_class == order.drug_class
        if not (by_code or by_class):
            continue
        what = "the same drug" if by_code else f"the {a.drug_class} class"
        reaction = f"; reaction: {a.reaction}" if a.reaction else ""
        out.append(Finding(
            rule="allergy", severity="blocking", title=f"Allergy: {order.name} (allergic to {a.display})",
            detail=(f"The patient is allergic to {a.display} ({what}, criticality {a.criticality or 'unknown'}"
                    f"{reaction})."),
            recommendation=("Blocking: a pharmacist must handle this order with the prescriber (an alternative, or a "
                            "documented, tolerated previous use)."),
            evidence_refs=(a.ref,), entry=f"allergy:{'code' if by_code else 'class'}",
            source="The patient's AllergyIntolerance record (drug and drug-class match)"))
    return out


# ---------- 5. dose bounds ----------


@dataclass(frozen=True)
class Bounds:
    min_dose: float | None
    max_dose: float | None
    max_daily: float | None
    max_weekly: float | None
    basis: str


def _tighter(current: float | None, limit: float | None) -> float | None:
    if limit is None:
        return current
    return limit if current is None else min(current, limit)


def bounds_for(entry: DoseBound, age: int | None, weight: float | None) -> Bounds:
    """The limits that apply to this patient: the adult limits, tightened by age and weight."""
    min_dose, max_dose = entry.adult.min_dose, entry.adult.max_dose
    max_daily, max_weekly = entry.adult.max_daily, entry.adult.max_weekly
    basis = ["adults"]
    if entry.older and age is not None and age >= entry.older.age_from:
        max_dose = _tighter(max_dose, entry.older.max_dose)
        max_daily = _tighter(max_daily, entry.older.max_daily)
        basis.append(f"age {entry.older.age_from} or over ({age})")
    low = entry.low_weight
    if low and weight is not None and weight < low.below_kg:
        max_dose = _tighter(max_dose, low.max_dose)
        max_daily = _tighter(max_daily, low.max_daily)
        if low.max_dose_per_kg is not None:
            max_dose = _tighter(max_dose, round(low.max_dose_per_kg * weight, 1))
        if low.max_daily_per_kg is not None:
            max_daily = _tighter(max_daily, round(low.max_daily_per_kg * weight, 1))
        basis.append(f"under {_num(low.below_kg)} kg ({_num(weight)} kg)")
    if entry.per_kg and weight is not None:
        if entry.per_kg.min_dose_per_kg is not None:
            per_kg_min = round(entry.per_kg.min_dose_per_kg * weight, 1)
            min_dose = per_kg_min if min_dose is None else max(min_dose, per_kg_min)
        if entry.per_kg.max_dose_per_kg is not None:
            max_dose = _tighter(max_dose, round(entry.per_kg.max_dose_per_kg * weight, 1))
        basis.append(f"weight {_num(weight)} kg")
    return Bounds(min_dose, max_dose, max_daily, max_weekly, ", ".join(basis))


def range_text(b: Bounds, unit: str) -> str:
    parts = []
    if b.min_dose is not None and b.max_dose is not None:
        parts.append(f"{_num(b.min_dose)}-{_num(b.max_dose)} {unit} per dose")
    elif b.max_dose is not None:
        parts.append(f"up to {_num(b.max_dose)} {unit} per dose")
    elif b.min_dose is not None:
        parts.append(f"at least {_num(b.min_dose)} {unit} per dose")
    if b.max_daily is not None:
        parts.append(f"up to {_num(b.max_daily)} {unit} a day")
    if b.max_weekly is not None:
        parts.append(f"up to {_num(b.max_weekly)} {unit} a week")
    return ", ".join(parts)


def dose_bounds(order: Med, facts: PatientFacts, tables: Tables) -> list[Finding]:
    """The order's dose against the limits for the patient's age and weight."""
    entry = tables.dose.get(order.rxcui)
    if entry is None or order.dose is None or order.unit != entry.unit:
        return []
    weight = facts.weight.value if facts.weight else None
    b = bounds_for(entry, facts.age, weight)
    problems: list[tuple[str, str]] = []  # (severity, text)
    daily = order.daily
    if b.max_dose is not None and order.dose > b.max_dose:
        problems.append((entry.severity, f"{_num(order.dose)} {entry.unit} per dose is above {_num(b.max_dose)}"))
    if b.min_dose is not None and order.dose < b.min_dose:
        problems.append(("moderate", f"{_num(order.dose)} {entry.unit} per dose is below {_num(b.min_dose)}"))
    if b.max_daily is not None and daily is not None and daily > b.max_daily:
        problems.append((entry.severity, f"{_num(round(daily, 2))} {entry.unit} a day is above {_num(b.max_daily)}"))
    if b.max_weekly is not None and daily is not None and daily * 7 > b.max_weekly:
        problems.append((entry.severity,
                         f"{_num(round(daily * 7, 2))} {entry.unit} a week is above {_num(b.max_weekly)}"))
    if not problems:
        return []
    evidence = tuple(r for r in (facts.weight.ref if facts.weight and "kg" in b.basis else None,
                                 facts.patient_ref if "age" in b.basis else None) if r) or (order.ref,)
    allowed = range_text(b, entry.unit)
    return [Finding(
        rule="dose_bounds", severity=most_severe(s for s, _ in problems),
        title=f"Dose bounds: {order.name} {order.text or _num(order.dose) + ' ' + entry.unit}".strip(),
        detail=f"{'; '.join(t for _, t in problems)} (limits for {b.basis}).",
        recommendation=f"Keep within {allowed}.", evidence_refs=evidence, entry=f"dose_bounds:{entry.rxcui}",
        source=entry.source)]


# ---------- all rules ----------

MEDICATION_RULES = (allergy, interactions, renal_dosing, dose_bounds)


def evaluate(order: Med | ServiceFact, facts: PatientFacts, tables: Tables | None = None) -> list[Finding]:
    """Every rule that applies to the order, most severe first."""
    tables = tables or load_tables()
    if isinstance(order, ServiceFact):
        found = duplicate_test(order, facts, tables)
    else:
        found = [f for rule in MEDICATION_RULES for f in rule(order, facts, tables)]
    return sorted(found, key=lambda f: -SEVERITY_RANK[f.severity])
