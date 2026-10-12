"""System 12 · Critical Results Tracker.

A confirmed finding that needs communication opens a case. A worker (the
in-process loop, standing in for Temporal) works each open case through the
policy for its level: notify the ordering physician, re-notify by phone and
fax, then escalate to the medical director. An acknowledgement must say who,
when and how; a case cannot be closed without one. Every step lands on the
case timeline."""

import random
from datetime import datetime, timedelta

from pydantic import BaseModel, Field

from app.core.models import MessageOutbox
from app.core.store import Store
from app.integrations.mocks import adapter

ACK_METHODS = {"phone": "Phone read-back", "fax": "Fax confirmation", "in_person": "In person",
               "portal": "Referrer portal", "secure_message": "Secure message"}


class LevelPolicy(BaseModel):
    label: str
    real_world: str  # what a clinic would typically configure
    renotify_after_s: int = Field(gt=0)
    escalate_after_s: int = Field(gt=0)


def _default_levels() -> dict[str, LevelPolicy]:
    # Demo timings are compressed to seconds so escalation is visible live.
    return {
        "critical": LevelPolicy(label="Critical (Level 1)", real_world="Read-back by phone within 60 minutes",
                                renotify_after_s=20, escalate_after_s=45),
        "urgent": LevelPolicy(label="Urgent (Level 2)", real_world="Ordering physician informed within 24 hours",
                              renotify_after_s=60, escalate_after_s=150),
        "significant": LevelPolicy(label="Significant unexpected (Level 3)", real_world="Communicated within 7 days",
                                   renotify_after_s=180, escalate_after_s=420),
    }


class Policy(BaseModel):
    levels: dict[str, LevelPolicy] = Field(default_factory=_default_levels)
    escalate_to_user_id: str = "U-MD"
    updated_by: str | None = None
    updated_at: datetime | None = None


class Acknowledgement(BaseModel):
    by_name: str
    by_role: str  # ordering physician, covering physician, medical director, ...
    method: str  # key of ACK_METHODS
    at: datetime
    recorded_by: str


class CriticalCase(BaseModel):  # FHIR Communication
    id: str
    report_id: str
    study_id: str | None
    patient_id: str
    referrer_id: str
    finding: str
    level: str
    created_at: datetime
    opened_by: str
    status: str = "open"  # open, escalated, acknowledged, closed
    step: int = 0  # 0 nothing sent, 1 notified, 2 re-notified, 3 escalated
    next_action_at: datetime | None = None
    ack_due_at: datetime
    acknowledgement: Acknowledgement | None = None
    closed_at: datetime | None = None
    closed_by: str | None = None
    close_note: str | None = None
    events: list[dict] = []


class CaseError(Exception):
    pass


def policy(store: Store) -> Policy:
    return store.module("critical_policy", Policy)


def cases(store: Store) -> dict[str, CriticalCase]:
    return store.module("critical_cases", dict)


def _log(case: CriticalCase, kind: str, text: str, actor: str, at: datetime) -> None:
    case.events.append({"ts": at.isoformat(timespec="seconds"), "kind": kind, "text": text, "actor": actor})


def queue_message(store: Store, *, channel: str, kind: str, to: str, body: str, patient_id: str, now: datetime) -> None:
    """Outbox entry on the given store (the seed builds a store that is not global yet)."""
    msg = MessageOutbox(id=store.next_id("MSG"), channel=channel, kind=kind, patient_id=patient_id, to=to,
                        language="en", body=body, scheduled_for=now)
    store.outbox[msg.id] = msg


def _phone_ok(case: CriticalCase, to: str) -> bool:
    """One live call attempt (correlation id = case id); the escalation policy owns the retries."""
    return adapter("phone").call("call", {"to": to, "kind": "critical_result"}, correlation_id=case.id,
                                 attempts=1, dead_letter=False).ok


def open_case(store: Store, report, finding: str, level: str, opened_by: str,
              now: datetime | None = None) -> CriticalCase:
    if level not in policy(store).levels:
        level = "urgent"
    now = now or datetime.now()
    pol = policy(store).levels[level]
    case = CriticalCase(
        id=store.next_id("CR"), report_id=report.id, study_id=report.study_id, patient_id=report.patient_id,
        referrer_id=report.referrer_id, finding=finding, level=level, created_at=now, opened_by=opened_by,
        next_action_at=now, ack_due_at=now + timedelta(seconds=pol.escalate_after_s),
    )
    _log(case, "opened", f"Case opened from report {report.id}: {finding} ({pol.label})", opened_by, now)
    cases(store)[case.id] = case
    advance(store, case, now)  # first notification goes out immediately
    store.touch()
    return case


