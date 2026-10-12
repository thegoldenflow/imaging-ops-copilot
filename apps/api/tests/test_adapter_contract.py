"""WP2: the contract for adapters to non-FHIR systems (spec 6.2): correlation ids,
timeout and retry, circuit breaker, dead-letter queue; the mocks follow it."""

import logging
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

from app.integrations import interfaces
from app.integrations.contract import Adapter, AdapterError, AdapterRejected, AdapterTimeout, Policy, dead_letters
from app.integrations.mocks import MOCK_CONFIG, MockAdapter, adapter, dispatch_due, queue_message, validate_health_card


class Scripted(Adapter):
    """Fails with the queued errors, then answers."""

    def __init__(self, errors=(), **kwargs):
        super().__init__("scripted", **kwargs)
        self.errors, self.sent = list(errors), []

    def _send(self, operation, payload, *, correlation_id, timeout_s):
        self.sent.append(correlation_id)
        if self.errors:
            raise self.errors.pop(0)
        return {"echo": payload}


class Clock:
    def __init__(self):
        self.now, self.slept = 1000.0, []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def test_retries_with_backoff_then_succeeds(fresh_state):
    clock = Clock()
    a = Scripted([AdapterError("busy"), AdapterTimeout("slow")], policy=Policy(attempts=3, backoff_s=0.2),
                 clock=clock, sleep=clock.sleep)
    result = a.call("send", {"x": 1}, correlation_id="corr-1")
    assert result.ok and result.attempts == 3 and result.response == {"echo": {"x": 1}}
    assert a.sent == ["corr-1"] * 3  # the same correlation id on every attempt
    assert clock.slept == [0.2, 0.8]  # exponential backoff
    assert not any(d.correlation_id == "corr-1" for d in dead_letters(fresh_state).values())


def test_final_failure_is_dead_lettered_with_the_payload_encrypted(fresh_state):
    clock = Clock()
    a = Scripted([AdapterError("down")] * 3, policy=Policy(attempts=3), clock=clock, sleep=clock.sleep)
    result = a.call("send", {"to": "+1-416-555-0100", "body": "Your appointment"}, correlation_id="corr-2")
    assert not result.ok and result.attempts == 3 and result.error == "down"
    letter = dead_letters(fresh_state)[result.dead_letter_id]
    assert (letter.adapter, letter.operation, letter.correlation_id, letter.attempts, letter.status) == (
        "scripted", "send", "corr-2", 3, "parked")
    assert letter.payload["to"] == "+1-416-555-0100"
    fresh_state.save()
    raw = fresh_state.conn().execute(text("SELECT payload FROM dead_letters WHERE row_key = :k"),
                                     {"k": letter.id}).scalar()
    assert raw.startswith("k1:") and "555-0100" not in raw


def test_rejected_messages_are_not_retried(fresh_state):
    a = Scripted([AdapterRejected("invalid number")], policy=Policy(attempts=3), sleep=lambda s: None)
    result = a.call("send", {})
    assert not result.ok and result.attempts == 1 and result.dead_letter_id
    assert len(result.correlation_id) == 32  # generated when the caller has none


def test_callers_with_their_own_retry_park_nothing(fresh_state):
    a = Scripted([AdapterError("down")], sleep=lambda s: None)
    result = a.call("fetch", {}, attempts=1, dead_letter=False)
    assert not result.ok and result.attempts == 1 and result.dead_letter_id is None and len(a.sent) == 1


def test_circuit_breaker_opens_and_recovers(fresh_state):
    clock = Clock()
    a = Scripted([AdapterError("down")] * 5, policy=Policy(attempts=1, breaker_failures=5, breaker_reset_s=30),
                 clock=clock, sleep=clock.sleep)
    for _ in range(5):
        assert not a.call("send", {}).ok
    assert a.breaker.state == "open"
    result = a.call("send", {})
    assert not result.ok and "circuit open" in result.error and len(a.sent) == 5  # the system was not called
    assert result.dead_letter_id  # the undelivered message is parked
    clock.now += 30
    assert a.breaker.state == "half_open"
    assert a.call("send", {}).ok and a.breaker.state == "closed"  # the trial call closed it
    a.errors = [AdapterError("down")] * 6
    for _ in range(5):
        a.call("send", {})
    clock.now += 30
    assert not a.call("send", {}).ok and a.breaker.state == "open"  # a failed trial reopens it


