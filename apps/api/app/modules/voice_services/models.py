"""The voice services' own records (spec 7.4): call-centre calls and post-discharge follow-up calls.

Only text is kept: the browser turns speech into text and text into speech (Web Speech), the server sees
transcripts, never audio. Transcripts, dialog state, answers, quotes and summaries are encrypted at rest
(app/core/db/schema.py: tables `voice_calls` and `followup_calls`, migration 0010). The EHR side of a call (the
Communication, the patient-reported Observations, the Tasks, the proposed Appointments) is written through the Tool
Gateway and FhirGateway; these rows hold the conversation and point at those resources.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class Turn(BaseModel):
    role: Literal["caller", "agent"]
    text: str
    at: datetime = Field(default_factory=datetime.now)
    lang: str = "en"
    intent: str | None = None  # call centre: what the caller's turn was recognised as
    source: str | None = None  # model / rules
    question: str | None = None  # follow-up: the question key an agent turn asked / a caller turn answered
    gloss: str | None = None  # an agent turn in Chinese: the same hand-written reply in English (phrases.Said)


class HospitalCall(BaseModel):
    """An inbound call to the hospital call centre, run in the console by a member of staff playing the caller."""

    id: str  # VC-00001
    started_at: datetime
    ended_at: datetime | None = None
    hospital_time: str | None = None  # the hospital clock when the call started (ISO)
    staff_user_id: str | None = None  # who ran the console (the agent acts as itself, not for them)
    language: str = "en"  # en, zh-Hans, zh-Hant
    patient_id: str | None = None  # set once the identity check passed
    caller_relation: str | None = None  # "daughter", "son", ... when someone calls for the patient
    identity_failures: int = 0
    unrecognised: int = 0  # turns in a row without a recognised intent
    state: dict = Field(default_factory=dict)  # the dialog's pending step and its data
    transcript: list[Turn] = Field(default_factory=list)
    intents: list[dict] = Field(default_factory=list)  # [{intent, source, confidence, turn, topic}]
    actions: list[dict] = Field(default_factory=list)  # [{action, refs, status, detail}]
    outcome: str = "in_progress"  # in_progress, resolved, requested (waiting for a clerk), transferred, abandoned
    transfer_reason: str | None = None
    summary: str | None = None
    run_ids: list[str] = Field(default_factory=list)
    model_status: str | None = None  # the last intent step: ok, unavailable (rules), not_used


class FollowupCall(BaseModel):
    """The follow-up call of one discharged stay (one per encounter: scheduling twice finds this row)."""

    id: str  # FU-<encounter>
    encounter_id: str
    patient_id: str
    unit_id: str | None = None  # the unit the patient left from (whose nurses follow up)
    discharged_at: datetime | None = None  # hospital time
    due_at: datetime | None = None  # hospital time (discharge + the configured delay)
    source: str = "sweep"  # sweep (no journey), journey (the workflow's step), demo
    workflow_id: str | None = None
    status: str = "scheduled"  # manual, scheduled, in_progress, completed, no_answer, not_confirmed
    missing: list[str] = Field(default_factory=list)  # discharge, consent, phone, instructions
    reason: str | None = None
    language: str = "en"  # en, zh-Hans, zh-Hant (the patient's communication.language)
    instructions_ref: str | None = None
    script: list[dict] = Field(default_factory=list)  # the questions: {key, kind, code, text{en, zh-Hans, zh-Hant}}
    step: int = 0  # the question being asked
    retries: int = 0  # unclear answers to the current question
    dob_failures: int = 0
    transcript: list[Turn] = Field(default_factory=list)
    answers: dict = Field(default_factory=dict)  # question key -> {answer: yes/no/unclear/text, text}
    rule_hits: list[dict] = Field(default_factory=list)  # redflags.Hit as dicts (with the patient's words)
    classifier: dict | None = None  # {status, escalate, reason, confidence, summary, symptoms, quotes}
    escalated: bool = False
    emergency: bool = False
    escalated_by: str | None = None  # rules, model, low_confidence, model_unavailable
    priority: str | None = None  # stat (emergency), urgent (red flag), routine (review, manual)
    summary: str | None = None
    communication_id: str | None = None
    observation_ids: list[str] = Field(default_factory=list)
    task_id: str | None = None  # the nurse Task: escalation or manual follow-up
    review_status: str = "none"  # none, open, closed, referred (the nurse's handling)
    nurse_actions: list[dict] = Field(default_factory=list)  # [{kind, note, by, role, at, communication_id, task_id}]
    run_ids: list[str] = Field(default_factory=list)
    # hospital time, as the discharge and the due time (followup.py sets them; the defaults are for a bare model)
    created_at: datetime = Field(default_factory=datetime.now)
    started_at: datetime | None = None
    ended_at: datetime | None = None
    updated_at: datetime = Field(default_factory=datetime.now)  # the queue sorts by it: how long an item has waited
