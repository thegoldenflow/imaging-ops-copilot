"""In-memory data store.

The demo keeps everything in process memory and regenerates it from the
synthetic seed on startup or reset. Modules read and write through this object,
so swapping in a database later means replacing this layer only.
"""

from dataclasses import dataclass, field
from typing import Any

from app.core.audit import AuditLog
from app.core.models import (
    Appointment,
    Allergy,
    Exam,
    ImagingStudy,
    LabResult,
    LlmCall,
    MessageOutbox,
    Patient,
    Referrer,
    Requisition,
    Scanner,
    Site,
    StaffUser,
    WaitlistEntry,
)


@dataclass
class Store:
    sites: dict[str, Site] = field(default_factory=dict)
    scanners: dict[str, Scanner] = field(default_factory=dict)
    exams: dict[str, Exam] = field(default_factory=dict)
    patients: dict[str, Patient] = field(default_factory=dict)
    referrers: dict[str, Referrer] = field(default_factory=dict)
    appointments: dict[str, Appointment] = field(default_factory=dict)
    waitlist: dict[str, WaitlistEntry] = field(default_factory=dict)
    studies: dict[str, ImagingStudy] = field(default_factory=dict)
    requisitions: dict[str, Requisition] = field(default_factory=dict)
    labs: dict[str, LabResult] = field(default_factory=dict)
    allergies: dict[str, Allergy] = field(default_factory=dict)
    staff: dict[str, StaffUser] = field(default_factory=dict)
    outbox: dict[str, MessageOutbox] = field(default_factory=dict)
    images: dict[str, tuple[bytes, str]] = field(default_factory=dict)  # key -> (bytes, media type)
    audit: AuditLog = field(default_factory=AuditLog)
    llm_calls: list[LlmCall] = field(default_factory=list)
    # Module-owned state (reports, backfill cases, calls, pre-registration, ...).
    modules: dict[str, Any] = field(default_factory=dict)
    # Bumped on every write so the UI can cheaply detect changes.
    version: int = 0
    _counters: dict[str, int] = field(default_factory=dict)

    def next_id(self, prefix: str) -> str:
        n = self._counters.get(prefix, 0) + 1
        self._counters[prefix] = n
        return f"{prefix}-{n:05d}"

    def touch(self) -> None:
        self.version += 1

    def module(self, name: str, factory) -> Any:
        if name not in self.modules:
            self.modules[name] = factory()
        return self.modules[name]


_store: Store | None = None


def get_store() -> Store:
    global _store
    if _store is None:
        from app.seed import build_store

        _store = build_store()
    return _store


def reset_store() -> Store:
    global _store
    from app.seed import build_store

    _store = build_store()
    return _store
