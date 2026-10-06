"""Auth, demo controls, audit log viewer, AI usage dashboard and mock settings."""

from collections import defaultdict
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.core.auth import CLINICAL_STAFF, current_user, issue_token, require_roles
from app.core.config import settings
from app.core.models import Role, StaffUser
from app.core.store import get_store, reset_store
from app.integrations.mocks import MOCK_CONFIG, MockServiceConfig
from app.llm.gateway import get_gateway

router = APIRouter(prefix="/api")


@router.get("/health")
def health():
    store = get_store()
    return {"status": "ok", "llm_mode": get_gateway().mode, "appointments": len(store.appointments)}


@router.get("/auth/users")
def demo_users():
    return {
        "users": [u.model_dump() for u in get_store().staff.values()],
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


@router.get("/admin/audit")
def audit_log(limit: int = 200, outcome: str | None = None,
              user: StaffUser = Depends(require_roles(Role.ADMIN, Role.MEDICAL_DIRECTOR))):
    events = get_store().audit.events()
    if outcome:
        events = [e for e in events if e.outcome == outcome]
    return {"events": [e.model_dump() for e in reversed(events[-limit:])], "total": len(events)}


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
    return body.model_dump()
