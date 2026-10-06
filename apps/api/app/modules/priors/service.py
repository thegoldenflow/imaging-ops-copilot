"""System 10 · Prior Imaging Retrieval.

After booking, prior outside studies (mentioned on the requisition or known in
the patient's history) are requested from mock outside archives. An in-process
worker retries with exponential backoff and gives up after a fixed number of
attempts; every task ends received, not found or failed. Received studies are
imported and linked to the upcoming exam."""

import random
from datetime import datetime, timedelta

from pydantic import BaseModel

from app.core.models import Appointment, ImagingStudy
from app.core.store import Store, get_store
from app.integrations.mocks import MOCK_CONFIG, MockServiceConfig, MockServiceError, simulate_call
from app.modules.scheduling import service as scheduling
from app.phantom import chest_phantom

FACILITIES = ["Northview General Hospital", "Riverside Health Centre", "Lakeview Diagnostics"]
MAX_ATTEMPTS = 4
BACKOFF_SECONDS = 3  # 3 s, 6 s, 12 s ... (compressed for the demo)

MOCK_CONFIG.setdefault("outside_archive", MockServiceConfig(latency_ms=300, failure_rate=0.0))


class RetrievalTask(BaseModel):
    id: str
    appointment_id: str
    requisition_id: str | None
    patient_id: str
    facility: str
    reason: str
    status: str = "requested"  # requested, retrying, received, not_found, failed
    attempts: int = 0
    max_attempts: int = MAX_ATTEMPTS
    next_attempt_at: datetime
    created_at: datetime
    completed_at: datetime | None = None
    events: list[dict] = []
    imported_study_ids: list[str] = []


def archive_index(store: Store) -> dict[str, list[dict]]:
    """What each mock outside archive holds: patient id -> [{facility, description, date}]."""
    return store.module("outside_archive", dict)


def tasks(store: Store) -> dict[str, RetrievalTask]:
    return store.module("retrieval_tasks", dict)


def _log(task: RetrievalTask, text: str, now: datetime) -> None:
    task.events.append({"ts": now.isoformat(timespec="seconds"), "text": text})


def create_tasks(store: Store, appt: Appointment, now: datetime | None = None) -> list[RetrievalTask]:
    from app.modules.requisitions.service import extractions  # avoid import cycle

    now = now or datetime.now()
    wanted: dict[str, str] = {}
    rec = extractions(store).get(appt.requisition_id or "")
    if rec:
        for mention in rec.fields.get("prior_imaging", []):
            facility = next((f for f in FACILITIES if f.lower() in mention["value"].lower()), None)
            if facility:
                wanted.setdefault(facility, f"Mentioned on requisition: {mention['value']}")
    for entry in archive_index(store).get(appt.patient_id, []):
        wanted.setdefault(entry["facility"], f"Patient history lists imaging at {entry['facility']}")
    created = []
    existing = {(t.appointment_id, t.facility) for t in tasks(store).values()}
    for facility, reason in wanted.items():
        if (appt.id, facility) in existing:
            continue
        task = RetrievalTask(id=store.next_id("PRI"), appointment_id=appt.id, requisition_id=appt.requisition_id,
                             patient_id=appt.patient_id, facility=facility, reason=reason, next_attempt_at=now,
                             created_at=now)
        _log(task, f"Request created ({reason})", now)
        tasks(store)[task.id] = task
        created.append(task)
    if created:
        store.touch()
    return created


def _import(store: Store, task: RetrievalTask, studies: list[dict], now: datetime) -> None:
    appt = store.appointments.get(task.appointment_id)
    for entry in studies:
        key = store.next_id("IMG-PRIOR")
        store.images[key] = (chest_phantom("normal", seed=random.randint(0, 999)), "image/png")
        study = ImagingStudy(
            id=store.next_id("ST"), appointment_id=None, patient_id=task.patient_id,
            referrer_id=appt.referrer_id if appt else next(iter(store.referrers)), exam_code=appt.exam_code if appt else "XR_CHEST",
            performed_at=datetime.fromisoformat(entry["date"]), image_key=key, study_uid=f"2.25.{random.getrandbits(100)}",
            indication=f"Outside prior: {entry['description']}", source_facility=task.facility,
            prior_for_appointment_id=task.appointment_id,
        )
        store.studies[study.id] = study
        task.imported_study_ids.append(study.id)


def attempt(store: Store, task: RetrievalTask, now: datetime | None = None) -> None:
    now = now or datetime.now()
    task.attempts += 1
    try:
        simulate_call("outside_archive")
    except MockServiceError:
        if task.attempts >= task.max_attempts:
            task.status, task.completed_at = "failed", now
            _log(task, f"Attempt {task.attempts}: archive unavailable. Gave up after {task.attempts} attempts.", now)
        else:
            delay = BACKOFF_SECONDS * 2 ** (task.attempts - 1)
            task.status, task.next_attempt_at = "retrying", now + timedelta(seconds=delay)
            _log(task, f"Attempt {task.attempts}: archive unavailable. Retrying in {delay}s.", now)
        return
    found = [e for e in archive_index(store).get(task.patient_id, []) if e["facility"] == task.facility]
    task.completed_at = now
    if found:
        _import(store, task, found, now)
        task.status = "received"
        _log(task, f"Attempt {task.attempts}: received {len(found)} study(ies); imported and linked to {task.appointment_id}.", now)
    else:
        task.status = "not_found"
        _log(task, f"Attempt {task.attempts}: no matching studies at {task.facility}.", now)


def process_due(store: Store | None = None, now: datetime | None = None) -> int:
    store = store or get_store()
    now = now or datetime.now()
    due = [t for t in tasks(store).values() if t.status in ("requested", "retrying") and t.next_attempt_at <= now]
    for task in due:
        attempt(store, task, now)
    if due:
        store.touch()
    return len(due)


def retry(store: Store, task: RetrievalTask) -> None:
    now = datetime.now()
    task.status, task.attempts, task.next_attempt_at, task.completed_at = "requested", 0, now, None
    _log(task, "Manual retry requested", now)
    store.touch()


def on_booked(appt: Appointment) -> None:
    create_tasks(get_store(), appt)


scheduling.BOOKING_HOOKS.append(on_booked)