def _message(store: Store, case: CriticalCase) -> str:
    patient = store.patients[case.patient_id]
    label = policy(store).levels[case.level].label
    return (f"Imaging Ops Copilot: {label} result for your patient {patient.full_name} (DOB {patient.dob:%Y-%m-%d}): "
            f"{case.finding}. Please call the reading room to acknowledge. Case {case.id}.")


def advance(store: Store, case: CriticalCase, now: datetime) -> None:
    """Run the next step of the notification policy for an open case."""
    pol = policy(store).levels[case.level]
    ref = store.referrers[case.referrer_id]
    body = _message(store, case)
    if case.step == 0:
        queue_message(store, channel="phone", kind="critical_result", to=ref.phone, body=body, patient_id=case.patient_id, now=now)
        if _phone_ok(case, ref.phone):
            _log(case, "notify", f"Called {ref.name} at {ref.phone} (mock): message left with clinic staff", "system", now)
        else:
            _log(case, "notify", f"Call to {ref.name} failed (phone service unavailable); will retry", "system", now)
        case.step, case.next_action_at = 1, case.created_at + timedelta(seconds=pol.renotify_after_s)
    elif case.step == 1:
        queue_message(store, channel="phone", kind="critical_result", to=ref.phone, body=body, patient_id=case.patient_id, now=now)
        queue_message(store, channel="fax", kind="critical_result", to=ref.fax, body=body, patient_id=case.patient_id, now=now)
        _log(case, "renotify", f"No acknowledgement after {pol.renotify_after_s}s: called {ref.name} again and faxed {ref.fax} (mock)",
             "system", now)
        case.step, case.next_action_at = 2, case.ack_due_at
    elif case.step == 2:
        director = store.staff.get(policy(store).escalate_to_user_id)
        name = director.name if director else "Medical director"
        queue_message(store, channel="phone", kind="critical_escalation", to=f"{name} (on call)", body=body, patient_id=case.patient_id, now=now)
        _log(case, "escalate", f"Not acknowledged within {pol.escalate_after_s}s: escalated to {name}, medical director", "system", now)
        case.status, case.step, case.next_action_at = "escalated", 3, None
    store.touch()


def process_due(store: Store, now: datetime | None = None) -> int:
    now = now or datetime.now()
    done = 0
    for case in cases(store).claim_due("next_action_at", now, statuses=("open", "escalated")):
        while case.status in ("open", "escalated") and case.next_action_at and case.next_action_at <= now:
            advance(store, case, now)
            done += 1
    return done


def acknowledge(store: Store, case: CriticalCase, *, by_name: str, by_role: str, method: str, recorded_by: str,
                at: datetime | None = None) -> CriticalCase:
    if case.status == "closed":
        raise CaseError("Case is closed")
    if case.acknowledgement:
        raise CaseError("Already acknowledged")
    if method not in ACK_METHODS:
        raise CaseError(f"Unknown method; use one of {', '.join(ACK_METHODS)}")
    if not by_name.strip() or not by_role.strip():
        raise CaseError("Record who acknowledged and their role")
    at = at or datetime.now()
    case.acknowledgement = Acknowledgement(by_name=by_name.strip(), by_role=by_role.strip(), method=method, at=at,
                                           recorded_by=recorded_by)
    case.status, case.next_action_at = "acknowledged", None
    _log(case, "acknowledged", f"Acknowledged by {by_name} ({by_role}) via {ACK_METHODS[method].lower()}", recorded_by, at)
    store.touch()
    return case


def close(store: Store, case: CriticalCase, *, by: str, note: str, now: datetime | None = None) -> CriticalCase:
    if case.status == "closed":
        raise CaseError("Already closed")
    if case.acknowledgement is None:
        raise CaseError("A case cannot be closed without a recorded acknowledgement")
    now = now or datetime.now()
    case.status, case.closed_at, case.closed_by, case.close_note = "closed", now, by, note.strip() or None
    _log(case, "closed", f"Closed{': ' + note.strip() if note.strip() else ''}", by, now)
    store.touch()
    return case


