"""DocumentReference: discharge summary, patient instructions, handoff, medication reconciliation.
AI only creates docStatus=preliminary; final needs the signature service."""

from typing import Literal

from app.fhir.types.datatypes import Attachment, CodeableConcept, FhirModel, Reference, Resource


class DocumentContent(FhirModel):
    attachment: Attachment


class DocumentContext(FhirModel):
    encounter: list[Reference] | None = None


class DocumentReference(Resource):
    resourceType: Literal["DocumentReference"] = "DocumentReference"
    status: str  # current, superseded, entered-in-error
    docStatus: str | None = None  # preliminary, final, amended
    type: CodeableConcept
    subject: Reference
    date: str | None = None
    author: list[Reference] | None = None
    authenticator: Reference | None = None
    content: list[DocumentContent] | None = None
    context: DocumentContext | None = None
