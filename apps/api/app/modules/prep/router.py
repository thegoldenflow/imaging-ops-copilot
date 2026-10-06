"""System 9 · prep templates and translation approval."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.core.auth import CLINICAL_STAFF, audit_phi, require_roles
from app.core.models import Role, StaffUser
from app.core.store import get_store
from app.core.templates import LANGUAGES
from app.modules.prep import service

router = APIRouter(prefix="/api/prep", tags=["prep"])

APPROVERS = require_roles(Role.MEDICAL_DIRECTOR, Role.RADIOLOGIST, Role.ADMIN)


def _translation(key: str, lang: str):
    store = get_store()
    template = service.templates(store).get(key)
    if template is None or lang not in LANGUAGES or lang == "en":
        raise HTTPException(404, "Unknown template or language")
    return store, template


@router.get("/templates")
def list_templates(user: StaffUser = Depends(require_roles(*CLINICAL_STAFF))):
    store = get_store()
    return {"templates": [t.model_dump(mode="json") for t in service.templates(store).values()], "languages": LANGUAGES}


@router.post("/templates/{key}/{lang}/draft")
def draft(key: str, lang: str, request: Request, user: StaffUser = Depends(APPROVERS)):
    store, _ = _translation(key, lang)
    try:
        translation = service.draft_translation(store, key, lang)
    except RuntimeError as e:
        raise HTTPException(503, str(e)) from e
    audit_phi(request, user, action="create", resource_type="prep_translation", resource_id=f"{key}:{lang}")
    return translation.model_dump(mode="json")


class Edit(BaseModel):
    text: str


@router.put("/templates/{key}/{lang}")
def edit(key: str, lang: str, body: Edit, request: Request, user: StaffUser = Depends(APPROVERS)):
    store, template = _translation(key, lang)
    if not body.text.strip():
        raise HTTPException(422, "Text is empty")
    template.translations[lang] = service.Translation(text=body.text.strip(), status="draft", source="edited",
                                                      updated_at=datetime.now())
    store.touch()
    audit_phi(request, user, action="update", resource_type="prep_translation", resource_id=f"{key}:{lang}")
    return template.translations[lang].model_dump(mode="json")


@router.post("/templates/{key}/{lang}/approve")
def approve(key: str, lang: str, request: Request, user: StaffUser = Depends(APPROVERS)):
    store, template = _translation(key, lang)
    translation = template.translations.get(lang)
    if translation is None:
        raise HTTPException(409, "No draft to approve")
    translation.status, translation.approved_by, translation.approved_at = "approved", user.name, datetime.now()
    store.touch()
    audit_phi(request, user, action="approve", resource_type="prep_translation", resource_id=f"{key}:{lang}")
    return translation.model_dump(mode="json")
