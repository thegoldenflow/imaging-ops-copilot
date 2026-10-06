"""Requisition intake pipeline: extraction once, then triage and protocol
suggestion, then (after approval) waitlist or booking."""

import json
from datetime import datetime, timedelta
from typing import Callable

from pydantic import BaseModel

from app.core.models import ACTIVE_STATUSES, Requisition, WaitlistEntry
from app.core.store import Store
from app.llm.gateway import get_gateway
from app.modules.protocols import service as protocols
from app.modules.protocols.library import BY_ID, Protocol, detect_modality, retrieve
from app.modules.requisitions.extraction import (
    EXTRACT_PROMPT,
    Extraction,
    baseline_extract,
    low_confidence_fields,
)
from app.modules.scheduling import service as scheduling
from app.modules.triage import service as triage


class ExtractionRecord(BaseModel):
    requisition_id: str
    fields: dict
    ai_status: str  # ok, needs_human, unavailable, seeded
    llm_call_id: str | None
    model: str
    prompt_version: str
    low_confidence: list[str]
    corrections: list[dict] = []
    extracted_at: datetime


# Called with (store, requisition) after AI processing finishes (MRI screening hooks in here).
PROCESSED_HOOKS: list[Callable[[Store, Requisition], None]] = []


def extractions(store: Store) -> dict[str, ExtractionRecord]:
    return store.module("extractions", dict)


def empty_fields() -> dict:
    blank = {"value": "", "source_quote": "", "confidence": 0.0}
    return {"requested_exam": blank, "clinical_indication": blank, "relevant_history": [], "prior_imaging": [],
            "allergies": [], "renal_or_diabetes": [], "implant_hints": [],
            "stated_urgency": {"value": "unspecified", "source_quote": "", "confidence": 0.0}}


def values_only(fields: dict) -> dict:
    return {k: ([i["value"] for i in v] if isinstance(v, list) else v["value"]) for k, v in fields.items()}


def clinical_text(fields: dict) -> str:
    parts = [fields["clinical_indication"]["value"], *[h["value"] for h in fields["relevant_history"]]]
    return " ".join(p for p in parts if p)


def protocol_for(store: Store, req_id: str) -> Protocol | None:
    rec = protocols.records(store).get(req_id)
    return BY_ID.get(rec.effective_id) if rec else None


def process(store: Store, req: Requisition) -> None:
    """Run extraction, triage and protocol suggestion through the LLM gateway."""
    req.status = "processing"
    store.touch()
    patient = store.patients[req.patient_id]
    gateway = get_gateway()
    now = datetime.now()

    ext = gateway.structured(task="requisition_extract", prompt=EXTRACT_PROMPT, variables={"text": req.text},
                             schema_cls=Extraction, tier="fast", patients=[patient])
    fields = ext.data if ext.status == "ok" else empty_fields()
    extractions(store)[req.id] = ExtractionRecord(
        requisition_id=req.id, fields=fields, ai_status=ext.status, llm_call_id=ext.call_id, model=ext.model,
        prompt_version=ext.prompt_version, low_confidence=low_confidence_fields(fields), extracted_at=now,
    )

    tri = gateway.structured(task="requisition_triage", prompt=triage.TRIAGE_PROMPT,
                             variables={"fields": json.dumps(values_only(fields))},
                             schema_cls=triage.TriageOutput, tier="reasoning", patients=[patient])
    data = tri.data or {"priority": None, "rationale": "AI triage unavailable; needs manual triage.", "red_flags": []}
    triage.records(store)[req.id] = triage.TriageRecord(
        requisition_id=req.id, ai_priority=data["priority"], ai_rationale=data["rationale"],
        ai_red_flags=data["red_flags"], ai_status=tri.status, llm_call_id=tri.call_id, model=tri.model,
        final_priority=data["priority"] or "P3", triaged_at=datetime.now(),
    )

    exam = fields["requested_exam"]["value"]
    candidates = retrieve(exam, clinical_text(fields))
    if not candidates:  # unknown modality: fall back to the whole library ranking
        candidates = retrieve("", f"{exam} {clinical_text(fields)}")
    pro = gateway.structured(
        task="protocol_suggest", prompt=protocols.PROTOCOL_PROMPT,
        variables={"exam": exam or "not stated", "clinical": clinical_text(fields) or "not stated",
                   "candidates": protocols.format_candidates(candidates)},
        schema_cls=protocols.ProtocolOutput, tier="reasoning", patients=[patient],
    )
    candidate_ids = [p.id for p, _ in candidates]
    status = pro.status
    if pro.status == "ok" and pro.data["primary_protocol_id"] in candidate_ids:
        primary = pro.data["primary_protocol_id"]
        alternatives = [a for a in pro.data["alternative_protocol_ids"] if a in candidate_ids and a != primary][:2]
        rationale, contrast = pro.data["rationale"], pro.data["contrast_required"]
    else:  # unusable answer: fall back to retrieval order and ask a person
        status = "needs_human" if pro.status == "ok" else pro.status
        primary, alternatives = candidate_ids[0], candidate_ids[1:3]
        rationale, contrast = "Retrieval ranking only; AI suggestion unavailable.", BY_ID[primary].contrast
    protocols.records(store)[req.id] = protocols.ProtocolRecord(
        requisition_id=req.id, candidates=[{"id": p.id, "name": p.name, "score": s} for p, s in candidates],
        primary_id=primary, alternative_ids=alternatives, rationale=rationale, contrast_required=contrast,
        ai_status=status, llm_call_id=pro.call_id, model=pro.model, suggested_at=datetime.now(),
    )
    req.status = "ready"
    for hook in PROCESSED_HOOKS:
        hook(store, req)
    store.touch()


