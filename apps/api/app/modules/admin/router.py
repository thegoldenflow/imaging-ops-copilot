"""Auth, demo controls, audit log viewer, AI usage dashboard and mock settings."""

import csv
import io
from collections import defaultdict
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from app.core.audit import EVENT_TYPES as AUDIT_EVENT_TYPES
from app.core.audit import mrn_hash
from app.core.auth import CLINICAL_STAFF, client_ip, current_user, issue_token, require_roles
from app.core.config import settings
from app.core.models import Role, StaffUser
from app.core.store import get_store, reset_store
from app.integrations.contract import dead_letters
from app.integrations.mocks import ADAPTERS, MOCK_CONFIG, MockServiceConfig, adapter
from app.llm.gateway import get_gateway

router = APIRouter(prefix="/api")


@router.get("/health")
def health():
    store = get_store()
    return {"status": "ok", "llm_mode": get_gateway().mode, "appointments": len(store.appointments)}


@router.get("/auth/users")
def demo_users():
    return {
        "users": [u.model_dump() for u in get_store().staff.values() if u.demo_login],
        "passcode_required": settings.demo_passcode is not None,
    }


class LoginRequest(BaseModel):
    user_id: str
    passcode: str | None = None


@router.post("/auth/login")
def login(body: LoginRequest, request: Request):
    store = get_store()
    if settings.demo_passcode and body.passcode != settings.demo_passcode:
        raise HTTPException(status_code=401, detail="Wrong passcode")
    user = store.staff.get(body.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Unknown user")
    store.audit.record(user_id=user.id, user_name=user.name, role=user.role, action="login",
                       resource_type="session", resource_id=None,
                       source_ip=request.client.host if request.client else None, reason="demo login")
    return {"token": issue_token(user.id), "user": user.model_dump()}


@router.get("/auth/me")
def me(user: StaffUser = Depends(current_user)):
    return user.model_dump()


@router.get("/meta")
def meta(user: StaffUser = Depends(current_user)):
    store = get_store()
    return {
        "llm_mode": get_gateway().mode,
        "version": store.version,
        "now": datetime.now().isoformat(timespec="minutes"),
        "sites": [s.model_dump() for s in store.sites.values()],
        "scanners": [s.model_dump() for s in store.scanners.values()],
        "exams": [e.model_dump() for e in store.exams.values()],
    }


@router.get("/version")
def version():
    return {"version": get_store().version}


@router.post("/demo/reset")
def reset_demo(user: StaffUser = Depends(current_user)):
    # Tokens survive the reset because staff ids are stable.
    store = reset_store()
    store.audit.record(user_id=user.id, user_name=user.name, role=user.role, action="reset",
                       resource_type="demo_data", resource_id=None, reason="demo reset")
    return {"ok": True, "version": store.version}


def _audit_filters(outcome: str | None, event_type: str | None, module: str | None, mrn: str | None,
                   user_id: str | None) -> dict:
    return {"outcome": outcome or None, "event_type": event_type or None, "module": module or None,
            "patient_mrn_hash": mrn_hash(mrn.strip()) if mrn and mrn.strip() else None, "user_id": user_id or None}


@router.get("/admin/audit")
def audit_log(limit: int = 200, outcome: str | None = None, event_type: str | None = None, module: str | None = None,
              mrn: str | None = None, user_id: str | None = None,
              user: StaffUser = Depends(require_roles(Role.ADMIN, Role.MEDICAL_DIRECTOR))):
    """The audit log, newest first; filters by outcome, 6.3 event type, module, patient (MRN, matched
    through its keyed hash) and user."""
    audit = get_store().audit
    filters = _audit_filters(outcome, event_type, module, mrn, user_id)
    events = audit.query(**filters, limit=limit, newest_first=True)
    total = len(audit.query(**filters)) if any(filters.values()) else len(audit.events())
    return {"events": [e.model_dump() for e in events], "total": total, "event_types": list(AUDIT_EVENT_TYPES)}


@router.get("/admin/audit/export")
def audit_export(request: Request, outcome: str | None = None, event_type: str | None = None,
                 module: str | None = None, mrn: str | None = None, user_id: str | None = None,
                 user: StaffUser = Depends(require_roles(Role.ADMIN, Role.MEDICAL_DIRECTOR))):
    """The matching events as CSV (append-only log: export, never edit). The export itself is an `export` event."""
    store = get_store()
    filters = _audit_filters(outcome, event_type, module, mrn, user_id)
    events = store.audit.query(**filters)
    buffer = io.StringIO()
    fields = ["seq", "ts", "event_type", "action", "user_id", "user_name", "role", "resource_type", "resource_id",
              "patient_mrn_hash", "encounter_id", "module", "prompt_version", "outcome", "reason", "hash"]
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for e in events:
        writer.writerow(e.model_dump())
    store.audit.record(user_id=user.id, user_name=user.name, role=user.role, action="export", event_type="export",
                       resource_type="AuditEvent", resource_id=None, source_ip=client_ip(request), module="audit",
                       reason=f"{len(events)} events; filters: " + ", ".join(f"{k}={v}" for k, v in filters.items()
                                                                            if v and k != "patient_mrn_hash")
                       + (" + patient" if filters["patient_mrn_hash"] else ""))
    return Response(buffer.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="audit-log.csv"'})


@router.get("/admin/audit/verify")
def audit_verify(user: StaffUser = Depends(require_roles(Role.ADMIN, Role.MEDICAL_DIRECTOR))):
    intact, broken_at = get_store().audit.verify()
    return {"intact": intact, "broken_at_seq": broken_at, "events": len(get_store().audit.events())}


@router.get("/admin/ai-usage")
def ai_usage(user: StaffUser = Depends(require_roles(Role.ADMIN, Role.OPERATIONS_MANAGER, Role.MEDICAL_DIRECTOR))):
    calls = get_store().llm_calls
    by_task: dict[str, dict] = defaultdict(lambda: {"calls": 0, "failures": 0, "latency_ms": 0, "cost_usd": 0.0,
                                                    "input_tokens": 0, "output_tokens": 0})
    for c in calls:
        agg = by_task[c.task]
        agg["calls"] += 1
        agg["failures"] += c.outcome in ("needs_human", "unavailable")
        agg["latency_ms"] += c.latency_ms
        agg["cost_usd"] += c.cost_usd
        agg["input_tokens"] += c.input_tokens
        agg["output_tokens"] += c.output_tokens
    tasks = [
        {
            "task": task,
            "calls": a["calls"],
            "failure_rate": a["failures"] / a["calls"],
            "avg_latency_ms": round(a["latency_ms"] / a["calls"]),
            "cost_usd": round(a["cost_usd"], 4),
            "input_tokens": a["input_tokens"],
            "output_tokens": a["output_tokens"],
        }
        for task, a in sorted(by_task.items())
    ]
    return {
        "mode": get_gateway().mode,
        "tasks": tasks,
        "recent": [c.model_dump() for c in reversed(calls[-50:])],
    }


@router.get("/admin/mocks")
def mock_settings(user: StaffUser = Depends(require_roles(*CLINICAL_STAFF))):
    return {name: cfg.model_dump() for name, cfg in MOCK_CONFIG.items()}


@router.put("/admin/mocks/{service}")
def update_mock(service: str, body: MockServiceConfig, user: StaffUser = Depends(require_roles(Role.ADMIN))):
    if service not in MOCK_CONFIG:
        raise HTTPException(status_code=404, detail="Unknown service")
    MOCK_CONFIG[service] = body
    adapter(service).breaker.reset()  # a changed setting gets a fresh circuit
    return body.model_dump()


@router.get("/admin/integrations")
def integrations(user: StaffUser = Depends(require_roles(Role.ADMIN))):
    """Adapter health (circuit state, policy) and the dead-letter queue, without message contents."""
    letters = sorted(dead_letters(get_store()).values(), key=lambda d: d.ts, reverse=True)
    return {"adapters": [a.health() for a in ADAPTERS.values()],
            "dead_letters": [d.model_dump(mode="json", exclude={"payload"}) for d in letters[:100]],
            "dead_letter_count": len(letters)}
