"""WP3: the domain event bus and the HL7 v2 adapter (spec 6.2)."""

from datetime import datetime

import pytest

from app.ehr import hl7
from app.ehr.events import EVENT_TYPES, MAX_ATTEMPTS, DomainEvent, InProcessEventBus, RedisPubSubBus, event_id_for, platform_event
from app.ehr.hl7 import Hl7EventAdapter, Hl7Message
from app.integrations.contract import dead_letters

AT = datetime(2026, 10, 8, 9, 30)


@pytest.fixture
def local_bus():
    """A bus of its own, so the test's subscribers never leak into the app's."""
    return InProcessEventBus()


def _event(n: int, event_type: str = "patient.admitted", event_id: str | None = None) -> DomainEvent:
    return DomainEvent(event_id=event_id or f"evt-{n}", type=event_type, occurred_at=AT, correlation_id=f"c{n}",
                       actor="system:test", source="test", patient="pat-0001", encounter=f"ed-{n:05d}",
                       refs={"patient": "Patient/pat-0001", "encounter": f"Encounter/ed-{n:05d}"})


def test_delivers_committed_events_in_order_to_matching_subscribers(fresh_state, local_bus):
    admitted, everything = [], []
    local_bus.subscribe("patient.admitted", admitted.append, consumer="t.admitted")
    local_bus.subscribe("*", everything.append, consumer="t.all")
    published = local_bus.publish_many([_event(1), _event(2, "patient.discharged"), _event(3)])
    assert [e.seq for e in published] == sorted(e.seq for e in published)
    result = local_bus.drain()
    assert [e.event_id for e in admitted] == ["evt-1", "evt-3"]
    assert [e.event_id for e in everything] == ["evt-1", "evt-2", "evt-3"]
    assert result.delivered == 5 and result.by_consumer == {"t.admitted": 2, "t.all": 3}
    assert local_bus.drain().delivered == 0  # cursors moved on
    status = {c["consumer"]: c for c in local_bus.consumer_status(fresh_state)}
    assert status["t.all"]["backlog"] == 0 and status["t.all"]["delivered"] == 3


def test_consumers_deduplicate_by_event_id(fresh_state, local_bus):
    """A redelivered event (same event_id) produces no second business result."""
    handled = []
    local_bus.subscribe("patient.admitted", handled.append, consumer="t.dedupe")
    local_bus.publish(_event(1, event_id="same-id"))
    local_bus.drain()
    local_bus.publish(_event(2, event_id="same-id"))  # e.g. the interface engine resent the message
    result = local_bus.drain()
    assert [e.event_id for e in handled] == ["same-id"]
    assert result.duplicates == 1
    local_bus.publish_many([_event(3, event_id="twice"), _event(4, event_id="twice")])  # twice in one batch
    assert local_bus.drain().duplicates == 1 and [e.event_id for e in handled] == ["same-id", "twice"]
    assert local_bus.consumer_status(fresh_state)[0]["duplicates_skipped"] == 2


def test_a_failing_batch_is_redone_event_by_event(fresh_state, local_bus):
    """Events before the failing one are delivered once each; nothing is lost or delivered twice."""
    seen = []

    def strict(event: DomainEvent) -> None:
        if event.event_id == "evt-3":
            raise ValueError("bad event")
        seen.append(event.event_id)

    local_bus.subscribe("*", strict, consumer="t.strict")
    local_bus.publish_many([_event(i) for i in range(1, 6)])
    for _ in range(MAX_ATTEMPTS):
        local_bus.drain()
    assert sorted(set(seen)) == ["evt-1", "evt-2", "evt-4", "evt-5"]
    status = local_bus.consumer_status(fresh_state)[0]
    assert status["delivered"] == 4 and status["parked"] == 1 and status["backlog"] == 0


def test_a_failing_handler_is_retried_then_parked_without_reordering(fresh_state, local_bus):
    seen, calls = [], []

    def flaky(event: DomainEvent) -> None:
        calls.append(event.event_id)
        if event.event_id == "evt-1":
            raise RuntimeError("downstream unavailable")
        seen.append(event.event_id)

    local_bus.subscribe("patient.admitted", flaky, consumer="t.flaky")
    local_bus.publish_many([_event(1), _event(2)])
    for attempt in range(1, MAX_ATTEMPTS):
        result = local_bus.drain()
        assert result.delivered == 0 and seen == []  # evt-2 waits behind evt-1
        delivery = local_bus._delivery(fresh_state, "t.flaky", "evt-1")
        assert (delivery.status, delivery.attempts) == ("failed", attempt)
    result = local_bus.drain()  # last attempt: evt-1 is parked, evt-2 goes through
    assert result.parked == 1 and seen == ["evt-2"] and "evt-1" in calls
    letters = [d for d in dead_letters(fresh_state).values() if d.adapter == "event-bus/t.flaky"]
    assert len(letters) == 1 and letters[0].attempts == MAX_ATTEMPTS and "downstream unavailable" in letters[0].error
    status = local_bus.consumer_status(fresh_state)[0]
    assert status["parked"] == 1 and status["backlog"] == 0


