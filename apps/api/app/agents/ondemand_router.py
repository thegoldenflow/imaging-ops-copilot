"""AI on demand API: what the AI did in this demo, and batches a person asks for (app/agents/ondemand.py).

- `GET /api/ai/activity?scope=session|all`: the AI actions of this browser's demo session (the `X-Demo-Session`
  header), or everyone's since the last reset, with what is running and queued now and the totals.
- `POST /api/ai/batch`: "Ask AI for these N" on a list page; the items are queued for the AI job loop and the
  answer says how long it should take. Items the person may not see, or whose AI has run, are left out.
- `GET /api/ai/kinds`: per kind of item, its typical time and model calls (to state a batch's time before it runs).

Any signed-in person may read their session's activity; the activity holds ids and labels, no patient details.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.agents import activity, ondemand
from app.core.auth import current_user
from app.core.context import request_origin
from app.core.models import StaffUser
from app.core.store import get_store

router = APIRouter(prefix="/api/ai", tags=["ai"])


@router.get("/activity")
def ai_activity(scope: str = Query("session", pattern="^(session|all)$"), user: StaffUser = Depends(current_user)):
    origin = request_origin()
    return activity.view(get_store(), session=origin.session if origin else None, scope=scope)


class BatchItem(BaseModel):
    kind: str
    id: str


class Batch(BaseModel):
    items: list[BatchItem] = Field(min_length=1, max_length=ondemand.BATCH_MAX)


@router.post("/batch")
def ai_batch(body: Batch, user: StaffUser = Depends(current_user)):
    store = get_store()
    by_kind: dict[str, list[str]] = {}
    for item in body.items:
        if item.kind not in ondemand.KINDS:
            raise HTTPException(status_code=422, detail=f"No AI item kind {item.kind!r}")
        by_kind.setdefault(item.kind, []).append(item.id)
    parts = [ondemand.batch(store, user, kind, ids) for kind, ids in by_kind.items()]
    queued = sum(p["queued"] for p in parts)
    return {"queued": queued, "skipped": sum(p["skipped"] for p in parts), "calls": sum(p["calls"] for p in parts),
            "expected_s": round(sum(p["typical_s"] * p["queued"] for p in parts) / ondemand.CONCURRENCY, 1),
            "parts": parts}


@router.get("/kinds")
def ai_kinds(user: StaffUser = Depends(current_user)):
    return {"concurrency": ondemand.CONCURRENCY, "batch_max": ondemand.BATCH_MAX, "background_ai": "on_demand"
            if ondemand.on_demand() else "eager",
            "kinds": {name: {"noun": k.noun, "calls": k.calls, "typical_s": ondemand.typical_s(name),
                             "running_text": k.running_text, "steps": list(k.steps)}
                      for name, k in ondemand.KINDS.items()}}
