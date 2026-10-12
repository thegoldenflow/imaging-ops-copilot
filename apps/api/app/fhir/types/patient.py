"""Patient: the primary index (MRN, synthetic health card, language)."""

from typing import Literal

from app.fhir.types.datatypes import (Address, CodeableConcept, ContactPoint, FhirModel, HumanName, Identifier,
                                      Reference, Resource)


class PatientCommunication(FhirModel):
    language: CodeableConcept
    preferred: bool | None = None


class PatientContact(FhirModel):
    relationship: list[CodeableConcept] | None = None
    name: HumanName | None = None
    telecom: list[ContactPoint] | None = None


class Patient(Resource):
    resourceType: Literal["Patient"] = "Patient"
    active: bool | None = None
    identifier: list[Identifier] | None = None
    name: list[HumanName] | None = None
    telecom: list[ContactPoint] | None = None
    gender: str | None = None
    birthDate: str | None = None
    address: list[Address] | None = None
    contact: list[PatientContact] | None = None
    communication: list[PatientCommunication] | None = None
    managingOrganization: Reference | None = None
