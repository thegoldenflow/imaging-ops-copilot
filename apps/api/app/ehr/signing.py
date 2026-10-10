"""Signing service (spec 6.3, design rule 2): the only way a DocumentReference becomes final.

A draft is created as `docStatus=preliminary` by the module that wrote it (its
`source-module` extension). Signing it:

1. needs an agent-registry entry for that module (config/agents, 6.4) whose
   tools let its documents become final (signDocumentFinal), and a person
   (system actors and agents cannot sign);
2. the signer's role must be one of the module's `required_signoff_role` and the
   patient must be in the signer's scope (their units, or a break-glass grant);
3. adds a signature (extension `signature`: role, signer, time). With `cosign`
   (medication reconciliation: pharmacist and physician) the draft stays
   preliminary until every listed role has signed; otherwise one signature
   finalises it. The final document gets `authenticator` (the last signer's
   Practitioner) and `docStatus=final`;
4. writes a `sign` audit event (refusals are `sign` events with outcome denied);
5. for a draft an agent wrote (extension ai-run), adds the signer to the run's
   Provenance and records the signature as the trace's human action (6.4).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from app.core.store import get_store
from app.ehr import access
from app.ehr.clock import hospital_now
from app.ehr.codes import EXT
from app.fhir.dt import fhir_datetime, ref
from app.fhir.types import validate

if TYPE_CHECKING:
    from app.ehr.gateway import FhirGateway

SIGNATURE = EXT + "signature"


@dataclass
class SignResult:
    document_id: str
    doc_status: str  # preliminary (waiting for a co-signature) or final
    signed_roles: list[str]
    missing_roles: list[str]


def signatures(document: dict) -> list[dict]:
    """[{role, by, at}] from the document's signature extensions."""
    out = []
    for ext in document.get("extension") or []:
        if ext.get("url") != SIGNATURE:
            continue
        parts = {e.get("url"): e for e in ext.get("extension") or []}
        out.append({"role": (parts.get("role") or {}).get("valueCode"),
                    "by": ((parts.get("signer") or {}).get("valueReference") or {}).get("reference"),
                    "name": ((parts.get("signer") or {}).get("valueReference") or {}).get("display"),
                    "at": (parts.get("time") or {}).get("valueDateTime")})
    return out


def sign(fhir: FhirGateway, document_id: str, *, at: datetime | None = None) -> SignResult:
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
    if doc.get("docStatus") != "preliminary":
        raise FhirConflict(f"DocumentReference/{document_id} is already {doc.get('docStatus')}")
    done = signatures(doc)
    if any(s["role"] == role for s in done):
        raise FhirConflict(f"The {role} signature is already on this document")

    at = at or hospital_now(get_store())
    signer = ref("Practitioner", fhir.actor.practitioner_id, fhir.actor.name) if fhir.actor.practitioner_id else \
        {"reference": f"urn:demo-hospital:user:{fhir.actor.id}", "display": fhir.actor.name}
    doc.setdefault("extension", []).append({"url": SIGNATURE, "extension": [
        {"url": "role", "valueCode": role}, {"url": "signer", "valueReference": signer},
        {"url": "time", "valueDateTime": fhir_datetime(at)}]})
    signed = [s["role"] for s in done] + [role]
    missing = [r for r in entry.required_signoff_role if r not in signed] if entry.cosign else []
    if not missing:
        doc["docStatus"] = "final"
        doc["authenticator"] = signer
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
    return SignResult(document_id, doc["docStatus"], signed, missing)
