"""Appointment: OR cases and clinic bookings (AI only ever creates status=proposed)."""

from typing import Literal

from app.fhir.types.datatypes import CodeableConcept, FhirModel, Reference, Resource


class AppointmentParticipant(FhirModel):
    actor: Reference | None = None
    status: str | None = None


class Appointment(Resource):
    resourceType: Literal["Appointment"] = "Appointment"
    status: str  # proposed, pending, booked, arrived, fulfilled, cancelled, noshow
    serviceType: list[CodeableConcept] | None = None
    description: str | None = None
    start: str | None = None
    end: str | None = None
    minutesDuration: int | None = None
    basedOn: list[Reference] | None = None
    participant: list[AppointmentParticipant] | None = None
