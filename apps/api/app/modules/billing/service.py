"""System 18 · Billing & Claims QA.

Completed exams are reconciled one by one against submitted claims. Rules find
claims that were never submitted, duplicates, a fee code that does not match
the exam performed, a billed amount that differs from the fee table, rejected
claims and claims for exams that were never performed. Each finding becomes a
work item with an outcome and a trail. Fee codes are a synthetic table, not the
OHIP Schedule of Benefits."""

import csv
import io
import random
from datetime import datetime, timedelta

from pydantic import BaseModel

from app.core.models import AppointmentStatus
from app.core.store import Store

WINDOW_DAYS = 30
SUBMISSION_DAYS = 2  # a claim is expected within two days of the exam

# Synthetic fee table: exam code -> (fee code, description, amount in CAD).
FEES = {
    "MR_BRAIN": ("SYN-M101", "MRI head, technical", 412.00),
    "MR_LSPINE": ("SYN-M201", "MRI lumbar spine, technical", 398.00),
    "MR_KNEE": ("SYN-M301", "MRI extremity, technical", 365.00),
    "CT_CHEST_C": ("SYN-C112", "CT thorax with contrast, technical", 248.00),
    "CT_ABD_PEL": ("SYN-C212", "CT abdomen and pelvis with contrast, technical", 296.00),
    "CT_HEAD": ("SYN-C301", "CT head without contrast, technical", 178.00),
    "CT_CHEST": ("SYN-C111", "CT thorax without contrast, technical", 196.00),
    "US_ABD": ("SYN-U101", "Ultrasound abdomen, complete", 118.00),
    "US_PELVIS": ("SYN-U201", "Ultrasound pelvis", 104.00),
    "US_THYROID": ("SYN-U301", "Ultrasound neck / thyroid", 92.00),
    "US_VENOUS": ("SYN-U401", "Doppler venous, lower limb", 126.00),
    "XR_CHEST": ("SYN-X101", "Chest radiograph, 2 views", 34.50),
    "XR_KNEE": ("SYN-X201", "Knee radiograph, 3 views", 31.00),
    "XR_LSPINE": ("SYN-X301", "Lumbar spine radiograph, 2 views", 36.00),
}
CODE_INFO = {code: (exam, desc, amount) for exam, (code, desc, amount) in FEES.items()}

KINDS = {
    "missing": "Not submitted",
    "duplicate": "Duplicate claim",
    "code_mismatch": "Code does not match exam",
    "amount_mismatch": "Amount differs from fee table",
    "rejected": "Rejected by payer",
    "not_performed": "Billed but not performed",
}
OUTCOMES = {
    "claim_submitted": "Claim submitted",
    "corrected_resubmitted": "Corrected and resubmitted",
    "duplicate_voided": "Duplicate voided",
    "claim_voided": "Claim voided",
    "written_off": "Written off",
    "no_action": "No action needed (explained)",
}
REJECTIONS = ["Health card version code not valid on date of service", "Patient not eligible on date of service",
              "Referring physician number missing", "Service date outside the billing period"]


class Claim(BaseModel):  # FHIR Claim
    id: str
    appointment_id: str
    patient_id: str
    site_id: str
    payer: str  # ohip (mock), private, self
    fee_code: str
    amount: float
    service_date: str
    submitted_at: datetime
    status: str  # submitted, paid, rejected, voided
    rejection_reason: str | None = None


class WorkItem(BaseModel):
    id: str
    status: str = "open"  # open, resolved
    outcome: str | None = None
    note: str = ""
    resolved_by: str | None = None
    resolved_at: datetime | None = None
    history: list[dict] = []


def claims(store: Store) -> dict[str, Claim]:
    return store.module("claims", dict)


def work(store: Store) -> dict[str, WorkItem]:
    return store.module("billing_work", dict)


def _fee_for(exam_code: str):
    return FEES[exam_code]


def reconcile(store: Store, now: datetime) -> list[dict]:
    """Run every rule. Findings get stable ids (kind.appointment) so work items stick."""
    since = now - timedelta(days=WINDOW_DAYS)
    by_appt: dict[str, list[Claim]] = {}
    for c in list(claims(store).values()):
        if c.status != "voided":
            by_appt.setdefault(c.appointment_id, []).append(c)
    found: list[dict] = []

    def add(kind: str, appt, items: list[Claim], detail: str, at_stake: float):
        found.append({"id": f"{kind}.{appt.id}", "kind": kind, "appointment_id": appt.id, "claim_ids": [c.id for c in items],
                      "detail": detail, "at_stake": round(at_stake, 2)})

    for appt in list(store.appointments.values()):
        if appt.start < since or appt.start > now:
            continue
        mine = by_appt.get(appt.id, [])
        fee_code, _, fee = _fee_for(appt.exam_code)
        if appt.status in (AppointmentStatus.CANCELLED, AppointmentStatus.NO_SHOW):
            if mine:
                add("not_performed", appt, mine, f"Exam was {appt.status.value.replace('_', '-')}, but {len(mine)} claim(s) were submitted",
                    sum(c.amount for c in mine))
            continue
        if appt.status != AppointmentStatus.COMPLETED:
            continue
        if not mine:
            if appt.end < now - timedelta(days=SUBMISSION_DAYS):
                add("missing", appt, [], f"Completed {(now - appt.end).days} days ago with no claim; expected {fee_code}", fee)
            continue
        codes: dict[str, list[Claim]] = {}
        for c in mine:
            codes.setdefault(c.fee_code, []).append(c)
        for code, same in codes.items():
            if len(same) > 1:
                add("duplicate", appt, same, f"{len(same)} claims with {code} for one exam", sum(c.amount for c in same[1:]))
        for c in mine:
            if c.fee_code != fee_code:
                billed = CODE_INFO.get(c.fee_code, ("?", c.fee_code, 0))[1]
                add("code_mismatch", appt, [c], f"Billed {c.fee_code} ({billed}); exam performed needs {fee_code}",
                    abs(fee - c.amount))
            elif abs(c.amount - fee) > 0.005:
                add("amount_mismatch", appt, [c], f"Billed ${c.amount:.2f}; fee table says ${fee:.2f} for {fee_code}",
                    abs(fee - c.amount))
            if c.status == "rejected":
                add("rejected", appt, [c], f"Rejected: {c.rejection_reason}", c.amount)
    # Duplicate findings are per appointment and code; keep ids unique.
    seen, unique = set(), []
    for f in found:
        if f["id"] not in seen:
            seen.add(f["id"])
            unique.append(f)
    return unique


