"""Provenance: the source chain of an AI output and the human action on it."""

from typing import Literal

from app.fhir.types.datatypes import CodeableConcept, FhirModel, Reference, Resource


class ProvenanceAgent(FhirModel):
    type: CodeableConcept | None = None
    who: Reference


class ProvenanceEntity(FhirModel):
    role: str  # derivation, source, ...
    what: Reference


class Provenance(Resource):
    resourceType: Literal["Provenance"] = "Provenance"
    target: list[Reference]
    recorded: str
    agent: list[ProvenanceAgent]
    entity: list[ProvenanceEntity] | None = None
