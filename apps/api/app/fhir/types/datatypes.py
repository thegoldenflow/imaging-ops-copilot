"""FHIR R4 data types used by the hospital resources (only the fields this project uses).

Models accept extra fields (`extra="allow"`), so resources read back from a full
FHIR server (HAPI adds `meta`, `text`, ...) validate and round-trip unchanged.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class FhirModel(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    def to_fhir(self) -> dict:
        return self.model_dump(by_alias=True, exclude_none=True, mode="json")


class Coding(FhirModel):
    system: str | None = None
    code: str | None = None
    display: str | None = None


class CodeableConcept(FhirModel):
    coding: list[Coding] | None = None
    text: str | None = None


class Reference(FhirModel):
    reference: str | None = None
    display: str | None = None


class Period(FhirModel):
    start: str | None = None
    end: str | None = None


class Extension(FhirModel):
    url: str
    valueCode: str | None = None
    valueString: str | None = None
    valueBoolean: bool | None = None
    valueInteger: int | None = None
    valueDecimal: float | None = None
    valueDateTime: str | None = None
    valueReference: Reference | None = None
    valueAddress: dict[str, Any] | None = None
    extension: list[Extension] | None = None


class Identifier(FhirModel):
    use: str | None = None
    type: CodeableConcept | None = None
    system: str | None = None
    value: str | None = None
    extension: list[Extension] | None = None


class HumanName(FhirModel):
    use: str | None = None
    text: str | None = None
    family: str | None = None
    given: list[str] | None = None
    prefix: list[str] | None = None
    extension: list[Extension] | None = None


class ContactPoint(FhirModel):
    system: str | None = None
    value: str | None = None
    use: str | None = None


class Address(FhirModel):
    use: str | None = None
    text: str | None = None
    line: list[str] | None = None
    city: str | None = None
    state: str | None = None
    postalCode: str | None = None
    country: str | None = None


class Quantity(FhirModel):
    value: int | float | None = None
    unit: str | None = None
    system: str | None = None
    code: str | None = None


class Annotation(FhirModel):
    text: str


class Attachment(FhirModel):
    contentType: str | None = None
    language: str | None = None
    data: str | None = None  # base64
    title: str | None = None


class Timing(FhirModel):
    repeat: dict[str, Any] | None = None


class DoseAndRate(FhirModel):
    doseQuantity: Quantity | None = None


class Dosage(FhirModel):
    text: str | None = None
    timing: Timing | None = None
    route: CodeableConcept | None = None
    asNeededBoolean: bool | None = None
    doseAndRate: list[DoseAndRate] | None = None


class Meta(FhirModel):
    versionId: str | None = None
    lastUpdated: str | None = None


class Resource(FhirModel):
    """Base for every resource: id, meta and extensions."""

    resourceType: str
    id: str | None = None
    meta: Meta | None = None
    extension: list[Extension] | None = None
