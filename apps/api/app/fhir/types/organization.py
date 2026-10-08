"""Organization: the (fictional) hospital."""

from typing import Literal

from app.fhir.types.datatypes import CodeableConcept, Resource


class Organization(Resource):
    resourceType: Literal["Organization"] = "Organization"
    active: bool | None = None
    name: str | None = None
    type: list[CodeableConcept] | None = None