def is_overdue(case: CriticalCase, now: datetime) -> bool:
    return case.acknowledgement is None and now > case.ack_due_at

# ---------- Seed ----------

FINDINGS = {
    "CT_CHEST_C": [("Acute pulmonary embolism, right lower lobe segmental arteries", "critical"),
                   ("Spiculated 2.4 cm left upper lobe mass", "significant")],
    "CT_CHEST": [("New 1.8 cm right lower lobe nodule", "significant")],
    "CT_HEAD": [("Acute subdural haematoma, 8 mm, left convexity", "critical")],
    "CT_ABD_PEL": [("Free intraperitoneal air", "critical"), ("Enhancing 3 cm renal mass, left kidney", "significant")],
    "MR_BRAIN": [("Ring-enhancing lesion with vasogenic oedema, right frontal lobe", "urgent")],
    "MR_LSPINE": [("Cauda equina compression from large L4-5 disc extrusion", "critical")],
    "US_VENOUS": [("Occlusive deep vein thrombosis, left femoral vein", "urgent")],
    "US_ABD": [("Acute cholecystitis with gallbladder wall thickening", "urgent")],
}


def seed(s: Store, rng: random.Random, now: datetime) -> None:
    """Closed cases with full timelines, one acknowledged and still open, and one
    fresh critical case nobody acknowledges, so escalation happens live."""
    from app.modules.reports.service import reports

    signed = sorted((r for r in reports(s).values() if r.status == "signed" and r.signed_at
                     and s.studies[r.study_id].exam_code in FINDINGS), key=lambda r: r.signed_at)
    pol = policy(s)
    picks = rng.sample(signed[:-40], k=min(9, max(0, len(signed) - 40)))
    for i, report in enumerate(sorted(picks, key=lambda r: r.signed_at)):
        study = s.studies[report.study_id]
        finding, level = rng.choice(FINDINGS[study.exam_code])
        ref = s.referrers[report.referrer_id]
        t0 = report.signed_at
        case = CriticalCase(id=s.next_id("CR"), report_id=report.id, study_id=study.id, patient_id=report.patient_id,
                            referrer_id=report.referrer_id, finding=finding, level=level, created_at=t0,
                            opened_by=report.signed_by or "", step=1,
                            ack_due_at=t0 + timedelta(seconds=pol.levels[level].escalate_after_s))
        _log(case, "opened", f"Case opened from report {report.id}: {finding} ({pol.levels[level].label})", case.opened_by, t0)
        _log(case, "notify", f"Called {ref.name} at {ref.phone} (mock): message left with clinic staff", "system", t0)
        escalated = i == 3
        t = t0 + timedelta(minutes=rng.randint(8, 25))
        if escalated:
            _log(case, "renotify", f"No acknowledgement: called {ref.name} again and faxed {ref.fax} (mock)", "system", t)
            t += timedelta(minutes=20)
            _log(case, "escalate", "Not acknowledged in time: escalated to Dr. Daniel Okafor, medical director", "system", t)
            t += timedelta(minutes=12)
            by, role, method = "Dr. Daniel Okafor", "Medical director (covering)", "phone"
        else:
            by, role, method = ref.name, "Ordering physician", rng.choice(["phone", "phone", "fax", "portal"])
        case.acknowledgement = Acknowledgement(by_name=by, by_role=role, method=method, at=t, recorded_by=case.opened_by)
        _log(case, "acknowledged", f"Acknowledged by {by} ({role}) via {ACK_METHODS[method].lower()}", case.opened_by, t)
        case.step, case.status = (3 if escalated else 1), "acknowledged"
        if i < len(picks) - 1:
            t += timedelta(minutes=rng.randint(5, 90))
            case.status, case.closed_at, case.closed_by = "closed", t, case.opened_by
            case.close_note = "Follow-up arranged by the ordering physician"
            _log(case, "closed", f"Closed: {case.close_note}", case.opened_by, t)
        cases(s)[case.id] = case

    # Live demo case: opened as the demo starts and deliberately left unacknowledged.
    today = [r for r in signed[-40:] if s.studies[r.study_id].exam_code == "CT_CHEST_C"] or signed[-1:]
    if today:
        open_case(s, today[-1], "Acute pulmonary embolism, right lower lobe segmental arteries", "critical",
                  opened_by=today[-1].signed_by or "Radiologist", now=datetime.now())  # real clock: seed time is truncated
