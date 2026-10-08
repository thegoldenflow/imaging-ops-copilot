"""Encounter: a visit or stay (class EMER / IMP / AMB) with its bed history."""

from typing import Literal

from pydantic import Field

from app.fhir.types.datatypes import CodeableConcept, Coding, FhirModel, Period, Reference, Resource


class StatusHistory(FhirModel):
    status: str
    period: Period


class EncounterLocation(FhirModel):
    location: Reference
    status: str | None = None  # planned, active, reserved, completed
    period: Period | None = None


class Hospitalization(FhirModel):
    admitSource: CodeableConcept | None = None
    dischargeDisposition: CodeableConcept | None = None


class Participant(FhirModel):
    individual: Reference | None = None


class Encounter(Resource):
    resourceType: Literal["Encounter"] = "Encounter"
    status: str
    class_: Coding = Field(alias="class")
    statusHistory: list[StatusHistory] | None = None
    serviceType: CodeableConcept | None = None
    subject: Reference
    period: Period | None = None
    reasonCode: list[CodeableConcept] | None = None
    basedOn: list[Reference] | None = None
    participant: list[Participant] | None = None
    hospitalization: Hospitalization | None = None
    location: list[EncounterLocation] | None = None
    serviceProvider: Reference | None = None
    partOf: Reference | None = None
