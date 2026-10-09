"""Hospital platform API (spec 6.3): unit census and patient chart, break-glass,
consent changes, signing, and the safety-tier registry.

Every read and write goes through FhirGateway as the signed-in user, so the
role's resource types, unit scope and reduced views are enforced on the server:
the chart only contains the sections the role may read, and a patient outside a
physician's or nurse's units answers 403 with `code: break_glass_required`
(app/main.py maps FhirAccessDenied) until they open a break-glass grant.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.agents import registry
from app.core.auth import client_ip, current_user, require_roles
from app.core.models import Role, StaffUser
from app.core.store import get_store
from app.ehr import access, breakglass, consent
from app.ehr.codes import MRN_SYSTEM
from app.ehr.gateway import Actor, FhirConflict, FhirGateway, source_module
from app.ehr.reference import UNITS
from app.ehr.signing import signatures

router = APIRouter(prefix="/api")
VIEWERS = (Role.PHYSICIAN, Role.NURSE, Role.PHARMACIST, Role.CLERK, Role.OPERATIONS_MANAGER)
CHART = "patient_chart"  # purpose module of chart and census reads


def _fhir(user: StaffUser, request: Request, module: str = CHART) -> FhirGateway:
    return FhirGateway(Actor.of(user, request), module)


@router.get("/hospital/units")
def units(user: StaffUser = Depends(require_roles(*VIEWERS))):
    policy = access.policy_for(user.role)
    return {"units": [{"id": u.id, "name": u.name} for u in UNITS], "mine": user.unit_ids, "scope": policy.scope}


@router.get("/hospital/census")
def census(unit: str, request: Request, user: StaffUser = Depends(require_roles(*VIEWERS))):
    try:
        entries = _fhir(user, request).get_census(unit)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"unit": unit, "patients": [e.model_dump(mode="json") for e in entries]}


def _name(patient, use: str | None) -> str | None:
    for n in patient.name or []:
        if (n.use or "official") == (use or "official"):
            return n.text or " ".join([*(n.given or []), n.family or ""]).strip()
    return None


def _document_view(doc, role: str) -> dict:
    data = doc.to_fhir()
    module = source_module(data)
    entry = registry.agent(module) if module else None
    signed = signatures(data)
    required = list(entry.required_signoff_role) if entry else []
    missing = [r for r in required if r not in {s["role"] for s in signed}] if entry and entry.cosign else (
        [] if signed else required)
    return {
        "id": doc.id, "type": (doc.type.coding or [None])[0].display if doc.type.coding else doc.type.text,
        "doc_status": doc.docStatus, "date": doc.date, "description": data.get("description"), "module": module,
        "tier": entry.tier if entry else None, "required_signoff_role": required,
        "cosign": bool(entry and entry.cosign), "signatures": signed,
        "can_sign": bool(entry and doc.docStatus == "preliminary" and role in required and role in missing),
        "text": _attachment_text(data),
    }


def _attachment_text(doc: dict) -> str | None:
    import base64

    for content in doc.get("content") or []:
        attachment = content.get("attachment") or {}
        if attachment.get("data") and (attachment.get("contentType") or "").startswith("text/"):
            return base64.b64decode(attachment["data"]).decode("utf-8", errors="replace")
    return None


def _observation_view(o) -> dict:
    data = o.to_fhir()
    code = (data.get("code") or {})
    label = code.get("text") or ((code.get("coding") or [{}])[0].get("display"))
    if "valueQuantity" in data:
        value = f"{data['valueQuantity'].get('value')} {data['valueQuantity'].get('unit', '')}".strip()
    elif data.get("component"):
        parts = []
        for c in data["component"]:
            q = c.get("valueQuantity") or {}
            name = (c.get("code") or {}).get("text") or ((c.get("code") or {}).get("coding") or [{}])[0].get("display")
            parts.append(f"{name}: {q.get('value')} {q.get('unit', '')}".strip())
        value = "; ".join(parts)
    else:
        value = data.get("valueString") or str(data.get("valueInteger", "")) or None
    category = ((data.get("category") or [{}])[0].get("coding") or [{}])[0].get("code")
    return {"id": o.id, "label": label, "value": value, "at": data.get("effectiveDateTime"), "category": category}


@router.get("/hospital/patients/{mrn}")
def chart(mrn: str, request: Request, user: StaffUser = Depends(require_roles(*VIEWERS))):
    """The patient as this role may see them: sections the role cannot read are left out."""
    fhir = _fhir(user, request)
    patient = fhir.get_patient(mrn)  # BreakGlassRequired -> 403 break_glass_required
    if patient is None:
        raise HTTPException(status_code=404, detail="No patient with this MRN")
    readable = (lambda t: True) if fhir.policy is None else (lambda t: t in fhir.policy.read)
    grant = breakglass.active_grant(get_store(), user.id, patient.id)
    out: dict = {
        "patient": {"id": patient.id, "mrn": next((i.value for i in patient.identifier or []
                                                   if i.system == MRN_SYSTEM), mrn),
                    "name": _name(patient, None), "name_other": _name(patient, "usual"),
                    "gender": patient.gender, "birth_date": patient.birthDate,
                    "language": (patient.communication or [None])[0].language.coding[0].code
                    if patient.communication and patient.communication[0].language.coding else None},
        "role": str(user.role),
        "break_glass": {"id": grant.id, "expires_at": grant.expires_at.isoformat()} if grant else None,
        "sections": [],
    }
    encounter = None
    if readable("Encounter"):
        found = fhir.search_encounters(patient_id=patient.id, limit=1)
        encounter = found[0] if found else None
        if encounter is not None:
            current = next((loc for loc in reversed(encounter.location or []) if loc.status == "active"), None)
            out["encounter"] = {
                "id": encounter.id, "class": encounter.class_.code if getattr(encounter, "class_", None) else None,
                "status": encounter.status, "start": encounter.period.start if encounter.period else None,
                "end": encounter.period.end if encounter.period else None,
                "location": current.location.reference.split("/", 1)[1] if current else None,
                "reason": (encounter.to_fhir().get("reasonCode") or [{}])[0].get("text")
                if encounter.to_fhir().get("reasonCode") else None}
            out["sections"].append("encounter")
    if readable("Consent"):
        out["consents"] = consent.consent_status(fhir, patient.id)
        out["can_change_consent"] = str(user.role) in access.CONSENT_RECORDERS
        out["sections"].append("consents")
    if encounter is not None and readable("MedicationRequest"):
        meds = fhir.get_medications(encounter.id)
        out["medications"] = [{"id": m.id, "name": (m.medicationCodeableConcept.coding or [None])[0].display
                               if m.medicationCodeableConcept and m.medicationCodeableConcept.coding else None,
                               "status": m.status, "intent": m.intent} for m in meds]
        out["sections"].append("medications")
    if encounter is not None and readable("Observation"):
        observations = fhir.get_observations(encounter.id)
        out["observations"] = [_observation_view(o) for o in observations[-8:]][::-1]
        out["sections"].append("observations")
    if encounter is not None and readable("DocumentReference"):
        out["documents"] = [_document_view(d, str(user.role)) for d in fhir.get_documents(encounter.id)]
        out["sections"].append("documents")
    return out


# ---------- break-glass ----------


class BreakGlassBody(BaseModel):
    mrn: str
    reason: str


@router.post("/hospital/break-glass")
def request_break_glass(body: BreakGlassBody, request: Request,
                        user: StaffUser = Depends(require_roles(Role.PHYSICIAN, Role.NURSE))):
    fhir = _fhir(user, request, "break_glass")
    patient_id = fhir.resolve_mrn(body.mrn)
    if patient_id is None:
        raise HTTPException(status_code=404, detail="No patient with this MRN")
    if fhir.is_in_scope(patient_id):
        raise HTTPException(status_code=409, detail="This patient is already one of your unit's patients")
    try:
        grant = breakglass.request_access(get_store(), user_id=user.id, user_name=user.name, role=str(user.role),
                                          patient_id=patient_id, mrn=body.mrn, reason=body.reason,
                                          source_ip=client_ip(request))
    except breakglass.BreakGlassError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return {"id": grant.id, "mrn": body.mrn, "granted_at": grant.granted_at, "expires_at": grant.expires_at,
            "review_due": grant.review_due}


@router.get("/hospital/break-glass/active")
def active_break_glass(user: StaffUser = Depends(current_user)):
    now = datetime.now()
    return {"grants": [{"id": g.id, "mrn": g.mrn, "granted_at": g.granted_at, "expires_at": g.expires_at,
                        "minutes_left": max(0, int((g.expires_at - now).total_seconds() // 60))}
                       for g in breakglass.active_for_user(get_store(), user.id, now)]}


@router.get("/admin/break-glass")
def break_glass_queue(status: str = "pending", user: StaffUser = Depends(require_roles(Role.ADMIN))):
    return {"grants": breakglass.queue(get_store(), status=status)}


class ReviewBody(BaseModel):
    decision: Literal["justified", "not_justified"]
    note: str = ""


@router.post("/admin/break-glass/{grant_id}/review")
def review_break_glass(grant_id: str, body: ReviewBody, request: Request,
                       user: StaffUser = Depends(require_roles(Role.ADMIN))):
    try:
        grant = breakglass.review(get_store(), grant_id, decision=body.decision, note=body.note, reviewer_id=user.id,
                                  reviewer_name=user.name, reviewer_role=str(user.role), source_ip=client_ip(request))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except breakglass.BreakGlassError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return grant.model_dump(mode="json", exclude={"mrn"})


# ---------- consent and signing ----------


class ConsentBody(BaseModel):
    category: Literal["ai_processing", "followup_call", "sms"]
    decision: Literal["permit", "deny"]


@router.post("/hospital/patients/{mrn}/consents")
def change_consent(mrn: str, body: ConsentBody, request: Request,
                   user: StaffUser = Depends(require_roles(Role.CLERK, Role.NURSE, Role.PHYSICIAN))):
    fhir = _fhir(user, request, "consent_management")
    patient_id = fhir.resolve_mrn(mrn)
    if patient_id is None:
        raise HTTPException(status_code=404, detail="No patient with this MRN")
    result = consent.change_consent(fhir, patient_id, body.category, body.decision == "permit")
    return {"category": body.category, "previous": result["previous"], "current": result["current"],
            "event": result["event"].type if result["event"] else None,
            "consents": consent.consent_status(fhir, patient_id)}


@router.post("/hospital/documents/{document_id}/sign")
def sign_document(document_id: str, request: Request,
                  user: StaffUser = Depends(require_roles(Role.PHYSICIAN, Role.NURSE, Role.PHARMACIST))):
    try:
        result = _fhir(user, request, "signing").sign_document(document_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except FhirConflict as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return {"document_id": result.document_id, "doc_status": result.doc_status, "signed_roles": result.signed_roles,
            "missing_roles": result.missing_roles}


@router.get("/hospital/registry")
def module_registry(user: StaffUser = Depends(current_user)):
    """The registry entries that write to the EHR, in the 6.3 shape (the full registry is GET /api/agents)."""
    return {"modules": {name: {"tier": spec.risk_tier, "purpose": spec.purpose, "kind": spec.kind,
                               "required_signoff_role": spec.required_signoff_role, "cosign": spec.cosign,
                               "consent_required": spec.consent_required,
                               "writes_allowed": {t: sorted(s) for t, s in sorted(spec.writes().items())},
                               "allowed_tools": spec.allowed_tools}
                        for name, spec in sorted(registry.agents().items()) if spec.writes()}}
