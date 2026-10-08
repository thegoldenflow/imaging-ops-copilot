"""DiagnosticReport: lab and imaging reports."""

from typing import Literal

from app.fhir.types.datatypes import Attachment, CodeableConcept, Reference, Resource


class DiagnosticReport(Resource):
    resourceType: Literal["DiagnosticReport"] = "DiagnosticReport"
    status: str
    category: list[CodeableConcept] | None = None
    code: CodeableConcept
    subject: Reference
    encounter: Reference | None = None
    basedOn: list[Reference] | None = None
    effectiveDateTime: str | None = None
    issued: str | None = None
    result: list[Reference] | None = None
    conclusion: str | None = None
    presentedForm: list[Attachment] | None = None
