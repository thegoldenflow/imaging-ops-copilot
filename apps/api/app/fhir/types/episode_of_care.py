"""EpisodeOfCare: care across encounters (e.g. a fracture from the ED to follow-up)."""

from typing import Any, Literal

from app.fhir.types.datatypes import Period, Reference, Resource


class EpisodeOfCare(Resource):
    resourceType: Literal["EpisodeOfCare"] = "EpisodeOfCare"
    status: str
    patient: Reference
    period: Period | None = None
    diagnosis: list[dict[str, Any]] | None = None
    managingOrganization: Reference | None = None
    careManager: Reference | None = None
