"""The discharge medication table (spec 7.2 step 5), the interface the discharge summary and the patient
instructions (7.3, WP7) read.

    from app.modules.med_safety.discharge import discharge_medication_table
    table = discharge_medication_table(fhir, "stay-01102")   # fhir: a FhirGateway (the reader's actor)
    if table is None: ...                                     # no signed discharge reconciliation yet
    table.new / table.changed / table.stopped / table.continued   # lists of DischargeMedication

It reads the encounter's discharge reconciliation record through FhirGateway (so
the reader's 6.3 rights and the audit apply): the DocumentReference of type medrec
(urn:demo-hospital:doc-type) with category `discharge`
(urn:demo-hospital:medrec-kind), and its JSON attachment `medrec-data`, key
`discharge_medications`. By default only a final record counts (signed by the
pharmacist and co-signed by a physician); `final_only=False` also returns one
awaiting the co-signature or still in review, with that status. Every line keeps
its sources (`source_refs`): the discharge prescription (MedicationRequest, intent
plan), the home medication (MedicationStatement) and the inpatient order, so a
summary can cite them. HTTP: `GET /api/med-safety/encounters/{id}/discharge-medications`.
"""

from __future__ import annotations

import base64
import json
from typing import Literal

from pydantic import BaseModel, Field

from app.ehr.codes import DOC_TYPES
from app.ehr.gateway import FhirGateway
from app.ehr.signing import signatures
from app.modules.med_safety.reconcile import DischargeMedication
from app.modules.med_safety.tools import DATA_TITLE, MEDREC_KIND

SCHEMA = "discharge-medication-table@1"


class Signature(BaseModel):
    role: str | None = None
    by: str | None = None  # Practitioner reference
    name: str | None = None
    at: str | None = None


class DischargeMedicationTable(BaseModel):
    schema_id: str = Field(default=SCHEMA, alias="schema")
    encounter_id: str
    patient_id: str
    status: Literal["final", "awaiting_cosign", "in_review"]  # final: pharmacist signed and physician co-signed
    document: str  # "DocumentReference/<id>", the discharge reconciliation record
    session_id: str | None = None
    signatures: list[Signature] = Field(default_factory=list)
    new: list[DischargeMedication] = Field(default_factory=list)
    changed: list[DischargeMedication] = Field(default_factory=list)
    stopped: list[DischargeMedication] = Field(default_factory=list)
    continued: list[DischargeMedication] = Field(default_factory=list)

    model_config = {"populate_by_name": True}

    def all(self) -> list[DischargeMedication]:
        return [*self.new, *self.changed, *self.stopped, *self.continued]


def _kind(document: dict) -> str | None:
    return next((c.get("code") for cc in document.get("category") or [] for c in cc.get("coding") or []
                 if c.get("system") == MEDREC_KIND), None)


def _data(document: dict) -> dict | None:
    for content in document.get("content") or []:
        attachment = content.get("attachment") or {}
        if attachment.get("contentType") == "application/json" and attachment.get("title") == DATA_TITLE:
            return json.loads(base64.b64decode(attachment["data"]).decode())
    return None


def discharge_medication_table(fhir: FhirGateway, encounter_id: str, *,
                               final_only: bool = True) -> DischargeMedicationTable | None:
    """The encounter's discharge medication table, or None when there is no (final) discharge reconciliation."""
    medrec = DOC_TYPES["medrec"][1]
    found = []
    for doc in fhir.get_documents(encounter_id):
        data = doc.to_fhir()
        codes = {c.get("code") for c in (data.get("type") or {}).get("coding") or []}
        if medrec in codes and _kind(data) == "discharge" and data.get("status") == "current":
            found.append(data)
    if not found:
        return None
    doc = max(found, key=lambda d: d.get("date") or "")
    signed = signatures(doc)
    status = "final" if doc.get("docStatus") == "final" else ("awaiting_cosign" if signed else "in_review")
    if final_only and status != "final":
        return None
    payload = _data(doc) or {}
    table = payload.get("discharge_medications") or {}
    return DischargeMedicationTable(
        encounter_id=encounter_id, patient_id=(doc.get("subject") or {}).get("reference", "").split("/")[-1],
        status=status, document=f"DocumentReference/{doc['id']}", session_id=payload.get("session_id"),
        signatures=[Signature(**s) for s in signed],
        **{k: [DischargeMedication.model_validate(r) for r in table.get(k) or []]
           for k in ("new", "changed", "stopped", "continued")})
