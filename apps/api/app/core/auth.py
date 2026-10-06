"""Demo authentication and role/site authorization.

Demo mode logs in as one of the seeded staff users with a single click. Every
protected endpoint declares the roles it allows; a denied request returns 403
and is written to the audit log.
"""

import secrets

from fastapi import Depends, HTTPException, Request

from app.core.models import Role, StaffUser
from app.core.store import get_store

# token -> staff user id. Tokens live in memory and are dropped on restart.
_sessions: dict[str, str] = {}

ALL_ROLES = set(Role)
CLINICAL_STAFF = ALL_ROLES - {Role.REFERRER}


def issue_token(user_id: str) -> str:
    token = secrets.token_urlsafe(24)
    _sessions[token] = user_id
    return token


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def access_reason(request: Request) -> str:
    return request.headers.get("X-Access-Reason", "operations")


def current_user(request: Request) -> StaffUser:
    header = request.headers.get("Authorization", "")
    token = header.removeprefix("Bearer ").strip()
    user_id = _sessions.get(token)
    user = get_store().staff.get(user_id) if user_id else None
    if user is None:
        raise HTTPException(status_code=401, detail="Not signed in")
    return user


def require_roles(*roles: Role):
    """Dependency factory: allow only the given roles, audit denials."""
    allowed = set(roles)

    def checker(request: Request, user: StaffUser = Depends(current_user)) -> StaffUser:
        if user.role not in allowed:
            deny(request, user, resource_type=request.url.path, resource_id=None)
        return user

    return checker


def deny(request: Request, user: StaffUser, *, resource_type: str, resource_id: str | None):
    get_store().audit.record(
        user_id=user.id,
        user_name=user.name,
        role=user.role,
        action=request.method.lower(),
        resource_type=resource_type,
        resource_id=resource_id,
        outcome="denied",
        source_ip=client_ip(request),
        reason=access_reason(request),
    )
    raise HTTPException(status_code=403, detail="Not permitted for your role")


def ensure_site(request: Request, user: StaffUser, site_id: str, resource_type: str, resource_id: str):
    """Site-scoped staff may only touch records at their own sites."""
    if user.site_ids and site_id not in user.site_ids:
        deny(request, user, resource_type=resource_type, resource_id=resource_id)


def audit_phi(
    request: Request,
    user: StaffUser,
    *,
    action: str,
    resource_type: str,
    resource_id: str | None,
) -> None:
    get_store().audit.record(
        user_id=user.id,
        user_name=user.name,
        role=user.role,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        source_ip=client_ip(request),
        reason=access_reason(request),
    )
