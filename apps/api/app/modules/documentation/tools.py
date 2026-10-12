"""Tool handlers of the documentation agents (config/tools/getDischargeMedications.yaml, getHandoffContext.yaml,
getSignedDischargeSummary.yaml, draftClinicalDocument.yaml).

The Tool Gateway calls them after its checks (6.4); `ctx.fhir` is FhirGateway for
the actor the gateway chose (the user the agent acts for, or the agent itself) with
the agent as purpose module, so 6.3's rights, the registry's writes and the audit
apply underneath.

- getDischargeMedications (read): WP6's discharge medication table of the encounter
  (`discharge_medication_table`, final or not, with its status) and its record.
- getHandoffContext (read): what a shift handoff needs: the encounter, the patient,
  diagnoses and procedures of the stay, its medication orders, the observations
  since the cut-off, open tasks and active flags.
- getSignedDischargeSummary (read): the encounter, the patient (language) and the
  encounter's discharge summaries written by this module (the instructions use only
  a signed one).
- draftClinicalDocument (recommend): create or update the module's draft of a
  document (DocumentReference, docStatus preliminary; one per document and
  encounter, deterministic id); a signed document is never rewritten.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from app.core.store import get_store
from app.ehr import access
from app.ehr.clock import hospital_now
from app.fhir.dt import fhir_datetime, parse
from app.modules.documentation import document as D

if TYPE_CHECKING:
    from app.agents.gateway import ToolContext


def _ref(resource: dict) -> str:
    return f"{resource['resourceType']}/{resource['id']}"


def _encounter(ctx: ToolContext, encounter_id: str) -> dict:
    gateway = ctx.fhir  # FhirGateway for the tool's actor (the Tool Gateway opened it)
    found = gateway.read("Encounter", encounter_id)
    if found is None:
        raise LookupError(f"Encounter/{encounter_id} not found")
    return found.to_fhir()


def _patient(ctx: ToolContext, encounter: dict) -> dict | None:
    pid = access.patient_of(encounter)
    gateway = ctx.fhir
    found = gateway.read("Patient", pid) if pid and ctx.readable("Patient") else None
    return found.to_fhir() if found is not None else None


# ---------- read ----------


def get_discharge_medications(ctx: ToolContext, encounter_id: str) -> dict:
    from app.modules.med_safety.discharge import discharge_medication_table

    table = discharge_medication_table(ctx.fhir, encounter_id, final_only=False)
    if table is None:
        return {"resources": [], "table": None, "_refs": []}
    data = table.model_dump(mode="json", by_alias=True)
    gateway = ctx.fhir
    found = gateway.read("DocumentReference", table.document.split("/", 1)[1])
    resources = [found.to_fhir()] if found is not None else []
    return {"resources": resources, "table": data, "_refs": [table.document]}


def handoff_resources(fhir, encounter_id: str, since: str, readable) -> list[dict]:
    """The 12-hour handoff context (also read by the module itself for the template, with no model)."""
    found = fhir.read("Encounter", encounter_id)
    if found is None:
        raise LookupError(f"Encounter/{encounter_id} not found")
    encounter = found.to_fhir()
    out = [encounter]
    pid = access.patient_of(encounter)
    if pid and readable("Patient") and (patient := fhir.read("Patient", pid)) is not None:
        out.append(patient.to_fhir())
    for rtype in ("Condition", "Procedure", "MedicationRequest", "Task", "Flag"):
        if readable(rtype):
            out += [r.to_fhir() for r in fhir.get_encounter_resources(rtype, encounter_id)]
    out = [r for r in out if not (r["resourceType"] == "Task" and r.get("status") in ("completed", "cancelled",
                                                                                         "rejected", "failed"))]
    out = [r for r in out if not (r["resourceType"] == "Flag" and r.get("status") != "active")]
    if readable("Observation"):
        out += [o.to_fhir() for o in fhir.get_observations(encounter_id, since=parse(since))]
    return out


def get_handoff_context(ctx: ToolContext, encounter_id: str, since: str) -> dict:
    out = handoff_resources(ctx.fhir, encounter_id, since, ctx.readable)
    return {"resources": out, "_refs": [_ref(r) for r in out]}


def get_signed_discharge_summary(ctx: ToolContext, encounter_id: str) -> dict:
    encounter = _encounter(ctx, encounter_id)
    out = [encounter]
    if (patient := _patient(ctx, encounter)) is not None:
        out.append(patient)
    summaries = [d.to_fhir() for d in ctx.fhir.get_documents(encounter_id)]
    summaries = [d for d in summaries if D.kind_of(d) == "discharge_summary" and d.get("status") == "current"]
    out += summaries
    return {"resources": out, "_refs": [_ref(r) for r in out]}


# ---------- recommend ----------


def draft_clinical_document(ctx: ToolContext, encounter_id: str, kind: str, document_id: str, title: str,
                            texts: list[dict], data: str, source_refs: list[str] | None = None,
                            sign_block: str | None = None) -> dict:
    payload = json.loads(data)
    if payload.get("schema") != D.SCHEMAS[kind]:
        raise ValueError(f"data is not a {D.SCHEMAS[kind]} document")
    if not document_id.startswith(D.KINDS[kind][1] + "-"):
        raise ValueError(f"a {kind} draft's id starts with {D.KINDS[kind][1]}-")
    encounter = _encounter(ctx, encounter_id)
    patient_id = access.patient_of(encounter)
    when = fhir_datetime(hospital_now(get_store()))
    gateway = ctx.fhir
    current = gateway.read("DocumentReference", document_id)
    if current is not None:
        existing = current.to_fhir()
        if access.encounter_of(existing) != encounter_id:
            raise ValueError(f"DocumentReference/{document_id} belongs to another encounter")
        if existing.get("docStatus") != "preliminary":
            raise ValueError(f"DocumentReference/{document_id} is signed ({existing.get('docStatus')}); it stays as "
                             f"it is")
        doc = D.apply_content(existing, texts=texts, data=payload, title=title, block=sign_block,
                              sources=source_refs or [], when=when)
        doc["author"] = [{"display": f"AI draft by {ctx.agent.label}"}]
        stored = gateway.update(ctx.stamp(doc)).to_fhir()
    else:
        doc = D.new_document(kind, document_id, patient_id=patient_id, encounter_id=encounter_id, title=title,
                             texts=texts, data=payload, block=sign_block, when=when,
                             author=f"AI draft by {ctx.agent.label}", sources=source_refs or [])
        stored = gateway.create(ctx.stamp(doc)).to_fhir()
    return {"document_id": stored["id"], "doc_status": stored["docStatus"], "_refs": [_ref(stored)]}
