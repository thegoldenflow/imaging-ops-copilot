"""AI on demand API: what the AI did in this demo, and batches a person asks for (app/agents/ondemand.py).

- `GET /api/ai/activity?scope=session|all`: the AI actions of this browser's demo session (the `X-Demo-Session`
  header), or everyone's since the last reset (operations roles), with what is running and queued now and the
  totals. Session ids are never returned. Reading everyone's is audited.
- `POST /api/ai/batch`: "Ask AI for these N" on a list page; the items are queued for the AI job loop and the
  answer says how long it should take. Items the person may not ask for (audited), or whose AI has run, are left
  out.
- `GET /api/ai/kinds`: per kind of item, its typical time and model calls (to state a batch's time before it runs).

Any signed-in person may read their session's activity; the activity holds ids and labels, no patient details.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.agents import activity, ondemand
from app.core.auth import audit_phi, client_ip, current_user, deny
from app.core.context import request_origin
from app.core.models import StaffUser
from app.core.store import get_store

router = APIRouter(prefix="/api/ai", tags=["ai"])


ALL_SCOPE = ("operations_manager", "medical_director", "admin")  # everyone's actions: the people who run the demo


@router.get("/activity")
def ai_activity(request: Request, scope: str = Query("session", pattern="^(session|all)$"),
                user: StaffUser = Depends(current_user)):
    """This browser's AI actions (any signed-in person), or everyone's since the reset (operations roles: it names
    other people and their items; the read is audited). A browser's own actions are items its person opened, whose
    reads were audited then; the header polls them from every page, so they are not audited again."""
    if scope == "all" and str(user.role) not in ALL_SCOPE:
        deny(request, user, resource_type="ai_activity", resource_id="all")
    origin = request_origin()
    view = activity.view(get_store(), session=origin.session if origin else None, scope=scope)
    if scope == "all":
        audit_phi(request, user, action="read", resource_type="ai_activity", resource_id="all")
    return view


class BatchItem(BaseModel):
    kind: str
    id: str


class Batch(BaseModel):
    items: list[BatchItem] = Field(min_length=1, max_length=ondemand.BATCH_MAX)


@router.post("/batch")
def ai_batch(body: Batch, request: Request, user: StaffUser = Depends(current_user)):
    """Queue the AI of several items (a list page's "Ask AI for these N"). Items the person may not ask for (the
    kind's role and 6.3 scope checks) are left out, and each refusal is audited."""
    store = get_store()
    by_kind: dict[str, list[str]] = {}
    for item in body.items:
        if item.kind not in ondemand.KIND_MODULES:
            raise HTTPException(status_code=422, detail=f"No AI item kind {item.kind!r}")
        by_kind.setdefault(item.kind, []).append(item.id)
    parts = [ondemand.batch(store, user, kind, ids) for kind, ids in by_kind.items()]
    for part in parts:
        for item_id in part["denied"]:
            store.audit.record(user_id=user.id, user_name=user.name, role=str(user.role), action="post",
                               resource_type=f"ai_{part['kind']}", resource_id=item_id, outcome="denied",
                               source_ip=client_ip(request), reason="batch: not this person's to ask for")
    queued = sum(p["queued"] for p in parts)
    return {"queued": queued, "skipped": sum(p["skipped"] for p in parts),
            "denied": sum(len(p["denied"]) for p in parts), "calls": sum(p["calls"] for p in parts),
            "expected_s": round(sum(p["typical_s"] * p["queued"] for p in parts) / ondemand.CONCURRENCY, 1),
            "parts": [{k: v for k, v in p.items() if k != "denied"} for p in parts]}


@router.get("/kinds")
def ai_kinds(user: StaffUser = Depends(current_user)):
    return {"concurrency": ondemand.CONCURRENCY, "batch_max": ondemand.BATCH_MAX, "background_ai": "on_demand"
            if ondemand.on_demand() else "eager",
            "kinds": {name: {"noun": k.noun, "calls": k.calls, "typical_s": ondemand.typical_s(name),
                             "running_text": k.running_text, "steps": list(k.steps)}
                      for name, k in ondemand.KINDS.items()}}