def resolve(store: Store, item_id: str, outcome: str, note: str, by: str, now: datetime) -> WorkItem:
    item = work(store).setdefault(item_id, WorkItem(id=item_id))
    item.status, item.outcome, item.note, item.resolved_by, item.resolved_at = "resolved", outcome, note, by, now
    item.history.append({"ts": now.isoformat(timespec="seconds"), "action": f"Resolved: {OUTCOMES[outcome]}", "note": note, "by": by})
    return item


def reopen(store: Store, item_id: str, by: str, now: datetime) -> WorkItem:
    item = work(store).setdefault(item_id, WorkItem(id=item_id))
    item.status, item.outcome = "open", None
    item.history.append({"ts": now.isoformat(timespec="seconds"), "action": "Reopened", "note": "", "by": by})
    return item


def to_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    cols = ["id", "kind_label", "status", "outcome", "service_date", "site_id", "appointment_id", "patient_name", "exam_name",
            "claim_ids", "detail", "at_stake", "resolved_by", "note"]
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({**r, "claim_ids": " ".join(r["claim_ids"])})
    return buf.getvalue()


def seed(s: Store, rng: random.Random, now: datetime) -> None:
    """Claims for the last 30 days of exams, with planted discrepancies of every kind."""
    since = now - timedelta(days=WINDOW_DAYS)
    done = sorted((a for a in s.appointments.values() if a.status == AppointmentStatus.COMPLETED and since <= a.start
                   and a.end < now - timedelta(days=SUBMISSION_DAYS)), key=lambda a: a.id)
    skipped = sorted((a for a in s.appointments.values()
                      if a.status in (AppointmentStatus.CANCELLED, AppointmentStatus.NO_SHOW) and since <= a.start < now),
                     key=lambda a: a.id)
    picks = rng.sample(done, 26)
    missing, duplicate, mismatch, amount, rejected = picks[:7], picks[7:12], picks[12:18], picks[18:21], picks[21:26]
    planted = [("missing", a.id) for a in missing]
    missing_ids = {a.id for a in missing}

    def claim(appt, *, code=None, amount=None, status=None, reason=None) -> Claim:
        fee_code, _, fee = _fee_for(appt.exam_code)
        submitted = appt.end + timedelta(hours=rng.randint(6, 40))
        c = Claim(id=s.next_id("CLM"), appointment_id=appt.id, patient_id=appt.patient_id, site_id=appt.site_id,
                  payer=rng.choices(["ohip", "private", "self"], weights=[0.86, 0.1, 0.04])[0],
                  fee_code=code or fee_code, amount=fee if amount is None else amount,
                  service_date=appt.start.date().isoformat(), submitted_at=submitted,
                  status=status or ("paid" if submitted < now - timedelta(days=12) else "submitted"), rejection_reason=reason)
        claims(s)[c.id] = c
        return c

    for appt in done:
        if appt.id in missing_ids:
            continue
        if appt in mismatch:
            # Typical slips: contrast billed as plain, wrong body part, wrong modality.
            wrong = {"CT_CHEST_C": "SYN-C111", "CT_CHEST": "SYN-C112", "CT_ABD_PEL": "SYN-C111", "MR_KNEE": "SYN-M201",
                     "MR_LSPINE": "SYN-M101", "MR_BRAIN": "SYN-M201", "XR_CHEST": "SYN-X301", "XR_KNEE": "SYN-X101",
                     "XR_LSPINE": "SYN-X201", "US_ABD": "SYN-U201", "US_PELVIS": "SYN-U101", "US_THYROID": "SYN-U101",
                     "US_VENOUS": "SYN-U101", "CT_HEAD": "SYN-C111"}[appt.exam_code]
            claim(appt, code=wrong, amount=CODE_INFO[wrong][2])
            planted.append(("code_mismatch", appt.id))
        elif appt in amount:
            claim(appt, amount=round(_fee_for(appt.exam_code)[2] * rng.choice([0.5, 1.1, 1.25]), 2))
            planted.append(("amount_mismatch", appt.id))
        elif appt in rejected:
            claim(appt, status="rejected", reason=rng.choice(REJECTIONS))
            planted.append(("rejected", appt.id))
        else:
            claim(appt)
            if appt in duplicate:
                claim(appt)
                planted.append(("duplicate", appt.id))
    for appt in rng.sample(skipped, 4):
        claim(appt)
        planted.append(("not_performed", appt.id))
    s.modules["billing_planted"] = planted
    # Two findings were already worked last week.
    for kind, aid in planted[:1] + [p for p in planted if p[0] == "rejected"][:1]:
        resolve(s, f"{kind}.{aid}", "claim_submitted" if kind == "missing" else "corrected_resubmitted",
                "Handled in last week's billing run", "Casey Brooks", now - timedelta(days=4))
