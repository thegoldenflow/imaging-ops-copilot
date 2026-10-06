"""Local stand-ins for external systems: SMS, email, phone, OHIP health card
validation and private insurance checks. Each has configurable latency and
failure rate so the demo can show degraded behaviour."""

import random
import time
from datetime import datetime

from pydantic import BaseModel

from app.core.models import MessageOutbox
from app.core.store import get_store


class MockServiceConfig(BaseModel):
    latency_ms: int = 150
    failure_rate: float = 0.0


MOCK_CONFIG: dict[str, MockServiceConfig] = {
    "sms": MockServiceConfig(),
    "email": MockServiceConfig(),
    "phone": MockServiceConfig(),
    "fax": MockServiceConfig(),
    "ohip": MockServiceConfig(latency_ms=400),
    "private_insurance": MockServiceConfig(latency_ms=600),
}


class MockServiceError(Exception):
    pass


def simulate_call(service: str) -> None:
    cfg = MOCK_CONFIG[service]
    time.sleep(cfg.latency_ms / 1000)
    if random.random() < cfg.failure_rate:
        raise MockServiceError(f"{service} service unavailable (simulated)")


def queue_message(
    *,
    channel: str,
    kind: str,
    to: str,
    body: str,
    language: str,
    patient_id: str | None,
    appointment_id: str | None = None,
    scheduled_for: datetime | None = None,
    note: str | None = None,
) -> MessageOutbox:
    store = get_store()
    msg = MessageOutbox(
        id=store.next_id("MSG"), channel=channel, kind=kind, patient_id=patient_id, to=to, language=language,
        body=body, appointment_id=appointment_id, scheduled_for=scheduled_for or datetime.now(), note=note,
    )
    store.outbox[msg.id] = msg
    store.touch()
    return msg


def dispatch_due(now: datetime | None = None) -> int:
    """Send every scheduled message whose time has come. Returns the count sent."""
    now = now or datetime.now()
    sent = 0
    for msg in get_store().outbox.values():
        if msg.status != "scheduled" or msg.scheduled_for > now:
            continue
        # No latency here: dispatch runs in a background loop over many messages.
        if random.random() < MOCK_CONFIG[msg.channel].failure_rate:
            msg.status = "failed"
            continue
        msg.status, msg.sent_at = "sent", now
        sent += 1
    if sent:
        get_store().touch()
    return sent


class CoverageResult(BaseModel):
    valid: bool
    status: str  # valid, invalid, service_unavailable
    detail: str


def validate_health_card(number: str, version: str) -> CoverageResult:
    """Mock OHIP check: 10 digits plus a two-letter version code."""
    try:
        simulate_call("ohip")
    except MockServiceError as e:
        return CoverageResult(valid=False, status="service_unavailable", detail=str(e))
    digits = number.replace(" ", "").replace("-", "")
    if len(digits) == 10 and digits.isdigit() and len(version) == 2 and version.isalpha():
        return CoverageResult(valid=True, status="valid", detail="Coverage active (mock)")
    return CoverageResult(valid=False, status="invalid", detail="Number or version code not recognized (mock)")


def verify_private_insurance(insurer: str, policy: str) -> CoverageResult:
    try:
        simulate_call("private_insurance")
    except MockServiceError as e:
        return CoverageResult(valid=False, status="service_unavailable", detail=str(e))
    if insurer.strip() and len(policy.strip()) >= 6:
        return CoverageResult(valid=True, status="valid", detail=f"{insurer}: policy active (mock)")
    return CoverageResult(valid=False, status="invalid", detail="Policy not found (mock)")
