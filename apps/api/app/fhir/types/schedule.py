"""Schedule: the planning horizon of an OR room or clinic."""

from typing import Literal

from app.fhir.types.datatypes import CodeableConcept, Period, Reference, Resource


class Schedule(Resource):
    resourceType: Literal["Schedule"] = "Schedule"
    active: bool | None = None
    serviceType: list[CodeableConcept] | None = None
    actor: list[Reference] | None = None
    planningHorizon: Period | None = None