def test_mock_timeout_is_enforced(fresh_state, monkeypatch):
    clock = Clock()
    archive = MockAdapter("outside_archive", Policy(timeout_s=0.5, attempts=2, backoff_s=0.1), clock=clock,
                          sleep=clock.sleep)
    monkeypatch.setitem(MOCK_CONFIG, "outside_archive", MOCK_CONFIG["outside_archive"].model_copy(update={"latency_ms": 2000}))
    result = archive.call("fetch", {})
    assert not result.ok and "did not answer within 0.5 s" in result.error
    assert clock.slept == [0.5, 0.1, 0.5]  # waited the timeout, backed off, waited again


def test_correlation_ids_are_logged_both_ways(caplog):
    a = Scripted(sleep=lambda s: None)
    with caplog.at_level(logging.INFO, logger="app.integrations"):
        a.call("send", {"secret": "Paul Brennan"}, correlation_id="corr-out", dead_letter=False)
        a.receive("reply", correlation_id="MSG-00042")
    lines = [r.getMessage() for r in caplog.records]
    assert any("correlation_id=corr-out" in line and "direction=out" in line and "outcome=ok" in line for line in lines)
    assert any("correlation_id=MSG-00042" in line and "direction=in" in line for line in lines)
    assert not any("Paul Brennan" in line for line in lines)  # payloads never reach the log


# ---------- the mocks under the contract ----------


def test_outbox_dispatch_dead_letters_failed_messages(fresh_state, monkeypatch):
    sms = adapter("sms")
    monkeypatch.setattr(sms, "sleep", lambda s: None)
    monkeypatch.setitem(MOCK_CONFIG, "sms", MOCK_CONFIG["sms"].model_copy(update={"failure_rate": 1.0}))
    when = datetime(2000, 1, 1)
    msg = queue_message(channel="sms", kind="test", to="+1-416-555-0199", body="Hello", language="en",
                        patient_id=None, scheduled_for=when)
    dispatch_due(when + timedelta(minutes=1))
    assert msg.status == "failed"
    letter = next(d for d in dead_letters(fresh_state).values() if d.correlation_id == msg.id)
    assert letter.adapter == "sms" and letter.attempts == 3
    monkeypatch.setitem(MOCK_CONFIG, "sms", MOCK_CONFIG["sms"].model_copy(update={"failure_rate": 0.0}))
    sms.breaker.reset()
    ok = queue_message(channel="sms", kind="test", to="+1-416-555-0198", body="Hi", language="en",
                       patient_id=None, scheduled_for=when)
    dispatch_due(when + timedelta(minutes=1))
    assert ok.status == "sent"


def test_health_card_check_degrades_when_ohip_is_down(fresh_state, monkeypatch):
    monkeypatch.setattr(adapter("ohip"), "sleep", lambda s: None)
    monkeypatch.setitem(MOCK_CONFIG, "ohip", MOCK_CONFIG["ohip"].model_copy(update={"latency_ms": 0, "failure_rate": 1.0}))
    down = validate_health_card("1234567890", "AB")
    assert (down.valid, down.status) == (False, "service_unavailable") and "unavailable" in down.detail
    monkeypatch.setitem(MOCK_CONFIG, "ohip", MOCK_CONFIG["ohip"].model_copy(update={"failure_rate": 0.0}))
    adapter("ohip").breaker.reset()
    assert validate_health_card("1234567890", "AB").status == "valid"


@pytest.mark.parametrize("cls", [interfaces.Hl7v2InterfaceEngine, interfaces.PacsAdapter, interfaces.TelephonyAdapter,
                                 interfaces.SmsGatewayAdapter, interfaces.FaxAdapter, interfaces.BillingAdapter])
def test_real_adapters_are_interfaces_only(cls):
    with pytest.raises(NotImplementedError):
        cls("real").call("send", {}, dead_letter=False)


def test_admin_sees_adapter_health_without_message_contents(client, login, fresh_state):
    Scripted([AdapterRejected("bad")], sleep=lambda s: None).call("send", {"body": "secret text"})
    body = client.get("/api/admin/integrations", headers=login("U-ADMIN")).json()
    assert {a["adapter"] for a in body["adapters"]} >= {"sms", "email", "phone", "fax", "ohip", "outside_archive"}
    assert body["dead_letter_count"] >= 1 and all("payload" not in d for d in body["dead_letters"])
    assert client.get("/api/admin/integrations", headers=login("U-OPS")).status_code == 403
