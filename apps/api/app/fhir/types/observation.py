"""Observation: vital signs, labs and scores (LOINC)."""

from typing import Literal

from app.fhir.types.datatypes import CodeableConcept, FhirModel, Quantity, Reference, Resource


class ObservationComponent(FhirModel):
    code: CodeableConcept
    valueQuantity: Quantity | None = None


class Observation(Resource):
    resourceType: Literal["Observation"] = "Observation"
    status: str
    category: list[CodeableConcept] | None = None
    code: CodeableConcept
    subject: Reference
    encounter: Reference | None = None
    effectiveDateTime: str | None = None
    valueQuantity: Quantity | None = None
    valueInteger: int | None = None
    valueString: str | None = None
    valueCodeableConcept: CodeableConcept | None = None
    interpretation: list[CodeableConcept] | None = None
    component: list[ObservationComponent] | None = None
