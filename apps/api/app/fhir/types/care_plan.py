"""CarePlan: discharge and follow-up plans."""

from typing import Any, Literal

from app.fhir.types.datatypes import CodeableConcept, Period, Reference, Resource


class CarePlan(Resource):
    resourceType: Literal["CarePlan"] = "CarePlan"
    status: str
    intent: str
    category: list[CodeableConcept] | None = None
    title: str | None = None
    subject: Reference
    encounter: Reference | None = None
    period: Period | None = None
    activity: list[dict[str, Any]] | None = None
