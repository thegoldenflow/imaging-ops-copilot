"""Documentation API (spec 7.3): worklist, drafts, review and edits, signing, delivery.

Physicians draft, edit and sign discharge summaries; nurses review and sign the
bilingual patient instructions and confirm shift handoffs. Everything is read and
written as the signed-in person (6.3 unit scope, break-glass, audit); the AI runs as
the registered agents for that person.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.core.auth import require_roles
from app.core.models import Role, StaffUser
from app.core.store import get_store
from app.modules.documentation import service

router = APIRouter(prefix="/api/documentation")
CLINICIANS = (Role.PHYSICIAN, Role.NURSE)


def _errors(fn):
    """Module errors to HTTP (FhirAccessDenied and break-glass go to the app's own handlers)."""
    try:
        return fn()
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except service.DocDenied as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except service.DocProblem as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


class Draft(BaseModel):
    force: bool = False


class Content(BaseModel):
    content: dict


class Delivery(BaseModel):
    channel: str = Field(pattern="^(print|sms|portal)$")


@router.get("/worklist")
def worklist(request: Request, user: StaffUser = Depends(require_roles(*CLINICIANS))):
    return _errors(lambda: {"items": service.worklist(get_store(), user, request)})


@router.get("/demo")
def demo(user: StaffUser = Depends(require_roles(*CLINICIANS))):
    """The planted demo cases (encounter ids only; opening them needs the usual access)."""
    return service.demo_cases()


@router.get("/encounters/{encounter_id}")
def encounter(encounter_id: str, request: Request, user: StaffUser = Depends(require_roles(*CLINICIANS))):
    return _errors(lambda: service.encounter_view(get_store(), user, encounter_id, request))


@router.post("/encounters/{encounter_id}/discharge-summary")
def draft_summary(encounter_id: str, body: Draft, request: Request,
                  user: StaffUser = Depends(require_roles(Role.PHYSICIAN))):
    return _errors(lambda: service.draft_summary(get_store(), user, encounter_id, force=body.force, request=request))


@router.post("/encounters/{encounter_id}/patient-instructions")
def draft_instructions(encounter_id: str, body: Draft, request: Request,
                       user: StaffUser = Depends(require_roles(Role.NURSE))):
    return _errors(lambda: service.draft_instructions(get_store(), user, encounter_id, force=body.force,
                                                      request=request))


@router.post("/encounters/{encounter_id}/handoff")
def draft_handoff(encounter_id: str, body: Draft, request: Request,
                  user: StaffUser = Depends(require_roles(Role.NURSE))):
    return _errors(lambda: service.draft_handoff(get_store(), user, encounter_id, force=body.force, request=request))


@router.get("/documents/{doc_id}")
def document(doc_id: str, request: Request, user: StaffUser = Depends(require_roles(*CLINICIANS))):
    return _errors(lambda: service.document_view(get_store(), user, doc_id, request))


@router.put("/documents/{doc_id}")
def save(doc_id: str, body: Content, request: Request, user: StaffUser = Depends(require_roles(*CLINICIANS))):
    return _errors(lambda: service.save(get_store(), user, doc_id, body.content, request))


@router.post("/documents/{doc_id}/sign")
def sign(doc_id: str, request: Request, user: StaffUser = Depends(require_roles(*CLINICIANS))):
    return _errors(lambda: service.sign(get_store(), user, doc_id, request))


@router.post("/documents/{doc_id}/deliver")
def deliver(doc_id: str, body: Delivery, request: Request, user: StaffUser = Depends(require_roles(Role.NURSE))):
    return _errors(lambda: service.deliver(get_store(), user, doc_id, body.channel, request))


@router.get("/portal/{doc_id}")
def portal(doc_id: str, request: Request, user: StaffUser = Depends(require_roles(*CLINICIANS))):
    return _errors(lambda: service.portal_view(get_store(), user, doc_id, request))
