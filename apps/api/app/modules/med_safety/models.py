"""The review queue's records (spec 7.2): order reviews and medication reconciliations.

Two tables (migration 0008): `order_reviews`, one row per reviewed order (key: the order reference, so an order is
reviewed once), and `medrec_sessions`, one row per encounter and kind (admission, discharge). Free text that may
carry a patient's details (the AI commentary, the reconciliation items with the pharmacist's reasons, decisions) is
encrypted at rest. The FHIR side of each record: the AI commentary's `ai-review` Task (its focus is the order) and
the reconciliation's DocumentReference (docStatus preliminary until the signing service makes it final).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.core.store import Store

REVIEWS = "order_reviews"
SESSIONS = "medrec_sessions"
ReviewStatus = Literal["pending", "clear", "resolved"]
CommentaryStatus = Literal["pending", "on_demand", "done", "unavailable", "refused", "guard", "skipped"]


class OrderReview(BaseModel):
    id: str  # ORV-00001
    order_ref: str  # MedicationRequest/... or ServiceRequest/...
    kind: Literal["medication", "service"]
    patient_id: str
    encounter_id: str | None = None
    unit_id: str | None = None
    order: dict  # what the queue shows: name, text, category, authored, requester, status
    authored_at: datetime | None = None
    reviewed_at: datetime  # hospital clock
    reviewer_roles: list[str]  # who decides: pharmacist (and physician) for medication orders, physician for tests
    findings: list[dict] = Field(default_factory=list)  # rule findings (rules.Finding.as_dict)
    severity: str = "none"  # the most severe rule finding or AI concern
    blocking: bool = False  # only rules block
    status: ReviewStatus = "clear"
    commentary_status: CommentaryStatus = "pending"
    commentary: dict | None = None  # the validated AI output and how it was made (encrypted)
    review_task_id: str | None = None  # the ai-review Task (submitOrderReview) a decision completes
    decision: dict | None = None  # {decision, reason, by, name, role, at, task_id} (encrypted)


class MedRecSession(BaseModel):
    id: str  # MR-00001
    encounter_id: str
    patient_id: str
    kind: Literal["admission", "discharge"]
    unit_id: str | None = None
    status: Literal["open", "pharmacist_signed", "final"] = "open"
    created_at: datetime  # hospital clock
    updated_at: datetime
    severity: str = "none"  # the most severe discrepancy still waiting
    items: list[dict] = Field(default_factory=list)  # reconcile.RecItem dumps (encrypted: reasons are free text)
    ai_status: CommentaryStatus = "pending"  # the indication judgements of the medication_reconciliation agent
    ai_detail: dict | None = None  # run id, evaluated flag, problems, model status
    document_id: str | None = None  # the MedRec DocumentReference
    signatures: list[dict] = Field(default_factory=list)  # [{role, by, name, at}]
    change_tasks: list[str] = Field(default_factory=list)  # prescriber Tasks for "modify" items
    discharge_table: dict | None = None  # {new, changed, stopped, continued} once a discharge MedRec is signed


def reviews(store: Store) -> dict:
    return store.module(REVIEWS, dict)


def sessions(store: Store) -> dict:
    return store.module(SESSIONS, dict)


def review_key(order_ref: str) -> str:
    return order_ref


def session_key(encounter_id: str, kind: str) -> str:
    return f"{encounter_id}:{kind}"


def _by_id(table, value: str):
    if hasattr(table, "find_by"):  # a database table: an indexed lookup
        found = table.find_by("id", value)
        return found[0] if found else None
    return next((r for r in table.values() if r.id == value), None)


def get_review(store: Store, review_id: str) -> OrderReview | None:
    return _by_id(reviews(store), review_id)


def get_session(store: Store, session_id: str) -> MedRecSession | None:
    return _by_id(sessions(store), session_id)
