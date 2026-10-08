"""Practitioner: staff."""

from typing import Literal

from app.fhir.types.datatypes import HumanName, Identifier, Resource


class Practitioner(Resource):
    resourceType: Literal["Practitioner"] = "Practitioner"
    active: bool | None = None
    identifier: list[Identifier] | None = None
    name: list[HumanName] | None = None