def process_pending(store: Store) -> int:
    pending = [r for r in store.requisitions.values() if r.status == "received"]
    for req in pending:
        try:
            process(store, req)
        except Exception:
            req.status = "failed"
            raise
    return len(pending)


def seed_processed(store: Store, req: Requisition, labels: dict, reviewed: bool, reviewer: str) -> None:
    """Pre-process a seeded requisition with the rule-based baseline (no LLM call)."""
    when = req.received_at + timedelta(minutes=2)
    fields = baseline_extract(req.text)
    extractions(store)[req.id] = ExtractionRecord(
        requisition_id=req.id, fields=fields, ai_status="seeded", llm_call_id=None, model="baseline-rules",
        prompt_version="baseline", low_confidence=low_confidence_fields(fields), extracted_at=when,
    )
    t = triage.baseline_triage(fields)
    rec = triage.TriageRecord(
        requisition_id=req.id, ai_priority=t["priority"], ai_rationale=t["rationale"], ai_red_flags=t["red_flags"],
        ai_status="seeded", llm_call_id=None, model="baseline-rules", final_priority=t["priority"], triaged_at=when,
    )
    candidates = retrieve(fields["requested_exam"]["value"], clinical_text(fields))
    ids = [p.id for p, _ in candidates]
    prot = protocols.ProtocolRecord(
        requisition_id=req.id, candidates=[{"id": p.id, "name": p.name, "score": s} for p, s in candidates],
        primary_id=ids[0], alternative_ids=ids[1:3], rationale="Seeded with the retrieval baseline.",
        contrast_required=BY_ID[ids[0]].contrast, ai_status="seeded", llm_call_id=None, model="baseline-rules",
        suggested_at=when,
    )
    if reviewed:
        rec.reviewed_by, rec.reviewed_at = reviewer, when + timedelta(hours=3)
        if t["priority"] == labels["priority"]:
            rec.review_action = "confirmed"
        else:
            rec.review_action, rec.final_priority = "overridden", labels["priority"]
            rec.override_reason = "Radiologist correction (seed data)"
        prot.approved_id, prot.approved_by, prot.approved_at = labels["protocol_id"], reviewer, when + timedelta(hours=3)
        if prot.approved_id != prot.primary_id:
            prot.change_reason = "Better fit for the indication (seed data)"
        req.status = "approved"
    else:
        req.status = "ready"
    triage.records(store)[req.id] = rec
    protocols.records(store)[req.id] = prot


# ---------- After approval: waitlist or book ----------

def add_to_waitlist(store: Store, req: Requisition) -> WaitlistEntry:
    protocol = protocol_for(store, req.id)
    rec = triage.records(store)[req.id]
    sites = [s.id for s in store.sites.values() if protocol.modality in s.modalities]
    entry = WaitlistEntry(
        id=store.next_id("WL"), patient_id=req.patient_id, referrer_id=req.referrer_id, exam_code=protocol.exam_code,
        urgency=rec.final_priority, acceptable_site_ids=sites, earliest_date=datetime.now().date(),
        added_at=req.received_at, notes=f"From requisition {req.id}", requisition_id=req.id, protocol_id=protocol.id,
        duration_minutes=protocol.minutes,
    )
    store.waitlist[entry.id] = entry
    req.status, req.waitlist_id = "waitlisted", entry.id
    store.touch()
    return entry


def next_free_slot(store: Store, protocol: Protocol, after: datetime | None = None) -> tuple[str, datetime] | None:
    """Earliest gap of the protocol's length on any scanner of the right modality."""
    exam = store.exams[protocol.exam_code]
    start_from = max(after or datetime.now(), datetime.now() + timedelta(hours=exam.prep_hours))
    best: tuple[str, datetime] | None = None
    for scanner in store.scanners.values():
        if scanner.modality != protocol.modality:
            continue
        site = store.sites[scanner.site_id]
        busy = sorted((a.start, a.end) for a in store.appointments.values()
                      if a.scanner_id == scanner.id and a.status in ACTIVE_STATUSES and a.end > start_from)
        for offset in range(0, 21):
            day = (start_from + timedelta(days=offset)).date()
            if day.weekday() not in site.open_days:
                continue
            t = datetime.combine(day, datetime.min.time()) + timedelta(hours=site.open_hour)
            close = datetime.combine(day, datetime.min.time()) + timedelta(hours=site.close_hour)
            found = None
            while t + timedelta(minutes=protocol.minutes) <= close:
                end = t + timedelta(minutes=protocol.minutes)
                if t >= start_from and not any(s < end and e > t for s, e in busy):
                    found = t
                    break
                t += timedelta(minutes=15)
            if found:
                if best is None or found < best[1]:
                    best = (scanner.id, found)
                break
    return best


def book_next(store: Store, req: Requisition):
    protocol = protocol_for(store, req.id)
    slot = next_free_slot(store, protocol)
    if slot is None:
        return None
    scanner_id, start = slot
    appt = scheduling.book(
        store, patient_id=req.patient_id, referrer_id=req.referrer_id, scanner_id=scanner_id,
        exam_code=protocol.exam_code, start=start, urgency=triage.records(store)[req.id].final_priority,
        duration_minutes=protocol.minutes, requisition_id=req.id, protocol_id=protocol.id,
    )
    req.status, req.appointment_id = "booked", appt.id
    store.touch()
    return appt


def modality_of(store: Store, req_id: str) -> str | None:
    protocol = protocol_for(store, req_id)
    if protocol:
        return str(protocol.modality)
    rec = extractions(store).get(req_id)
    found = detect_modality(rec.fields["requested_exam"]["value"]) if rec else None
    return str(found) if found else None
