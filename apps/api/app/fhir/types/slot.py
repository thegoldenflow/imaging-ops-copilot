"""Slot: a bookable time on a Schedule."""

from typing import Literal

from app.fhir.types.datatypes import CodeableConcept, Reference, Resource


class Slot(Resource):
    resourceType: Literal["Slot"] = "Slot"
    schedule: Reference
    status: str  # busy, free, busy-unavailable, busy-tentative
    serviceType: list[CodeableConcept] | None = None
    start: str
    end: str
