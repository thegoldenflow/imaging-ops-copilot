"""Tool handlers of medication safety (config/tools/getMedicationContext.yaml, getServiceOrderContext.yaml,
submitOrderReview.yaml, draftMedRec.yaml).

The Tool Gateway calls them after its checks (6.4); `ctx.fhir` is FhirGateway for
the actor the gateway chose (the agent itself in the background runs) with the
agent as purpose module, so the EHR's allow-list, registry writes and audit apply.

- getMedicationContext (read): the encounter, the patient, the encounter's
  medication orders and discharge prescriptions, the home medications, allergies,
  the latest kidney function, INR, potassium and weight, and the diagnosis list.
- getServiceOrderContext (read): a lab or imaging order and the same test ordered
  in the 72 hours before it, with their results.
- submitOrderReview (recommend): the AI commentary on an order as an `ai-review`
  Task (intent proposal, focus the order) for the reviewer; nothing about the order changes.
- draftMedRec (recommend): the draft medication reconciliation record
  (DocumentReference, docStatus preliminary) with the readable text and the
  structured reconciliation as a JSON attachment; updates the encounter's draft
  when one is named. Only the signing service makes it final.
"""

from __future__ import annotations

import base64
import json
from datetime import timedelta
from typing import TYPE_CHECKING

from app.agents.approvals import REVIEW
from app.core.store import get_store
from app.ehr import access
from app.ehr.clock import hospital_now
from app.ehr.codes import DOC_TYPES, MRN_SYSTEM, PRACTITIONER_ROLE, TASK_CODE, concept
from app.fhir.dt import fhir_datetime, parse, ref, ref_id
from app.modules.med_safety.document import record_id

if TYPE_CHECKING:
    from app.agents.gateway import ToolContext

# Labs and measurements a medication review looks at (latest values): eGFR, creatinine, INR, potassium, weight
CONTEXT_CODES = ("33914-3", "2160-0", "6301-6", "2823-3", "29463-7")
MEDREC_KIND = "urn:demo-hospital:medrec-kind"
DATA_TITLE = "medrec-data"  # the JSON attachment's title (app/modules/med_safety/discharge.py reads it)
DUPLICATE_WINDOW = timedelta(hours=72)


def _ref(resource: dict) -> str:
    return f"{resource['resourceType']}/{resource['id']}"


def _out(resources: list[dict]) -> dict:
    return {"resources": resources, "_refs": [_ref(r) for r in resources]}


def _encounter(ctx: ToolContext, encounter_id: str) -> dict:
    gateway = ctx.fhir  # FhirGateway for the tool's actor (the Tool Gateway opened it)
    found = gateway.read("Encounter", encounter_id)
    if found is None:
        raise LookupError(f"Encounter/{encounter_id} not found")
    return found.to_fhir()


def _latest_per_code(observations: list[dict], keep: int = 2) -> list[dict]:
    by_code: dict[str, list[dict]] = {}
    for o in observations:
        code = ((o.get("code") or {}).get("coding") or [{}])[0].get("code")
        by_code.setdefault(code, []).append(o)
    out = []
    for items in by_code.values():
        items.sort(key=lambda o: o.get("effectiveDateTime") or "")
        out += items[-keep:]
    return out


# ---------- read ----------


def get_medication_context(ctx: ToolContext, encounter_id: str) -> dict:
    gateway = ctx.fhir  # FhirGateway for the tool's actor (the Tool Gateway opened it)
    encounter = _encounter(ctx, encounter_id)
    patient_id = access.patient_of(encounter)
    out = [encounter]
    patient = gateway.read("Patient", patient_id).to_fhir() if patient_id else None
    if patient:
        out.append(patient)
    out += [m.to_fhir() for m in gateway.get_medications(encounter_id)]
    mrn = next((i.get("value") for i in (patient or {}).get("identifier") or [] if i.get("system") == MRN_SYSTEM), None)
    if mrn:
        out += [m.to_fhir() for m in gateway.get_home_meds(mrn)]
    if patient_id:
        out += [a.to_fhir() for a in gateway.get_patient_resources("AllergyIntolerance", patient_id)
                if (a.to_fhir().get("clinicalStatus") or {}).get("coding", [{}])[0].get("code") in ("active", None)]
        labs = [o.to_fhir() for o in gateway.get_patient_resources("Observation", patient_id,
                                                                     codes=list(CONTEXT_CODES))]
        out += _latest_per_code(labs)
        conditions = [c.to_fhir() for c in gateway.get_patient_resources("Condition", patient_id)]
        out += [c for c in conditions
                if (c.get("clinicalStatus") or {}).get("coding", [{}])[0].get("code") == "active"
                or ref_id(c.get("encounter")) == encounter_id]
    return _out(out)


