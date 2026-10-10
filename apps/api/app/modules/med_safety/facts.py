"""Plain facts for the medication rules, built from FHIR resources (spec 7.2).

The rule layer (rules.py, reconcile.py) never sees FHIR or the database: the
service reads the resources through FhirGateway and turns them into these frozen
records. Everything here is a pure function of its input.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from app.ehr import codes as C
from app.fhir.dt import parse

PER_DAY = {"d": 1.0, "h": 24.0, "min": 1440.0, "wk": 1 / 7, "mo": 1 / 30}


@dataclass(frozen=True)
class Med:
    """A medication: an order (MedicationRequest intent order), a discharge prescription (intent plan) or a home
    medication (MedicationStatement)."""

    ref: str
    rxcui: str
    name: str
    drug_class: str = ""
    dose: float | None = None
    unit: str | None = None
    per_day: float | None = None  # administrations a day (an as-needed order: the most it allows)
    route: str | None = None
    prn: bool = False
    text: str = ""
    source: str = "order"  # order, discharge, home
    status: str | None = None
    authored: datetime | None = None
    reason_codes: tuple[str, ...] = ()

    @property
    def daily(self) -> float | None:
        if self.dose is None or self.per_day is None:
            return None
        return self.dose * self.per_day

    def same_regimen(self, other: Med) -> bool:
        return self.dose == other.dose and self.unit == other.unit and self.per_day == other.per_day

    def line(self) -> dict:
        """The medication as a stored table line."""
        return {"ref": self.ref, "rxcui": self.rxcui, "name": self.name, "drug_class": self.drug_class,
                "text": self.text, "dose": self.dose, "unit": self.unit, "per_day": self.per_day,
                "route": self.route, "prn": self.prn}


@dataclass(frozen=True)
class AllergyFact:
    ref: str
    code: str
    display: str
    drug_class: str = ""
    criticality: str | None = None
    reaction: str | None = None


@dataclass(frozen=True)
class Measurement:
    ref: str
    value: float
    unit: str | None
    at: datetime | None


@dataclass(frozen=True)
class ServiceFact:
    """A lab, imaging or consult order and, once resulted, its result."""

    ref: str
    code: str
    display: str
    category: str
    status: str | None = None
    authored: datetime | None = None
    result_ref: str | None = None
    result_at: datetime | None = None


@dataclass(frozen=True)
class ConditionFact:
    ref: str
    code: str
    display: str
    category: str = ""
    active: bool = True


@dataclass(frozen=True)
class PatientFacts:
    patient_ref: str
    age: int | None = None
    sex: str | None = None
    weight: Measurement | None = None
    egfr: Measurement | None = None
    allergies: tuple[AllergyFact, ...] = ()
    meds: tuple[Med, ...] = ()  # the patient's other active orders and home medications
    services: tuple[ServiceFact, ...] = ()  # lab and imaging orders (duplicate test)
    conditions: tuple[ConditionFact, ...] = field(default_factory=tuple)


# ---------- from FHIR ----------


def _first(items):
    return items[0] if items else None


def _code(concept: dict | None, system: str | None = None) -> tuple[str, str]:
    """(code, display) of a CodeableConcept: the first coding (of `system` when given)."""
    concept = concept or {}
    codings = [c for c in concept.get("coding") or [] if system is None or c.get("system") == system]
    coding = _first(codings) or {}
    return coding.get("code") or "", coding.get("display") or concept.get("text") or ""


def ref_of(resource: dict) -> str:
    return f"{resource['resourceType']}/{resource['id']}"


def dosage_fields(dosage: dict | None) -> dict:
    """dose, unit, per_day, route, prn and text of a FHIR Dosage."""
    dosage = dosage or {}
    rate = _first(dosage.get("doseAndRate") or []) or {}
    quantity = rate.get("doseQuantity") or {}
    repeat = (dosage.get("timing") or {}).get("repeat") or {}
    per_day = None
    if repeat.get("frequency") is not None:
        period = repeat.get("period") or 1
        per_day = round(repeat["frequency"] / period * PER_DAY.get(repeat.get("periodUnit") or "d", 1.0), 4)
    return {"dose": quantity.get("value"), "unit": quantity.get("unit") or quantity.get("code"),
            "per_day": per_day, "route": (dosage.get("route") or {}).get("text"),
            "prn": bool(dosage.get("asNeededBoolean")), "text": dosage.get("text") or ""}


def _drug(concept: dict | None) -> tuple[str, str, str]:
    rxcui, display = _code(concept, C.RXNORM)
    if not rxcui:
        rxcui, display = _code(concept)
    known = C.MEDICATIONS.get(rxcui)
    return rxcui, (known[0] if known else display), (known[1] if known else "")


def med_from_request(resource: dict) -> Med:
    rxcui, name, drug_class = _drug(resource.get("medicationCodeableConcept"))
    intent = resource.get("intent")
    return Med(ref=ref_of(resource), rxcui=rxcui, name=name, drug_class=drug_class,
               source="discharge" if intent == "plan" else "order", status=resource.get("status"),
               authored=parse(resource.get("authoredOn")),
               reason_codes=tuple(c for c in (_code(r)[0] for r in resource.get("reasonCode") or []) if c),
               **dosage_fields(_first(resource.get("dosageInstruction") or [])))


def med_from_statement(resource: dict) -> Med:
    rxcui, name, drug_class = _drug(resource.get("medicationCodeableConcept"))
    period = resource.get("effectivePeriod") or {}
    return Med(ref=ref_of(resource), rxcui=rxcui, name=name, drug_class=drug_class, source="home",
               status=resource.get("status"), authored=parse(period.get("start")),
               reason_codes=tuple(c for c in (_code(r)[0] for r in resource.get("reasonCode") or []) if c),
               **dosage_fields(_first(resource.get("dosage") or [])))


def allergy_from(resource: dict) -> AllergyFact:
    code, display = _code(resource.get("code"))
    drug_class = next((e.get("valueCode") for e in resource.get("extension") or []
                       if e.get("url") == C.EXT + "drug-class"), "") or ""
    reaction = _first(resource.get("reaction") or []) or {}
    return AllergyFact(ref=ref_of(resource), code=code, display=display, drug_class=drug_class,
                       criticality=resource.get("criticality"), reaction=reaction.get("description"))


def allergy_active(resource: dict) -> bool:
    return _code(resource.get("clinicalStatus"))[0] in ("active", "")


def measurement(resource: dict) -> Measurement | None:
    quantity = resource.get("valueQuantity") or {}
    if quantity.get("value") is None:
        return None
    return Measurement(ref=ref_of(resource), value=float(quantity["value"]), unit=quantity.get("unit"),
                       at=parse(resource.get("effectiveDateTime")))


def latest(measurements: list[Measurement | None], before: datetime | None = None) -> Measurement | None:
    found = [m for m in measurements if m is not None and (before is None or m.at is None or m.at <= before)]
    return max(found, key=lambda m: m.at or datetime.min) if found else None


def service_from(resource: dict, result: tuple[str, datetime | None] | None = None) -> ServiceFact:
    code, display = _code(resource.get("code"))
    category = _code(_first(resource.get("category") or []))[0]
    return ServiceFact(ref=ref_of(resource), code=code, display=display, category=category,
                       status=resource.get("status"), authored=parse(resource.get("authoredOn")),
                       result_ref=result[0] if result else None, result_at=result[1] if result else None)


def condition_from(resource: dict) -> ConditionFact:
    code, display = _code(resource.get("code"))
    status = _code(resource.get("clinicalStatus"))[0]
    category = _code(_first(resource.get("category") or []))[0]
    return ConditionFact(ref=ref_of(resource), code=code, display=display, category=category,
                         active=status in ("active", "recurrence", "relapse", ""))


def age_on(birth_date: str | None, day: date) -> int | None:
    born = parse(birth_date)
    if born is None:
        return None
    return day.year - born.year - ((day.month, day.day) < (born.month, born.day))
