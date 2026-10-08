"""System 21 · Inspection Readiness Hub API."""

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.core.auth import CLINICAL_STAFF, audit_phi, require_roles
from app.core.models import Role, StaffUser
from app.core.store import Store, get_store
from app.modules.inspection import service

router = APIRouter(prefix="/api/inspection", tags=["inspection"])

VIEWERS = require_roles(*CLINICAL_STAFF)
EDITORS = require_roles(Role.OPERATIONS_MANAGER, Role.MEDICAL_DIRECTOR, Role.ADMIN)


def doc_view(store: Store, doc: service.Document, full: bool = False) -> dict:
    today = datetime.now().date()
    cur = doc.current
    data = doc.model_dump(mode="json", exclude={"versions"})
    data |= {"status": service.due_status(doc.due, today), "days_to_due": (doc.due - today).days if doc.due else None,
             "version": cur.version if cur else None, "effective": cur.effective.isoformat() if cur else None,
             "versions_count": len(doc.versions),
             "site_name": store.sites[doc.site_id].name if doc.site_id else None}
    if full:
        data["versions"] = [v.model_dump(mode="json") for v in reversed(doc.versions)]
    return data


def _doc(doc_id: str) -> service.Document:
    doc = service.documents(get_store()).get(doc_id)
    if doc is None:
        raise HTTPException(404, "Document not found")
    return doc


@router.get("/overview")
def overview(user: StaffUser = Depends(VIEWERS)):
    store, now = get_store(), datetime.now()
    docs = list(service.documents(store).values())
    due = sorted((d for d in docs if d.due and d.kind in ("credential", "equipment", "policy")
                  and service.due_status(d.due, now.date()) in ("overdue", "due_soon", "upcoming")), key=lambda d: d.due)
    rems = sorted(service.reminders(store), key=lambda r: r["sent_at"], reverse=True)
    names = {d.id: d.title for d in docs}
    return {
        "checklist": service.checklist(store, now),
        "due": [doc_view(store, d) for d in due],
        "reminders": [{**r, "title": names.get(r["doc_id"], r["doc_id"])} for r in rems[:30]],
        "counts": {k: sum(d.kind == k for d in docs) for k in ("policy", "equipment", "credential", "qc_record")},
        "stages": service.REMINDER_STAGES,
    }


@router.get("/documents")
def list_documents(kind: str | None = None, user: StaffUser = Depends(VIEWERS)):
    store = get_store()
    docs = [d for d in service.documents(store).values() if not kind or d.kind == kind]
    docs.sort(key=lambda d: (d.kind, d.category, d.title))
    return {"documents": [doc_view(store, d) for d in docs]}


@router.get("/documents/{doc_id}")
def get_document(doc_id: str, user: StaffUser = Depends(VIEWERS)):
    return doc_view(get_store(), _doc(doc_id), full=True)


class Upload(BaseModel):
    title: str = Field(min_length=3)
    owner: str = Field(min_length=2)
    category: str = "Policy"
    version: str = "1.0"
    text: str = Field(min_length=20, max_length=60_000)


@router.post("/documents")
def upload(body: Upload, request: Request, user: StaffUser = Depends(EDITORS)):
    store = get_store()
    try:
        doc = service.add_policy(store, doc_id=None, title=body.title.strip(), category=body.category, owner=body.owner,
                                 text=body.text, version=body.version, effective=date.today(), change_note="Uploaded",
                                 by=user.name)
    except ValueError as e:
        raise HTTPException(422, str(e))
    store.touch()
    audit_phi(request, user, action="create", resource_type="document", resource_id=doc.id)
    return doc_view(store, doc, full=True)


class NewVersion(BaseModel):
    version: str = Field(min_length=1)
    change_note: str = Field(min_length=3)
    text: str = Field(min_length=20, max_length=60_000)


@router.post("/documents/{doc_id}/versions")
def new_version(doc_id: str, body: NewVersion, request: Request, user: StaffUser = Depends(EDITORS)):
    store = get_store()
    doc = _doc(doc_id)
    if doc.kind != "policy":
        raise HTTPException(409, "Only policies are versioned")
    if any(v.version == body.version for v in doc.versions):
        raise HTTPException(409, "That version already exists")
    service.add_policy(store, doc_id=doc.id, title=doc.title, category=doc.category, owner=doc.owner, text=body.text,
                       version=body.version, effective=date.today(), change_note=body.change_note, by=user.name)
    store.touch()
    audit_phi(request, user, action="update", resource_type="document", resource_id=doc.id)
    return doc_view(store, doc, full=True)


class Renewal(BaseModel):
    performed: date
    due: date
    result: str = ""


@router.put("/documents/{doc_id}/record")
def renew(doc_id: str, body: Renewal, request: Request, user: StaffUser = Depends(EDITORS)):
    """Record a renewed credential or a completed test; the next due date restarts reminders."""
    store = get_store()
    doc = _doc(doc_id)
    if doc.kind not in ("credential", "equipment"):
        raise HTTPException(409, "Only credentials and equipment records have due dates to renew")
    if body.due <= body.performed:
        raise HTTPException(422, "The due date must be after the date performed")
    doc.performed, doc.due, doc.result = body.performed, body.due, body.result or doc.result
    store.touch()
    audit_phi(request, user, action="update", resource_type="document", resource_id=doc.id)
    return doc_view(store, doc)


@router.post("/reminders/{reminder_id}/ack")
def ack(reminder_id: str, user: StaffUser = Depends(VIEWERS)):
    store = get_store()
    rem = next((r for r in service.reminders(store) if r["id"] == reminder_id), None)
    if rem is None:
        raise HTTPException(404, "Reminder not found")
    rem["acknowledged_by"] = user.name
    store.touch()
    return rem


class Question(BaseModel):
    question: str = Field(min_length=3, max_length=500)


@router.post("/ask")
def ask(body: Question, user: StaffUser = Depends(VIEWERS)):
    return service.ask(get_store(), body.question, user.name, datetime.now())


@router.get("/qa")
def qa_history(user: StaffUser = Depends(VIEWERS)):
    return {"entries": list(reversed(service.qa_log(get_store())[-20:]))}
