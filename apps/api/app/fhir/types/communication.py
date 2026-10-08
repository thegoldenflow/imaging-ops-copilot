"""Communication: follow-up calls, reminders and escalations."""

from typing import Any, Literal

from app.fhir.types.datatypes import CodeableConcept, Reference, Resource


class Communication(Resource):
    resourceType: Literal["Communication"] = "Communication"
    status: str
    category: list[CodeableConcept] | None = None
    subject: Reference | None = None
    encounter: Reference | None = None
    about: list[Reference] | None = None
    sent: str | None = None
    recipient: list[Reference] | None = None
    sender: Reference | None = None
    payload: list[dict[str, Any]] | None = None