def test_subscriptions_are_checked(local_bus):
    with pytest.raises(ValueError):
        local_bus.subscribe("patient.teleported", print)
    local_bus.subscribe("patient.admitted", print, consumer="t.once")
    with pytest.raises(ValueError):
        local_bus.subscribe("patient.admitted", print, consumer="t.once")
    with pytest.raises(NotImplementedError):
        RedisPubSubBus("redis://localhost").publish(_event(1))


# ---------- HL7 v2 ----------

MESSAGES = [
    Hl7Message("ADT^A01", "C1", AT, patient="pat-0001", patient_class="E", location="ED", visit="ed-00001"),
    Hl7Message("ADT^A02", "C2", AT, patient="pat-0001", patient_class="I", location="SURG-03-B",
               prior_location="ICU-02-A", visit="stay-00001"),
    Hl7Message("ADT^A03", "C3", AT, patient="pat-0001", patient_class="I", prior_location="SURG-03-B",
               visit="stay-00001", disposition="home"),
    Hl7Message("ADT^A08", "C4", AT, patient="pat-0001", patient_class="E", location="ED-04-A", visit="ed-00001",
               reason="DECISION", disposition="admit"),
    Hl7Message("ADT^A20", "C5", AT, location="MEDA-05-A", bed_status="U"),
    Hl7Message("ORM^O01", "C6", AT, patient="pat-0001", patient_class="E", visit="ed-00001", order="ord-ed-00001-01",
               section="LAB"),
    Hl7Message("ORU^R01", "C7", AT, patient="pat-0001", patient_class="E", visit="ed-00001", order="ord-ed-00001-01",
               section="LAB", results=("Observation/obs-ord-ed-00001-01",)),
    Hl7Message("SIU^S12", "C8", AT, patient="pat-0001", visit="ed-00001", appointment="appt-1",
               appointment_status="Booked", location="OR-04"),
    Hl7Message("SIU^S14", "C9", AT, patient="pat-0001", visit="stay-00001", appointment="appt-1",
               appointment_status="Complete", location="OR-04"),
    Hl7Message("SIU^S15", "C10", AT, patient="pat-0001", visit="stay-00001", appointment="appt-1",
               appointment_status="Cancelled", location="OR-04"),
]


@pytest.mark.parametrize("message", MESSAGES, ids=lambda m: m.message_type)
def test_every_message_type_maps_to_its_domain_event(message):
    event = hl7.to_event(message)
    assert event.type == hl7.ROUTES[message.message_type] and event.type in EVENT_TYPES
    assert event.source == message.message_type and event.message_id == message.control_id
    assert event.occurred_at == AT and event.actor == "system:demo-ehr"
    assert all(isinstance(v, list) or "/" in v for v in event.refs.values()), "references only"
    # ER7 round trip: what the interface engine would receive parses back to the same message
    assert hl7.parse(message.er7()) == message


def test_mapping_details():
    by_type = {m.message_type: hl7.to_event(m) for m in MESSAGES}
    assert by_type["ADT^A01"].attrs == {"encounter_class": "EMER"}
    assert by_type["ADT^A02"].refs["from_location"] == "Location/ICU-02-A"
    assert by_type["ADT^A03"].attrs["disposition"] == "home"
    assert by_type["ADT^A08"].attrs == {"encounter_class": "EMER", "change": "decision", "disposition": "admit"}
    assert by_type["ADT^A20"].attrs == {"status": "U"} and by_type["ADT^A20"].patient is None
    assert by_type["ORU^R01"].attrs["category"] == "laboratory"
    assert by_type["ORU^R01"].refs["results"] == ["Observation/obs-ord-ed-00001-01"]
    assert by_type["SIU^S14"].attrs["status"] == "Complete"
    # A resent message (same control id) is the same event; the operator (EVN-5) becomes the actor
    resent = hl7.to_event(MESSAGES[0])
    assert resent.event_id == by_type["ADT^A01"].event_id == event_id_for("hl7:DEMO-EHR:C1")
    operated = hl7.to_event(Hl7Message("ADT^A01", "C11", AT, operator="U-OPS", patient="pat-0001",
                                       patient_class="I", location="MEDA-01-A", visit="stay-1"))
    assert operated.actor == "user:U-OPS"


