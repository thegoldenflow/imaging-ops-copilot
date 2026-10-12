"""Procedure: surgery and procedures (SNOMED CT)."""

from typing import Literal

from app.fhir.types.datatypes import CodeableConcept, FhirModel, Period, Reference, Resource


class Performer(FhirModel):
    actor: Reference
    function: CodeableConcept | None = None


class Procedure(Resource):
    resourceType: Literal["Procedure"] = "Procedure"
    status: str
    code: CodeableConcept
    subject: Reference
    encounter: Reference | None = None
    basedOn: list[Reference] | None = None
    performedPeriod: Period | None = None
    performer: list[Performer] | None = None
    location: Reference | None = None
