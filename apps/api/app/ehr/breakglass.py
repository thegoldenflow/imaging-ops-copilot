"""Break-glass emergency access (spec 6.3).

A physician or nurse who needs a patient outside their units asks for emergency
access with a reason (required, at least 10 characters). The grant opens that
patient to that user for 4 hours (wall clock: it is real access, not simulated
time); the web app shows a red banner while any grant is active. Every grant
writes a `break_glass` audit event and joins the admin review queue, due within
24 hours; the review (justified / not justified, with a note) is written back to
the audit log as a second `break_glass` event. Reads made under a grant name it
in their audit reason, so the reviewer sees what was opened.

Grants are rows in `break_glass_grants` (reason and review note encrypted).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel

from app.core.audit import mrn_hash
from app.core.store import Store

GRANT_HOURS = 4
REVIEW_HOURS = 24
MIN_REASON = 10
TABLE = "break_glass_grants"


class BreakGlassGrant(BaseModel):
    id: str
    user_id: str
    user_name: str
    role: str
    patient_id: str
    mrn_hash: str | None = None
    mrn: str = ""  # encrypted; shown to the user in the banner, not in the review queue
    reason: str
    granted_at: datetime
    expires_at: datetime
    review_due: datetime
    review_status: str = "pending"  # pending, justified, not_justified
    reviewed_by: str | None = None
    reviewed_by_name: str | None = None
    reviewed_at: datetime | None = None
    review_note: str = ""
    audit_seq: int | None = None

    def active(self, now: datetime | None = None) -> bool:
        return self.granted_at <= (now or datetime.now()) < self.expires_at


class BreakGlassError(ValueError):
    pass


def grants(store: Store):
    return store.module(TABLE, dict)


def active_grant(store: Store, user_id: str, patient_id: str, now: datetime | None = None) -> BreakGlassGrant | None:
    now = now or datetime.now()
    live = [g for g in grants(store).find_by("user_id", user_id) if g.patient_id == patient_id and g.active(now)]
    return max(live, key=lambda g: g.expires_at) if live else None


def active_for_user(store: Store, user_id: str, now: datetime | None = None) -> list[BreakGlassGrant]:
    now = now or datetime.now()
    return sorted((g for g in grants(store).find_by("user_id", user_id) if g.active(now)),
                  key=lambda g: g.expires_at)


def request_access(store: Store, *, user_id: str, user_name: str, role: str, patient_id: str, mrn: str | None,
                   reason: str, source_ip: str | None = None, now: datetime | None = None) -> BreakGlassGrant:
    """Open the patient to the user for GRANT_HOURS (the caller checks the role may break the glass)."""
    reason = " ".join((reason or "").split())
    if len(reason) < MIN_REASON:
        raise BreakGlassError(f"Give a reason of at least {MIN_REASON} characters")
    now = (now or datetime.now()).replace(microsecond=0)
    grant = BreakGlassGrant(
        id=store.next_id("BG"), user_id=user_id, user_name=user_name, role=str(role), patient_id=patient_id,
        mrn_hash=mrn_hash(mrn), mrn=mrn or "", reason=reason, granted_at=now, expires_at=now + timedelta(hours=GRANT_HOURS),
        review_due=now + timedelta(hours=REVIEW_HOURS))
    event = store.audit.record(
        user_id=user_id, user_name=user_name, role=role, action="break_glass", event_type="break_glass",
        resource_type="Patient", resource_id=patient_id, outcome="allowed", source_ip=source_ip,
        reason=f"{grant.id}: {reason}", patient_mrn_hash=grant.mrn_hash, module="break_glass")
    grant.audit_seq = event.seq
    grants(store)[grant.id] = grant
    return grant


def review(store: Store, grant_id: str, *, decision: Literal["justified", "not_justified"], note: str,
           reviewer_id: str, reviewer_name: str, reviewer_role: str, source_ip: str | None = None,
           now: datetime | None = None) -> BreakGlassGrant:
    table = grants(store)
    grant = table.get(grant_id)
    if grant is None:
        raise LookupError(f"No break-glass grant {grant_id}")
    if grant.review_status != "pending":
        raise BreakGlassError(f"{grant_id} was already reviewed ({grant.review_status})")
    if reviewer_id == grant.user_id:
        raise BreakGlassError("A user cannot review their own emergency access")
    now = (now or datetime.now()).replace(microsecond=0)
    reviewed = grant.model_copy(update={"review_status": decision, "reviewed_by": reviewer_id,
                                        "reviewed_by_name": reviewer_name, "reviewed_at": now,
                                        "review_note": (note or "").strip()})
    store.audit.record(
        user_id=reviewer_id, user_name=reviewer_name, role=reviewer_role, action="review", event_type="break_glass",
        resource_type="BreakGlassGrant", resource_id=grant.id, outcome=decision, source_ip=source_ip,
        reason=f"review of {grant.id} by {grant.user_name}: {decision}" + (f" ({reviewed.review_note})"
                                                                           if reviewed.review_note else ""),
        patient_mrn_hash=grant.mrn_hash, module="break_glass")
    table[grant.id] = reviewed
    return reviewed


def queue(store: Store, *, status: str | None = "pending", now: datetime | None = None) -> list[dict]:
    """Grants for the admin review queue, oldest first, with what was opened under each."""
    now = now or datetime.now()
    rows = [g for g in grants(store).values() if status in (None, "all") or g.review_status == status]
    rows.sort(key=lambda g: g.granted_at)
    out = []
    for g in rows:
        opened = store.audit.query(user_id=g.user_id, patient_mrn_hash=g.mrn_hash, since=g.granted_at,
                                   until=g.expires_at, event_type="read") if g.mrn_hash else []
        types: dict[str, int] = {}
        for e in opened:
            types[e.resource_type] = types.get(e.resource_type, 0) + 1
        out.append({**g.model_dump(mode="json", exclude={"mrn"}), "active": g.active(now), "overdue": g.review_status == "pending"
                    and now > g.review_due, "accessed": types, "accessed_count": len(opened)})
    return out
