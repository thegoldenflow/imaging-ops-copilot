"""MedicationStatement: home medications (stand-in for the provincial drug history)."""

from typing import Literal

from app.fhir.types.datatypes import CodeableConcept, Dosage, Period, Reference, Resource


class MedicationStatement(Resource):
    resourceType: Literal["MedicationStatement"] = "MedicationStatement"
    status: str
    medicationCodeableConcept: CodeableConcept
    subject: Reference
    context: Reference | None = None
    effectivePeriod: Period | None = None
    dateAsserted: str | None = None
    informationSource: Reference | None = None
    reasonCode: list[CodeableConcept] | None = None
    dosage: list[Dosage] | None = None
