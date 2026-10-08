"""Flag: safety flags (ALC, fall risk, isolation, NEWS2)."""

from typing import Literal

from app.fhir.types.datatypes import CodeableConcept, Period, Reference, Resource


class Flag(Resource):
    resourceType: Literal["Flag"] = "Flag"
    status: str
    category: list[CodeableConcept] | None = None
    code: CodeableConcept
    subject: Reference
    period: Period | None = None
    encounter: Reference | None = None
