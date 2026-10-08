"""ServiceRequest: labs, imaging, consults, surgery and bed requests."""

from typing import Literal

from app.fhir.types.datatypes import Annotation, CodeableConcept, Identifier, Reference, Resource


class ServiceRequest(Resource):
    resourceType: Literal["ServiceRequest"] = "ServiceRequest"
    identifier: list[Identifier] | None = None
    requisition: Identifier | None = None  # the imaging requisition an exam was booked from
    status: str
    intent: str
    priority: str | None = None
    category: list[CodeableConcept] | None = None
    code: CodeableConcept | None = None
    orderDetail: list[CodeableConcept] | None = None  # the approved imaging protocol
    subject: Reference
    encounter: Reference | None = None
    authoredOn: str | None = None
    occurrenceDateTime: str | None = None
    requester: Reference | None = None
    locationReference: list[Reference] | None = None
    reasonCode: list[CodeableConcept] | None = None
    note: list[Annotation] | None = None
