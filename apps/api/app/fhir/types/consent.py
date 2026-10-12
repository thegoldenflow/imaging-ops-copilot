"""Consent: ai_processing, followup_call and sms (permit or deny)."""

from typing import Any, Literal

from app.fhir.types.datatypes import CodeableConcept, Reference, Resource


class Consent(Resource):
    resourceType: Literal["Consent"] = "Consent"
    status: str
    scope: CodeableConcept
    category: list[CodeableConcept]
    patient: Reference
    dateTime: str | None = None
    provision: dict[str, Any] | None = None
