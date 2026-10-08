"""System 18 · Billing & Claims QA API."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from app.core.auth import audit_phi, require_roles
from app.core.models import Role, StaffUser
from app.core.store import Store, get_store
from app.modules.billing import service

router = APIRouter(prefix="/api/billing", tags=["billing"])

BILLING = require_roles(Role.OPERATIONS_MANAGER, Role.ADMIN)


def _rows(store: Store, now: datetime) -> list[dict]:
    items = service.work(store)
    rows = []
    for f in service.reconcile(store, now):
        appt = store.appointments[f["appointment_id"]]
        item = items.get(f["id"])
        rows.append({
            **f, "kind_label": service.KINDS[f["kind"]], "status": item.status if item else "open",
            "outcome": item.outcome if item else None, "note": item.note if item else "",
            "resolved_by": item.resolved_by if item else None, "history": item.history if item else [],
            "service_date": appt.start.date().isoformat(), "site_id": appt.site_id,
            "patient_name": store.patients[appt.patient_id].full_name, "exam_name": store.exams[appt.exam_code].name,
            "appointment_status": appt.status,
            "claims": [service.claims(store)[c].model_dump(mode="json") for c in f["claim_ids"]],
        })
    rows.sort(key=lambda r: (r["status"] != "open", r["service_date"]))
    return rows


@router.get("/overview")
def overview(request: Request, user: StaffUser = Depends(BILLING)):
    store, now = get_store(), datetime.now()
    rows = _rows(store, now)
    all_claims = list(service.claims(store).values())
    kinds = {k: {"label": v, "open": 0, "resolved": 0, "at_stake": 0.0} for k, v in service.KINDS.items()}
    for r in rows:
        kinds[r["kind"]][r["status"]] += 1
        if r["status"] == "open":
            kinds[r["kind"]]["at_stake"] = round(kinds[r["kind"]]["at_stake"] + r["at_stake"], 2)
    audit_phi(request, user, action="read", resource_type="billing_discrepancies", resource_id=None)
    return {
        "discrepancies": rows, "kinds": kinds, "outcomes": service.OUTCOMES,
        "claims": {"total": len(all_claims), "paid": sum(c.status == "paid" for c in all_claims),
                   "submitted": sum(c.status == "submitted" for c in all_claims),
                   "rejected": sum(c.status == "rejected" for c in all_claims),
                   "billed": round(sum(c.amount for c in all_claims), 2)},
        "fees": [{"exam_code": e, "exam_name": store.exams[e].name, "fee_code": c, "description": d, "amount": a}
                 for e, (c, d, a) in service.FEES.items()],
        "window_days": service.WINDOW_DAYS, "submission_days": service.SUBMISSION_DAYS,
    }


class Resolution(BaseModel):
    outcome: str
    note: str = ""


def _known(store: Store, item_id: str) -> None:
    if item_id not in {f["id"] for f in service.reconcile(store, datetime.now())}:
        raise HTTPException(404, "No such discrepancy")


@router.post("/discrepancies/{item_id}/resolve")
def resolve(item_id: str, body: Resolution, request: Request, user: StaffUser = Depends(BILLING)):
    store = get_store()
    _known(store, item_id)
    if body.outcome not in service.OUTCOMES:
        raise HTTPException(422, "Unknown outcome")
    if body.outcome in ("written_off", "no_action") and len(body.note.strip()) < 5:
        raise HTTPException(422, "Explain why in the note")
    item = service.resolve(store, item_id, body.outcome, body.note.strip(), user.name, datetime.now())
    store.touch()
    audit_phi(request, user, action="update", resource_type="billing_discrepancy", resource_id=item_id)
    return item.model_dump(mode="json")


@router.post("/discrepancies/{item_id}/reopen")
def reopen(item_id: str, request: Request, user: StaffUser = Depends(BILLING)):
    store = get_store()
    _known(store, item_id)
    item = service.reopen(store, item_id, user.name, datetime.now())
    store.touch()
    audit_phi(request, user, action="update", resource_type="billing_discrepancy", resource_id=item_id)
    return item.model_dump(mode="json")


@router.get("/export.csv")
def export(request: Request, status: str | None = None, user: StaffUser = Depends(BILLING)):
    rows = [r for r in _rows(get_store(), datetime.now()) if not status or r["status"] == status]
    audit_phi(request, user, action="export", resource_type="billing_discrepancies", resource_id=None)
    return Response(service.to_csv(rows), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="billing-discrepancies-{datetime.now():%Y%m%d}.csv"'})
