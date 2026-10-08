"""Condition: diagnoses and the problem list (SNOMED CT)."""

from typing import Literal

from app.fhir.types.datatypes import CodeableConcept, Reference, Resource


class Condition(Resource):
    resourceType: Literal["Condition"] = "Condition"
    clinicalStatus: CodeableConcept | None = None
    category: list[CodeableConcept] | None = None
    code: CodeableConcept
    subject: Reference
    encounter: Reference | None = None
    onsetDateTime: str | None = None
    recordedDate: str | None = None
    abatementDateTime: str | None = None