def get_service_order_context(ctx: ToolContext, encounter_id: str, order_id: str) -> dict:
    gateway = ctx.fhir  # FhirGateway for the tool's actor (the Tool Gateway opened it)
    found = gateway.read("ServiceRequest", order_id)
    if found is None:
        raise LookupError(f"ServiceRequest/{order_id} not found")
    order = found.to_fhir()
    patient_id = access.patient_of(order)
    authored = parse(order.get("authoredOn"))
    code = (order.get("code") or {}).get("coding", [{}])[0].get("code")
    out = [order]
    if patient_id and authored is not None:
        earlier = [s.to_fhir() for s in gateway.get_patient_resources("ServiceRequest", patient_id,
                                                                         since=authored - DUPLICATE_WINDOW)]
        same = [s for s in earlier if s["id"] != order_id and (s.get("code") or {}).get("coding", [{}])[0].get("code")
                == code and (parse(s.get("authoredOn")) or authored) <= authored]
        out += same
        for s in same:
            result = gateway.read("Observation", f"obs-{s['id']}") if ctx.readable("Observation") else None
            if result is not None:
                out.append(result.to_fhir())
        if same and ctx.readable("DiagnosticReport"):
            wanted = {f"ServiceRequest/{s['id']}" for s in same}
            out += [r.to_fhir() for r in gateway.get_patient_resources("DiagnosticReport", patient_id,
                                                                         since=authored - DUPLICATE_WINDOW)
                    if any((b.get("reference") in wanted) for b in r.to_fhir().get("basedOn") or [])]
    return _out(out)


# ---------- recommend ----------


def _entry(name: str, value: str) -> dict:
    return {"type": {"text": name}, "valueString": value}


def submit_order_review(ctx: ToolContext, review_id: str, encounter_id: str, order_ref: str,
                        reviewer_roles: list[str], severity: str, summary: str, commentary: str,
                        evidence_refs: list[str]) -> dict:
    gateway = ctx.fhir  # FhirGateway for the tool's actor (the Tool Gateway opened it)
    encounter = _encounter(ctx, encounter_id)
    now = fhir_datetime(hospital_now(get_store()))
    priority = {"blocking": "stat", "major": "urgent", "moderate": "asap"}.get(severity, "routine")
    task = {"resourceType": "Task", "status": "requested", "intent": "proposal", "priority": priority,
            "code": concept(TASK_CODE, REVIEW, "Review an AI recommendation"),
            "description": f"Order review {review_id}: {summary}"[:1000], "focus": {"reference": order_ref},
            "for": encounter["subject"], "encounter": ref("Encounter", encounter_id), "authoredOn": now,
            "lastModified": now,
            "performerType": [concept(PRACTITIONER_ROLE, access.ROLE_CODE[r]) for r in reviewer_roles],
            "input": [_entry("order-review", review_id), _entry("severity", severity),
                      _entry("recommendation", commentary),
                      *([_entry("evidence", ", ".join(evidence_refs))] if evidence_refs else [])]}
    stored = gateway.create(ctx.stamp(task)).to_fhir()
    return {"task_id": stored["id"], "status": stored["status"], "_refs": [_ref(stored)]}


def _attachments(text: str, data: str, title: str) -> list[dict]:
    return [{"attachment": {"contentType": "text/plain", "language": "en", "title": title,
                            "data": base64.b64encode(text.encode()).decode()}},
            {"attachment": {"contentType": "application/json", "title": DATA_TITLE,
                            "data": base64.b64encode(data.encode()).decode()}}]


def medrec_document(patient_id: str, encounter_id: str, kind: str, text: str, data: str, *, when: str,
                    source_refs: list[str] | None = None, author: str = "") -> dict:
    """A draft medication reconciliation record (also written by the module when the agent is refused)."""
    system, code, display = DOC_TYPES["medrec"]
    title = f"{display} ({kind})"
    json.loads(data)  # the structured part must be JSON
    return {"resourceType": "DocumentReference", "id": record_id(encounter_id, kind), "status": "current",
            "docStatus": "preliminary",
            "type": concept(system, code, display), "category": [concept(MEDREC_KIND, kind, f"{kind} reconciliation")],
            "subject": ref("Patient", patient_id), "date": when, "description": title,
            **({"author": [{"display": author}]} if author else {}),
            "content": _attachments(text, data, title),
            "context": {"encounter": [ref("Encounter", encounter_id)],
                        "related": [{"reference": r} for r in source_refs or []]}}


def draft_medrec(ctx: ToolContext, encounter_id: str, session_id: str, kind: str, text: str, data: str,
                 document_id: str | None = None, source_refs: list[str] | None = None) -> dict:
    gateway = ctx.fhir  # FhirGateway for the tool's actor (the Tool Gateway opened it)
    encounter = _encounter(ctx, encounter_id)
    patient_id = access.patient_of(encounter)
    when = fhir_datetime(hospital_now(get_store()))
    doc = medrec_document(patient_id, encounter_id, kind, text, data, when=when, source_refs=source_refs,
                          author=f"AI draft by {ctx.agent.label} (reconciliation {session_id})")
    target = document_id or doc["id"]  # another draft named (e.g. one already on the encounter), or the record's own
    current = gateway.read("DocumentReference", target)
    if current is None and document_id:
        raise LookupError(f"DocumentReference/{document_id} not found")
    if current is not None:
        existing = current.to_fhir()
        if access.encounter_of(existing) != encounter_id:
            raise ValueError(f"DocumentReference/{target} belongs to another encounter")
        if existing.get("docStatus") != "preliminary":
            raise ValueError(f"DocumentReference/{target} is signed ({existing.get('docStatus')}); it stays as it is")
        doc = {**existing, **{k: v for k, v in doc.items() if k != "extension"}, "id": target}
        stored = gateway.update(ctx.stamp(doc)).to_fhir()
    else:
        stored = gateway.create(ctx.stamp(doc)).to_fhir()
    return {"document_id": stored["id"], "doc_status": stored["docStatus"], "_refs": [_ref(stored)]}
