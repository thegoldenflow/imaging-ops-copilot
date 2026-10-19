"""Signing service (spec 6.3, design rule 2): the only way a DocumentReference becomes final.

A draft is created as `docStatus=preliminary` by the module that wrote it (its
`source-module` extension). Signing it:

1. needs an agent-registry entry for that module (config/agents, 6.4) whose
   tools let its documents become final (signDocumentFinal), and a person
   (system actors and agents cannot sign);
2. the signer's role must be one of the module's `required_signoff_role` and the
   patient must be in the signer's scope (their units, or a break-glass grant);
3. adds a signature (extension `signature`: role, signer, time; and a `note` when
   the storyline's demo automation signed as that person, app/core/context.py). With `cosign`
   (medication reconciliation: pharmacist and physician) the draft stays
   preliminary until every listed role has signed; otherwise one signature
   finalises it. The final document gets `docStatus=final` and `authenticator`:
   the signer, or with `cosign` the signer of the first listed role (the
   pharmacist of a medication reconciliation, spec 7.2), whatever the order of
   the signatures; the co-signers stay in the signature extensions;
4. writes a `sign` audit event (refusals are `sign` events with outcome denied);
5. for a draft an agent wrote (extension ai-run), adds the signer to the run's
   Provenance and records the signature as the trace's human action (6.4).

Some documents have their own signing flow and the generic path (the chart's
Sign button, the signDocumentFinal tool) refuses them (`signing_flow`): a
medication reconciliation record is signed only in Medication safety's
pharmacist workbench (WP6, spec 7.2), which records the pharmacist's decision on
every discrepancy first and passes `flow=MEDREC_WORKBENCH`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from app.core.store import get_store
from app.ehr import access
from app.ehr.clock import hospital_now
from app.ehr.codes import DOC_TYPES, EXT
from app.fhir.dt import fhir_datetime, ref
from app.fhir.types import validate

if TYPE_CHECKING:
    from app.ehr.gateway import FhirGateway

SIGNATURE = EXT + "signature"
MEDREC_WORKBENCH = "medrec_workbench"
# Why the generic path refuses a document with its own flow (the refusal's message and audit detail).
FLOW_REFUSALS = {
    MEDREC_WORKBENCH: "Medication reconciliation records are signed in Medication safety (the pharmacist workbench): "
                      "the pharmacist decides every discrepancy and signs, then a physician co-signs",
}


@dataclass
class SignResult:
    document_id: str
    doc_status: str  # preliminary (waiting for a co-signature) or final
    signed_roles: list[str]
    missing_roles: list[str]


def signatures(document: dict) -> list[dict]:
    """[{role, by, name, at, note}] from the document's signature extensions (note: the demo automation's, else None)."""
    out = []
    for ext in document.get("extension") or []:
        if ext.get("url") != SIGNATURE:
            continue
        parts = {e.get("url"): e for e in ext.get("extension") or []}
        out.append({"role": (parts.get("role") or {}).get("valueCode"),
                    "by": ((parts.get("signer") or {}).get("valueReference") or {}).get("reference"),
                    "name": ((parts.get("signer") or {}).get("valueReference") or {}).get("display"),
                    "at": (parts.get("time") or {}).get("valueDateTime"),
                    "note": (parts.get("note") or {}).get("valueString")})
    return out


def signing_flow(document: dict) -> str | None:
    """The flow a document must be signed in, or None when the generic path signs it.

    A medication reconciliation record (type MedRec, or any document of the medication_reconciliation module): signed
    elsewhere it would count as reconciled (the journey waits for it) with none of the pharmacist's decisions
    recorded."""
    from app.ehr.gateway import source_module

    codes = {c.get("code") for c in (document.get("type") or {}).get("coding") or []}
    if DOC_TYPES["medrec"][1] in codes or source_module(document) == "medication_reconciliation":
        return MEDREC_WORKBENCH
    return None


def sign(fhir: FhirGateway, document_id: str, *, at: datetime | None = None, flow: str | None = None) -> SignResult:
    """Sign a draft. `flow` names the module flow calling (MEDREC_WORKBENCH); documents with their own flow are
    refused without it."""
    from app.ehr.gateway import FhirConflict, source_module

    doc = fhir.backend.read("DocumentReference", document_id)
    if doc is None:
        raise LookupError(f"DocumentReference/{document_id} not found")
    patient, encounter = access.patient_of(doc), access.encounter_of(doc)
    module = source_module(doc)

    def deny(why: str):
        fhir._audit("sign", "DocumentReference", document_id, outcome="denied", detail=why, patient=patient,
                    encounter=encounter, event_type="sign", module=module)
        from app.ehr.gateway import FhirAccessDenied

        raise FhirAccessDenied(why)

    from app.agents import registry

    entry = registry.agent(module) if module else None
    if entry is None:
        deny(f"the document's module ({module or 'unknown'}) has no entry in the agent registry")
    if not entry.allows("DocumentReference", "final"):
        deny(f"documents of module {module} cannot become final")
    if fhir.policy is None or fhir.actor.kind != "user":
        deny("only a person can sign; system actors and agents cannot")
    role = fhir.role
    if not fhir.policy.signs or role not in entry.required_signoff_role:
        deny(f"documents of module {module} are signed by {', '.join(entry.required_signoff_role) or 'nobody'}, "
             f"not the {role} role")
    fhir._require_patient("sign", "DocumentReference", document_id, patient, encounter)
    if (own := signing_flow(doc)) is not None and flow != own:
        deny(FLOW_REFUSALS[own])
    if doc.get("docStatus") != "preliminary":
        raise FhirConflict(f"DocumentReference/{document_id} is already {doc.get('docStatus')}")
    done = signatures(doc)
    if any(s["role"] == role for s in done):
        raise FhirConflict(f"The {role} signature is already on this document")

    # A draft whose items do not all trace to their sources (the documentation module's validators, spec 7.3) carries
    # a sign block: refused on every path (the module's review, the chart's Sign button, signDocumentFinal).
    block = next((e.get("valueString") for e in doc.get("extension") or [] if e.get("url") == EXT + "sign-block"), None)
    if block is not None:
        deny(f"the validator blocks signing: {block}")

    at = at or hospital_now(get_store())
    signer = ref("Practitioner", fhir.actor.practitioner_id, fhir.actor.name) if fhir.actor.practitioner_id else \
        {"reference": f"urn:demo-hospital:user:{fhir.actor.id}", "display": fhir.actor.name}
    from app.core.context import automation_note

    note = automation_note(fhir.actor.id, fhir.actor.name, role)  # the demo automation signing as this person
    doc.setdefault("extension", []).append({"url": SIGNATURE, "extension": [
        {"url": "role", "valueCode": role}, {"url": "signer", "valueReference": signer},
        {"url": "time", "valueDateTime": fhir_datetime(at)}, *([{"url": "note", "valueString": note}] if note else [])]})
    signed = [s["role"] for s in done] + [role]
    missing = [r for r in entry.required_signoff_role if r not in signed] if entry.cosign else []
    if not missing:
        doc["docStatus"] = "final"
        doc["authenticator"] = signer
        if entry.cosign:  # the primary signer (first listed role) authenticates; the co-signers are extensions
            primary = next((s for s in done if s["role"] == entry.required_signoff_role[0]), None)
            if primary is not None and primary["by"]:
                doc["authenticator"] = {"reference": primary["by"], **({"display": primary["name"]}
                                                                        if primary["name"] else {})}
    validate(doc)
    fhir.backend.update(doc)
    detail = "final" if not missing else f"co-signature {len(signed)} of {len(entry.required_signoff_role)}; " \
                                         f"waiting for {', '.join(missing)}"
    fhir._audit("sign", "DocumentReference", document_id, detail=detail, patient=patient, encounter=encounter,
                event_type="sign", module=module)
    from app.agents.runtime import record_signature

    record_signature(doc, signer, role, fhir.actor.id)  # an AI draft: the signer joins its Provenance and trace
    from app.ehr.events import bus, platform_event

    refs = {"document": f"DocumentReference/{document_id}",
            **({"patient": f"Patient/{patient}"} if patient else {}),
            **({"encounter": f"Encounter/{encounter}"} if encounter else {})}
    bus.publish(platform_event("document.signed", at=at, actor=f"user:{fhir.actor.id}", refs=refs,
                               key=f"document-signed|{document_id}|{role}",
                               attrs={"role": role, "final": not missing, "module": module}))
    if not missing and document_id.startswith("dsum-") and encounter:
        from app.agents import cause as ai_cause
        from app.agents import ondemand

        # the background drafts the patient instructions from the signed summary: as this person's action
        cause = ai_cause.from_request("action")
        ondemand.remember(f"instructions:{encounter}", cause and cause.model_copy(update={
            "label": f"Patient instructions ({encounter})", "link": "/documentation",
            "note": "drafted after the discharge summary was signed"}))
    return SignResult(document_id, doc["docStatus"], signed, missing)
