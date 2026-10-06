"""System 6 · Protocol Assignment Assistant.

Retrieval narrows the protocol library to a few candidates; Claude picks a
primary and two alternatives with a rationale. A radiologist approves or
changes it; the approved protocol's duration sets the booking slot length."""

import re
from collections import Counter
from datetime import datetime

from pydantic import BaseModel

from app.core.store import Store
from app.llm.prompts import Prompt
from app.llm.providers import mock_fixture
from app.modules.protocols.library import BY_ID, Protocol


class ProtocolOutput(BaseModel):
    primary_protocol_id: str
    alternative_protocol_ids: list[str]
    rationale: str
    contrast_required: bool


class ProtocolRecord(BaseModel):
    requisition_id: str
    candidates: list[dict]  # [{id, name, score}]
    primary_id: str
    alternative_ids: list[str]
    rationale: str
    contrast_required: bool
    ai_status: str  # ok, needs_human, unavailable, seeded
    llm_call_id: str | None
    model: str
    approved_id: str | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None
    change_reason: str | None = None
    suggested_at: datetime

    @property
    def effective_id(self) -> str:
        return self.approved_id or self.primary_id


PROTOCOL_PROMPT = Prompt(
    name="protocol_suggest",
    version="protocol_suggest@1",
    system=(
        "You suggest an imaging protocol for an outpatient requisition. Choose only from the candidate protocols "
        "given, by id. Return the best match as primary and the next two as alternatives, explain the choice in "
        "one or two sentences, and say whether the primary needs IV contrast. A radiologist approves every choice."
    ),
    template="Requested exam: {exam}\nClinical information: {clinical}\n\nCANDIDATES:\n{candidates}",
)


@mock_fixture("protocol_suggest")
def _mock(text: str, images: list, attempt: int) -> dict:
    ids = re.findall(r"^- ([A-Z]{2}-[A-Z0-9-]+):", text, re.M)
    primary = BY_ID[ids[0]]
    return {
        "primary_protocol_id": primary.id,
        "alternative_protocol_ids": ids[1:3],
        "rationale": f"Best match for the stated indication among {len(ids)} retrieved {primary.modality} protocols.",
        "contrast_required": primary.contrast,
    }


def format_candidates(candidates: list[tuple[Protocol, float]]) -> str:
    return "\n".join(
        f"- {p.id}: {p.name}; contrast: {'yes' if p.contrast else 'no'}; {p.minutes} min; "
        f"indications: {', '.join(p.indications)}"
        for p, _ in candidates
    )


def records(store: Store) -> dict[str, ProtocolRecord]:
    return store.module("protocols", dict)


def stats(store: Store) -> dict:
    approved = [r for r in records(store).values() if r.approved_id]
    if not approved:
        return {"approved": 0, "adoption_rate": None, "top3_rate": None, "common_changes": [], "by_protocol": []}
    adopted = sum(r.approved_id == r.primary_id for r in approved)
    top3 = sum(r.approved_id in [r.primary_id, *r.alternative_ids] for r in approved)
    changes = Counter((r.primary_id, r.approved_id) for r in approved if r.approved_id != r.primary_id)
    by_protocol = Counter(r.approved_id for r in approved)
    return {
        "approved": len(approved),
        "adoption_rate": round(adopted / len(approved), 3),
        "top3_rate": round(top3 / len(approved), 3),
        "common_changes": [
            {"from": a, "from_name": BY_ID[a].name, "to": b, "to_name": BY_ID[b].name, "count": n}
            for (a, b), n in changes.most_common(8)
        ],
        "by_protocol": [{"id": pid, "name": BY_ID[pid].name, "count": n} for pid, n in by_protocol.most_common()],
    }
