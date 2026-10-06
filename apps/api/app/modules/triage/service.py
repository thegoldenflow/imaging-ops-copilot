"""System 5 · Priority Triage.

Claude assigns P1-P4 with a rationale and red flags from the shared extraction.
Each tier has a target wait (configurable); the queue is ordered by days left
to target. A radiologist can override with a mandatory reason, which is audited
and feeds the agreement statistics."""

import json
import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.core.store import Store
from app.llm.prompts import Prompt
from app.llm.providers import mock_fixture


class TriageOutput(BaseModel):
    priority: Literal["P1", "P2", "P3", "P4"]
    rationale: str
    red_flags: list[str]


class TriageConfig(BaseModel):
    """Target wait in days per tier. Demo placeholders, set by the medical director."""

    P1: int = Field(2, ge=0)
    P2: int = Field(7, ge=0)
    P3: int = Field(30, ge=0)
    P4: int = Field(60, ge=0)


class TriageRecord(BaseModel):
    requisition_id: str
    ai_priority: str | None
    ai_rationale: str
    ai_red_flags: list[str]
    ai_status: str  # ok, needs_human, unavailable, seeded
    llm_call_id: str | None
    model: str
    final_priority: str
    review_action: str | None = None  # confirmed, overridden
    override_reason: str | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    triaged_at: datetime


TRIAGE_PROMPT = Prompt(
    name="requisition_triage",
    version="requisition_triage@1",
    system=(
        "You triage outpatient imaging requisitions for scheduling priority. "
        "P1: must be imaged within 48 hours (e.g. suspected DVT, cauda equina symptoms). "
        "P2: within 1 week (suspected cancer, new seizure, postmenopausal bleeding, significant red flags). "
        "P3: within 1 month (symptomatic, no red flags). P4: routine or follow-up (chronic, stable, surveillance). "
        "List red flags as short phrases quoted from the request. A radiologist reviews every decision."
    ),
    template="Extracted requisition fields (JSON):\n{fields}",
)

P1_WORDS = r"\bdvt\b|deep vein thrombosis|cauda equina|saddle (numbness|anesthesia|anaesthesia)|urinary retention"
P4_FOLLOWUP = r"follow-up|surveillance|known (small )?(\w+ )?nodule"
P2_WORDS = (r"nodule|weight loss|metasta\w*|malignan\w*|seizure|postmenopausal bleeding|\bmass\b|cancer|melanoma|"
            r"hemoptysis|focal weakness")
P4_WORDS = r"chronic|osteoarthritis|memory|mechanical|tension-type|stairs|vertigo|dizziness"


def _value(item) -> str:
    return item.get("value", "") if isinstance(item, dict) else (item or "")


def baseline_triage(fields: dict) -> dict:
    """Accepts full extraction fields or the values-only form sent in the prompt."""
    text = " ".join([
        _value(fields.get("requested_exam")),
        _value(fields.get("clinical_indication")),
        " ".join(_value(h) for h in fields.get("relevant_history", [])),
    ]).lower()
    urgent = _value(fields.get("stated_urgency")) == "urgent"
    if re.search(P1_WORDS, text):
        flags = sorted({m.group(0) for m in re.finditer(P1_WORDS, text)})
        return {"priority": "P1", "rationale": "Findings that need imaging within 48 hours.", "red_flags": flags}
    if re.search(P4_FOLLOWUP, text):
        return {"priority": "P4", "rationale": "Planned follow-up or surveillance of a known finding.", "red_flags": []}
    flags = sorted({m.group(0) for m in re.finditer(P2_WORDS, text)})
    if flags:
        return {"priority": "P2", "rationale": "Red flags suggesting possible serious disease.", "red_flags": flags}
    if re.search(P4_WORDS, text) and not urgent:
        return {"priority": "P4", "rationale": "Chronic or stable symptoms without red flags.", "red_flags": []}
    if urgent:
        return {"priority": "P2", "rationale": "Referrer marked the request urgent; no explicit red flags found.", "red_flags": []}
    return {"priority": "P3", "rationale": "Symptomatic without red flags.", "red_flags": []}


@mock_fixture("requisition_triage")
def _mock(text: str, images: list, attempt: int) -> dict:
    return baseline_triage(json.loads(text.split("\n", 1)[1]))


def config(store: Store) -> TriageConfig:
    return store.module("triage_config", TriageConfig)


def records(store: Store) -> dict[str, TriageRecord]:
    return store.module("triage", dict)


def days_left(store: Store, record: TriageRecord, received_at: datetime, now: datetime | None = None) -> float:
    now = now or datetime.now()
    target = getattr(config(store), record.final_priority)
    return round(target - (now - received_at).total_seconds() / 86400, 1)


def agreement(store: Store) -> dict:
    """Model vs radiologist, over requisitions a radiologist confirmed or overrode."""
    reviewed = [r for r in records(store).values() if r.ai_priority and r.review_action]
    if not reviewed:
        return {"reviewed": 0, "agreement": None, "over_triaged": 0, "under_triaged": 0, "overrides": []}
    overrides = [r for r in reviewed if r.review_action == "overridden"]
    return {
        "reviewed": len(reviewed),
        "agreement": round(sum(r.ai_priority == r.final_priority for r in reviewed) / len(reviewed), 3),
        "over_triaged": sum(1 for r in overrides if r.ai_priority < r.final_priority),  # AI more urgent
        "under_triaged": sum(1 for r in overrides if r.ai_priority > r.final_priority),  # AI less urgent
        "overrides": [r.model_dump(mode="json") for r in overrides],
    }
