"""System 7 · Contrast & Renal Checker.

Claude's only job here is the shared extraction (history and allergies from the
requisition). The decision is made by rules whose thresholds the medical
director sets; statuses are computed on read, so changing a threshold
recomputes every check immediately."""

import re
from datetime import datetime, timedelta

from pydantic import BaseModel, Field

from app.core.models import ACTIVE_STATUSES, Appointment
from app.core.store import Store, get_store
from app.integrations.mocks import queue_message
from app.llm.deid import age_from_dob
from app.modules.protocols.library import BY_ID
from app.modules.scheduling import service as scheduling

CONTRAST_EXAMS = {"CT_CHEST_C", "CT_ABD_PEL"}
SEVERITY_ORDER = {"pass": 0, "needs_egfr": 1, "needs_premedication": 2, "needs_review": 3}
CONTRAST_WORDS = re.compile(r"contrast|iodin|gadolin|\bdye\b", re.I)
SEVERE_WORDS = re.compile(r"anaphyla|severe|airway|throat swelling", re.I)


class ContrastConfig(BaseModel):
    """Placeholder thresholds for the demo. A medical director must set real values."""

    egfr_threshold: float = Field(30, ge=0, le=120, description="eGFR below this needs radiologist review")
    egfr_max_age_days: int = Field(90, ge=1, le=730, description="Older results do not count")
    risk_age: int = Field(60, ge=18, le=100, description="Age at which an eGFR is required")
    egfr_required_for_all: bool = False
    premedicate_mild_moderate_reaction: bool = True
    updated_by: str | None = None
    updated_at: datetime | None = None


class ContrastCheck(BaseModel):
    status: str  # pass, needs_egfr, needs_premedication, needs_review
    basis: list[str]
    egfr: float | None
    egfr_date: datetime | None
    risk_factors: list[str]
    reactions: list[str]


def config(store: Store) -> ContrastConfig:
    return store.module("contrast_config", ContrastConfig)


def is_contrast(store: Store, appt: Appointment) -> bool:
    if appt.protocol_id and appt.protocol_id in BY_ID:
        return BY_ID[appt.protocol_id].contrast
    return appt.exam_code in CONTRAST_EXAMS


def evaluate(store: Store, patient_id: str, requisition_id: str | None, now: datetime | None = None) -> ContrastCheck:
    from app.modules.requisitions.service import extractions  # avoid import cycle

    now = now or datetime.now()
    cfg = config(store)
    patient = store.patients[patient_id]
    fields = extractions(store)[requisition_id].fields if requisition_id in extractions(store) else None

    reactions: list[tuple[str, bool]] = []  # (text, severe)
    for allergy in store.allergies.values():
        if allergy.patient_id == patient_id and allergy.contrast:
            reactions.append((f"{allergy.substance}: {allergy.reaction} ({allergy.severity}) on record",
                              allergy.severity == "severe"))
    if fields:
        for item in fields["allergies"]:
            if CONTRAST_WORDS.search(item["value"]):
                reactions.append((f"Requisition: “{item['value']}”", bool(SEVERE_WORDS.search(item["value"]))))

    risk = []
    age = age_from_dob(patient.dob, now.date())
    if age >= cfg.risk_age:
        risk.append(f"Age {age} (≥ {cfg.risk_age})")
    if fields:
        risk += [f"Requisition: “{i['value']}”" for i in fields["renal_or_diabetes"]]

    labs = sorted((l for l in store.labs.values() if l.patient_id == patient_id and l.code == "egfr"),
                  key=lambda l: l.taken_at)
    latest = labs[-1] if labs else None
    fresh = latest is not None and now - latest.taken_at <= timedelta(days=cfg.egfr_max_age_days)

    statuses, basis = ["pass"], []
    if any(severe for _, severe in reactions):
        statuses.append("needs_review")
        basis.append("Severe prior contrast reaction: radiologist must decide whether contrast can be given.")
    elif reactions:
        statuses.append("needs_premedication" if cfg.premedicate_mild_moderate_reaction else "needs_review")
        basis.append("Prior mild or moderate contrast reaction: premedication protocol applies.")
    if risk or cfg.egfr_required_for_all:
        if latest is None:
            statuses.append("needs_egfr")
            basis.append("eGFR required (risk factors present) but no result on file.")
        elif not fresh:
            statuses.append("needs_egfr")
            basis.append(f"Latest eGFR {latest.value:.0f} is {(now - latest.taken_at).days} days old "
                         f"(limit {cfg.egfr_max_age_days} days).")
    if latest is not None and fresh and latest.value < cfg.egfr_threshold:
        statuses.append("needs_review")
        basis.append(f"eGFR {latest.value:.0f} is below the threshold of {cfg.egfr_threshold:.0f}.")
    if latest is not None and fresh and latest.value >= cfg.egfr_threshold:
        basis.append(f"eGFR {latest.value:.0f} on {latest.taken_at:%b %d} meets the threshold of {cfg.egfr_threshold:.0f}.")
    if not reactions:
        basis.append("No contrast allergy on record or on the requisition.")
    if not risk and not cfg.egfr_required_for_all:
        basis.append("No renal risk factors; eGFR not required.")
    status = max(statuses, key=SEVERITY_ORDER.get)
    return ContrastCheck(status=status, basis=basis, egfr=latest.value if latest else None,
                         egfr_date=latest.taken_at if latest else None, risk_factors=risk,
                         reactions=[text for text, _ in reactions])


def followup_reminder(appt: Appointment) -> None:
    """Unresolved checks prompt the referring office 48 hours before the exam."""
    store = get_store()
    if not is_contrast(store, appt):
        return
    check = evaluate(store, appt.patient_id, appt.requisition_id)
    if check.status == "pass":
        return
    referrer = store.referrers[appt.referrer_id]
    patient = store.patients[appt.patient_id]
    queue_message(
        channel="fax", kind="contrast_followup", to=referrer.fax, language="en", patient_id=patient.id,
        appointment_id=appt.id, scheduled_for=max(appt.start - timedelta(hours=48), datetime.now()),
        body=(f"Contrast check outstanding for {patient.full_name}, {appt.start:%b %d %H:%M}: "
              f"{check.status.replace('_', ' ')}. {' '.join(check.basis[:2])}"),
    )


def upcoming_checks(store: Store, days: int = 14) -> list[tuple[Appointment, ContrastCheck]]:
    now = datetime.now()
    rows = []
    for appt in store.appointments.values():
        if appt.status in ACTIVE_STATUSES and now <= appt.start <= now + timedelta(days=days) and is_contrast(store, appt):
            rows.append((appt, evaluate(store, appt.patient_id, appt.requisition_id, now)))
    rows.sort(key=lambda r: (-SEVERITY_ORDER[r[1].status], r[0].start))
    return rows


scheduling.BOOKING_HOOKS.append(followup_reminder)
