"""Location: Unit -> Room -> Bed (physicalType wa / ro / bd, operationalStatus O / U / C / K)."""

from typing import Literal

from app.fhir.types.datatypes import CodeableConcept, Coding, Reference, Resource


class Location(Resource):
    resourceType: Literal["Location"] = "Location"
    status: str | None = None
    operationalStatus: Coding | None = None
    name: str | None = None
    mode: str | None = None
    physicalType: CodeableConcept | None = None
    partOf: Reference | None = None
    managingOrganization: Reference | None = None
