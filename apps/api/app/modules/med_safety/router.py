"""Medication safety API (spec 7.2): the pharmacist workbench's queue, order reviews, reconciliations, signing,
and the discharge medication table for the discharge summary (7.3).

Pharmacists see medication order reviews and every reconciliation hospital-wide;
physicians see the reviews of their units' patients (laboratory and imaging orders
are theirs) and co-sign reconciliations. Everything about a patient is read as the
signed-in user (6.3: unit scope, break-glass), decisions are audited.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.agents import approvals
from app.core.auth import client_ip, require_roles
from app.core.models import Role, StaffUser
from app.core.store import get_store
from app.ehr.gateway import Actor, FhirGateway
from app.modules.med_safety import discharge, service
from app.modules.med_safety.models import get_review

router = APIRouter(prefix="/api/med-safety")
WORKBENCH = (Role.PHARMACIST, Role.PHYSICIAN)
DISCHARGE_READERS = (Role.PHARMACIST, Role.PHYSICIAN, Role.NURSE)


def _errors(fn):
    try:
        return fn()
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except (service.DecisionDenied, approvals.DecisionDenied) as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except (service.DecisionProblem, approvals.DecisionError) as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


class ReviewDecision(BaseModel):
    decision: str = Field(pattern="^(accept_order|request_change)$")
    reason: str = Field(max_length=1000)


class ItemDecision(BaseModel):
    action: str = Field(pattern="^(accept|modify|reject)$")
    reason: str = Field(max_length=1000)
    change: str | None = Field(default=None, max_length=1000)


class BulkAccept(BaseModel):
    reason: str = Field(max_length=1000)


class StartReconciliation(BaseModel):
    kind: str = Field(pattern="^(admission|discharge)$")


@router.get("/queue")
def queue(show: str = Query("open", pattern="^(open|all|done)$"),
          user: StaffUser = Depends(require_roles(*WORKBENCH))):
    """The workbench queue: blocking first, then severity, then the longest wait."""
    return _errors(lambda: service.queue(get_store(), user, show=show))


@router.get("/reviews/{review_id}")
def review(review_id: str, request: Request, user: StaffUser = Depends(require_roles(*WORKBENCH))):
    return _errors(lambda: service.review_view(get_store(), user, review_id, request))


@router.post("/reviews/{review_id}/commentary")
def commentary(review_id: str, user: StaffUser = Depends(require_roles(*WORKBENCH))):
    """Run the AI commentary now (it normally runs in the background; on demand for unflagged tests)."""
    store = get_store()

    def run():
        found = get_review(store, review_id)
        if found is None:
            raise LookupError(f"No order review {review_id}")
        if str(user.role) not in found.reviewer_roles:
            raise service.DecisionDenied("Not your review")
        service.comment(store, review_id)
        return service.review_view(store, user, review_id)
    return _errors(run)


@router.post("/reviews/{review_id}/decision")
def decide(review_id: str, body: ReviewDecision, request: Request,
           user: StaffUser = Depends(require_roles(*WORKBENCH))):
    store = get_store()
    return _errors(lambda: (service.decide_review(store, user, review_id, body.decision, body.reason,
                                                  source_ip=client_ip(request)),
                            service.review_view(store, user, review_id, request))[1])


@router.get("/encounters/{encounter_id}")
def encounter(encounter_id: str, request: Request, user: StaffUser = Depends(require_roles(*WORKBENCH))):
    return _errors(lambda: service.encounter_view(get_store(), user, encounter_id, request))


@router.post("/encounters/{encounter_id}/medrec")
def start_medrec(encounter_id: str, body: StartReconciliation, request: Request,
                 user: StaffUser = Depends(require_roles(Role.PHARMACIST))):
    """Open (or return) the encounter's admission or discharge reconciliation."""
    store = get_store()

    def run():
        FhirGateway(Actor.of(user, request), service.MEDREC).read("Encounter", encounter_id)  # 6.3 scope and audit
        session = service.reconcile(store, encounter_id, body.kind)
        return service.session_view(store, user, session.id, request)
    return _errors(run)


@router.get("/encounters/{encounter_id}/discharge-medications")
def discharge_medications(encounter_id: str, request: Request, final_only: bool = True,
                          user: StaffUser = Depends(require_roles(*DISCHARGE_READERS))):
    """The discharge medication table (new / changed / stopped / continued) of a signed discharge reconciliation;
    404 when there is none (yet). The interface the discharge summary (7.3) reads."""
    fhir = FhirGateway(Actor.of(user, request), service.MEDREC)
    table = discharge.discharge_medication_table(fhir, encounter_id, final_only=final_only)
    if table is None:
        raise HTTPException(status_code=404, detail="No signed discharge medication reconciliation for this encounter")
    return table.model_dump(mode="json", by_alias=True)


@router.get("/medrec/{session_id}")
def medrec(session_id: str, request: Request, user: StaffUser = Depends(require_roles(*WORKBENCH))):
    return _errors(lambda: service.session_view(get_store(), user, session_id, request))


@router.post("/medrec/{session_id}/items/{item_id}")
def handle(session_id: str, item_id: str, body: ItemDecision, request: Request,
           user: StaffUser = Depends(require_roles(*WORKBENCH))):
    store = get_store()
    return _errors(lambda: (service.handle_item(store, user, session_id, item_id, body.action, body.reason,
                                                body.change, source_ip=client_ip(request)),
                            service.session_view(store, user, session_id, request))[1])


@router.post("/medrec/{session_id}/accept-hospital-use")
def accept_hospital_use(session_id: str, body: BulkAccept, request: Request,
                        user: StaffUser = Depends(require_roles(*WORKBENCH))):
    store = get_store()
    return _errors(lambda: (service.accept_hospital_use(store, user, session_id, body.reason,
                                                        source_ip=client_ip(request)),
                            service.session_view(store, user, session_id, request))[1])


@router.post("/medrec/{session_id}/sign")
def sign(session_id: str, request: Request, user: StaffUser = Depends(require_roles(*WORKBENCH))):
    """The pharmacist signs when every item is handled; a physician co-signs (the signing service, `sign` events)."""
    store = get_store()
    return _errors(lambda: (service.sign(store, user, session_id, request),
                            service.session_view(store, user, session_id, request))[1])


@router.get("/evidence")
def evidence(request: Request, ref: list[str] = Query(default_factory=list, max_length=40),
             user: StaffUser = Depends(require_roles(*WORKBENCH))):
    """'View evidence': FHIR references read as the user, guideline sections from knowledge/guidelines."""
    return {"evidence": service.evidence(user, ref, request)}


@router.get("/rules")
def rule_tables(user: StaffUser = Depends(require_roles(*WORKBENCH))):
    return service.rules_view()
