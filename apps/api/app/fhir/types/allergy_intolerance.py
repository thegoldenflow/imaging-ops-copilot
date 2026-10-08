"""AllergyIntolerance: allergies (the drug class is in extension `drug-class`)."""

from typing import Literal

from app.fhir.types.datatypes import CodeableConcept, FhirModel, Reference, Resource


class Reaction(FhirModel):
    description: str | None = None
    severity: str | None = None


class AllergyIntolerance(Resource):
    resourceType: Literal["AllergyIntolerance"] = "AllergyIntolerance"
    clinicalStatus: CodeableConcept | None = None
    category: list[str] | None = None
    criticality: str | None = None
    code: CodeableConcept
    patient: Reference
    encounter: Reference | None = None
    recordedDate: str | None = None
    reaction: list[Reaction] | None = None