def test_er7_looks_like_hl7():
    text = MESSAGES[1].er7()
    segments = text.split("\r")
    assert segments[0].startswith("MSH|^~\\&|DEMO-EHR|DEMO-HOSPITAL|") and "|ADT^A02^ADT_A02|C2|P|2.5.1" in segments[0]
    pv1 = next(s for s in segments if s.startswith("PV1")).split("|")
    assert pv1[2] == "I" and pv1[3] == "SURG^SURG-03^SURG-03-B^DEMO-HOSPITAL"
    assert pv1[6] == "ICU^ICU-02^ICU-02-A^DEMO-HOSPITAL" and pv1[19] == "stay-00001^^^DEMO-HOSPITAL^VN"
    assert next(s for s in segments if s.startswith("PID")) == "PID|1||pat-0001^^^DEMO-HOSPITAL^PI"
    assert "\n".join(segments) and hl7.parse("\n".join(segments)) == MESSAGES[1]  # LF-separated works too


@pytest.mark.parametrize("message", [
    Hl7Message("ADT^A04", "B1", AT, patient="pat-0001", patient_class="O", visit="v"),  # no route
    Hl7Message("ADT^A08", "B2", AT, patient="pat-0001", patient_class="E", visit="v"),  # no EVN-4
    Hl7Message("ADT^A01", "B3", AT, patient="pat-0001", patient_class="X", visit="v"),  # bad class
    Hl7Message("ADT^A01", "B4", AT, patient_class="E", visit="v"),  # no patient
    Hl7Message("ORM^O01", "B5", AT, patient="pat-0001", visit="v"),  # no order
])
def test_unmappable_messages_are_parked_with_an_error_ack(fresh_state, message):
    bus = InProcessEventBus()
    event, code = Hl7EventAdapter(bus).receive(message)
    assert event is None and code == "AE"
    parked = [d for d in dead_letters(fresh_state).values() if d.adapter == "hl7-adt-feed"]
    assert len(parked) == 1 and parked[0].payload["control_id"] == message.control_id and parked[0].correlation_id


def test_unparseable_text_is_rejected(fresh_state):
    event, ack = Hl7EventAdapter(InProcessEventBus()).receive_er7("hello")
    assert event is None and "MSA|AR|" in ack


def test_a_real_feed_sends_the_mrn_and_the_adapter_looks_it_up(fresh_state):
    patient = fresh_state.fhir.read("Patient", "pat-0042")
    mrn = next(i["value"] for i in patient["identifier"] if i["system"] == "urn:demo-hospital:mrn")
    message = Hl7Message("ADT^A01", "M1", AT, mrn=mrn, patient_class="E", location="ED", visit="ed-99999")
    assert "PID|1||" + mrn + "^^^DEMO-HOSPITAL^MR" in message.er7()
    bus = InProcessEventBus()
    received = []
    bus.subscribe("patient.admitted", received.append, consumer="t.mrn")
    event, code = Hl7EventAdapter(bus).receive(hl7.parse(message.er7()))
    assert code == "AA" and event.patient == "pat-0042" and event.refs["patient"] == "Patient/pat-0042"
    assert mrn not in str(event.model_dump()), "the MRN does not travel on the bus"
    bus.drain()
    assert [e.event_id for e in received] == [event.event_id]


def test_inbound_endpoint_acks_and_publishes(client, login):
    admin, ops = login("U-ADMIN"), login("U-OPS")
    text = MESSAGES[0].er7()
    assert client.post("/api/hospital/hl7", content=text, headers=ops).status_code == 403
    body = client.post("/api/hospital/hl7", content=text, headers={**admin, "Content-Type": "text/plain"}).json()
    assert "MSA|AA|C1" in body["ack"] and body["event"]["type"] == "patient.admitted"
    log = client.get("/api/hospital/events?limit=1", headers=ops).json()["events"]
    assert log[0]["message_id"] == "C1"
    bad = client.post("/api/hospital/hl7", content="MSH|^~\\&|X|Y|||20261008||ADT^A99|Z1|P|2.5.1",
                      headers={**admin, "Content-Type": "text/plain"}).json()
    assert "MSA|AE|Z1" in bad["ack"] and bad["event"] is None


def test_platform_events_need_a_known_type():
    event = platform_event("consent.revoked", at=AT, actor="user:U-OPS",
                           refs={"patient": "Patient/pat-0001", "consent": "Consent/consent-000001"}, key="k1")
    assert event.patient == "pat-0001" and event.source == "platform" and event.event_id == event_id_for("k1")
    with pytest.raises(ValueError):
        platform_event("consent.misplaced", at=AT, actor="x", refs={})
