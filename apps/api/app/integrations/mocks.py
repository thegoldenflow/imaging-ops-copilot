"""Local stand-ins for external systems: SMS, email, phone, fax, OHIP health card
validation, private insurance checks and outside image archives. Each has
configurable latency and failure rate so the demo can show degraded behaviour,
and each is an adapter under the shared contract (app/integrations/contract.py:
correlation ids, timeout and retry, circuit breaker, dead letters)."""

import random
from datetime import datetime

from pydantic import BaseModel

from app.core.models import MessageOutbox
from app.core.store import get_store
from app.integrations.contract import Adapter, AdapterError, AdapterTimeout, Policy


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
    "outside_archive": MockServiceConfig(latency_ms=300),
}


class MockAdapter(Adapter):
    """A mock external system with the latency and failure rate in MOCK_CONFIG.

    Messaging providers (SMS, email, phone, fax) accept a message and deliver it
    later, so their mock returns at once (`simulate_latency=False`); request and
    response services (OHIP, insurers, archives) make the caller wait."""

    def __init__(self, name: str, policy: Policy | None = None, *, simulate_latency: bool = True, **kwargs) -> None:
        super().__init__(name, policy or Policy(), **kwargs)
        self.simulate_latency = simulate_latency

    def _send(self, operation: str, payload: dict, *, correlation_id: str, timeout_s: float) -> dict:
        cfg = MOCK_CONFIG[self.name]
        latency = cfg.latency_ms / 1000 if self.simulate_latency else 0.0
        if latency > timeout_s:
            self.sleep(timeout_s)
            raise AdapterTimeout(f"{self.name} did not answer within {timeout_s:g} s (simulated)")
        if latency:
            self.sleep(latency)
        if random.random() < cfg.failure_rate:
            raise AdapterError(f"{self.name} service unavailable (simulated)")
        return {"status": "accepted", "correlation_id": correlation_id}


def _adapters() -> dict[str, MockAdapter]:
    messaging = Policy(timeout_s=5, attempts=3, backoff_s=0.2)
    lookup = Policy(timeout_s=3, attempts=2, backoff_s=0.2)
    return {
        "sms": MockAdapter("sms", messaging, simulate_latency=False),
        "email": MockAdapter("email", messaging, simulate_latency=False),
        "phone": MockAdapter("phone", messaging, simulate_latency=False),
        "fax": MockAdapter("fax", messaging, simulate_latency=False),
        "ohip": MockAdapter("ohip", lookup),
        "private_insurance": MockAdapter("private_insurance", lookup),
        # The retrieval worker keeps its own durable retry, so one attempt per call.
        "outside_archive": MockAdapter("outside_archive", Policy(timeout_s=10, attempts=1)),
    }


ADAPTERS: dict[str, MockAdapter] = _adapters()


def adapter(name: str) -> MockAdapter:
    return ADAPTERS[name]


def reset_adapters() -> None:
    """Close every circuit breaker (tests; after an admin changes a mock's settings)."""
    for a in ADAPTERS.values():
        a.breaker.reset()


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
    """Send every scheduled message whose time has come. Returns the count sent.
    The message id is the correlation id; a message that still fails is dead-lettered."""
    now = now or datetime.now()
    sent = 0
    for msg in get_store().outbox.claim_due("scheduled_for", now, statuses=("scheduled",)):
        result = adapter(msg.channel).call("send", {"to": msg.to, "body": msg.body, "kind": msg.kind},
                                           correlation_id=msg.id)
        if not result.ok:
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
    result = adapter("ohip").call("validate", {"number": number, "version": version})
    if not result.ok:
        return CoverageResult(valid=False, status="service_unavailable", detail=result.error)
    digits = number.replace(" ", "").replace("-", "")
    if len(digits) == 10 and digits.isdigit() and len(version) == 2 and version.isalpha():
        return CoverageResult(valid=True, status="valid", detail="Coverage active (mock)")
    return CoverageResult(valid=False, status="invalid", detail="Number or version code not recognized (mock)")


def verify_private_insurance(insurer: str, policy: str) -> CoverageResult:
    result = adapter("private_insurance").call("verify", {"insurer": insurer, "policy": policy})
    if not result.ok:
        return CoverageResult(valid=False, status="service_unavailable", detail=result.error)
    if insurer.strip() and len(policy.strip()) >= 6:
        return CoverageResult(valid=True, status="valid", detail=f"{insurer}: policy active (mock)")
    return CoverageResult(valid=False, status="invalid", detail="Policy not found (mock)")
