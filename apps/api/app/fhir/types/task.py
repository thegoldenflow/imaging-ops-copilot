"""Task: work items awaiting signature, review or action."""

from typing import Any, Literal

from pydantic import Field

from app.fhir.types.datatypes import CodeableConcept, Reference, Resource


class Task(Resource):
    resourceType: Literal["Task"] = "Task"
    status: str
    intent: str
    priority: str | None = None
    code: CodeableConcept | None = None
    description: str | None = None
    focus: Reference | None = None
    for_: Reference | None = Field(default=None, alias="for")
    encounter: Reference | None = None
    authoredOn: str | None = None
    lastModified: str | None = None
    performerType: list[CodeableConcept] | None = None
    owner: Reference | None = None
    restriction: dict[str, Any] | None = None
