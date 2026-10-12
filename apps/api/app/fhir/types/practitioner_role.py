"""PractitionerRole: a practitioner's role, specialty and unit (used by RBAC)."""

from typing import Literal

from app.fhir.types.datatypes import CodeableConcept, Reference, Resource


class PractitionerRole(Resource):
    resourceType: Literal["PractitionerRole"] = "PractitionerRole"
    active: bool | None = None
    practitioner: Reference | None = None
    organization: Reference | None = None
    code: list[CodeableConcept] | None = None
    specialty: list[CodeableConcept] | None = None
    location: list[Reference] | None = None
