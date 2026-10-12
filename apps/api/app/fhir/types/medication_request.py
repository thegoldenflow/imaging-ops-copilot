"""MedicationRequest: inpatient and discharge medication orders (RxNorm)."""

from typing import Literal

from app.fhir.types.datatypes import Annotation, CodeableConcept, Dosage, Reference, Resource


class MedicationRequest(Resource):
    resourceType: Literal["MedicationRequest"] = "MedicationRequest"
    status: str
    intent: str  # order, plan (discharge prescription)
    category: list[CodeableConcept] | None = None
    medicationCodeableConcept: CodeableConcept
    subject: Reference
    encounter: Reference | None = None
    authoredOn: str | None = None
    requester: Reference | None = None
    reasonCode: list[CodeableConcept] | None = None
    dosageInstruction: list[Dosage] | None = None
    note: list[Annotation] | None = None
