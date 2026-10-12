"""HL7 v2 messages from the EHR and their mapping to domain events (spec 6.2).

A hospital's EHR announces changes as HL7 v2 messages to an interface engine:
ADT for arrivals, transfers and discharges, ORM/ORU for orders and results, SIU
for scheduling. In the demo the day simulator (app/ehr/simulator.py) plays the
EHR and sends them; `Hl7EventAdapter` plays the interface engine's HL7 → event
converter. Subscribers only ever see domain events (app/ehr/events.py); the
message types stop here. Replacing the simulator with a real feed means feeding
real messages into the same adapter (an MLLP listener is not part of the demo).

Message → event (ROUTES):

    ADT^A01  patient.admitted        ED arrival (PV1-2 E), admission (I), day-surgery check-in (O)
    ADT^A02  patient.transferred     bed move, PV1-6 is the bed left
    ADT^A03  patient.discharged      end of the ED visit, stay or day-surgery visit; PV1-36 disposition
    ADT^A08  encounter.updated       EVN-4 says what changed: TRIAGED, SEEN, DECISION, ALC
    ADT^A20  bed.status_changed      NPU: bed and its status (v2 table 0116, as FHIR operationalStatus)
    ORM^O01  order.placed            ORC-2 placer order number = ServiceRequest id
    ORU^R01  result.available        OBR-2 order, OBX-21 the Observation / DiagnosticReport ids
    SIU^S12  appointment.scheduled   SCH-1 = Appointment id, SCH-25 status
    SIU^S14  appointment.updated
    SIU^S15  appointment.cancelled

What goes into a message here: resource ids and codes only. PID-3 carries the
FHIR Patient id with identifier type PI (no MRN, no name, no birth date), PV1-19
the Encounter id with type VN, locations are unit^room^bed. A real feed sends the
MRN (type MR); the adapter looks it up through FhirGateway. ER7 rendering and
parsing below cover the segments and fields this mapping reads, not the whole
standard.

Adapter contract (app/integrations/contract.py): every message is logged with its
correlation id and never its content; a message the adapter cannot map is
rejected (ACK AR/AE) and parked as a dead letter.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime

from app.ehr.events import DomainEvent, event_id_for

FACILITY = "DEMO-HOSPITAL"
EHR_APP = "DEMO-EHR"
VERSION = "2.5.1"

ROUTES: dict[str, str] = {
    "ADT^A01": "patient.admitted",
    "ADT^A02": "patient.transferred",
    "ADT^A03": "patient.discharged",
    "ADT^A08": "encounter.updated",
    "ADT^A20": "bed.status_changed",
    "ORM^O01": "order.placed",
    "ORU^R01": "result.available",
    "SIU^S12": "appointment.scheduled",
    "SIU^S14": "appointment.updated",
    "SIU^S15": "appointment.cancelled",
}
STRUCTURE = {"ADT^A01": "ADT_A01", "ADT^A02": "ADT_A02", "ADT^A03": "ADT_A03", "ADT^A08": "ADT_A01",
             "ADT^A20": "ADT_A20", "ORM^O01": "ORM_O01", "ORU^R01": "ORU_R01", "SIU^S12": "SIU_S12",
             "SIU^S14": "SIU_S12", "SIU^S15": "SIU_S12"}
PATIENT_CLASS = {"E": "EMER", "I": "IMP", "O": "AMB"}
CLASS_CODE = {v: k for k, v in PATIENT_CLASS.items()}
# OBR-24 diagnostic service section (v2 table 0074 where it has a code; VS and CON are local)
SERVICE_SECTION = {"lab": "LAB", "laboratory": "LAB", "imaging": "RAD", "consult": "CON", "vital-signs": "VS"}
CATEGORY = {"LAB": "laboratory", "RAD": "imaging", "CON": "consult", "VS": "vital-signs"}
A08_REASONS = {"TRIAGED": "triaged", "SEEN": "seen", "DECISION": "decision", "ALC": "alc"}


class Hl7Error(ValueError):
    """The message cannot be parsed or mapped (ACK AR / AE)."""


@dataclass(frozen=True)
class Hl7Message:
    """The fields of an HL7 v2 message this adapter reads, already parsed."""

    message_type: str  # MSH-9 "ADT^A01"
    control_id: str  # MSH-10
    event_at: datetime  # EVN-6 (event occurred), hospital time
    sending_app: str = EHR_APP  # MSH-3
    operator: str | None = None  # EVN-5: the person who triggered the event (a scripted scenario's user)
    reason: str | None = None  # EVN-4: for A08, what changed
    patient: str | None = None  # PID-3 with type PI: FHIR Patient id
    mrn: str | None = None  # PID-3 with type MR (a real feed)
    patient_class: str | None = None  # PV1-2: E, I, O
    location: str | None = None  # PV1-3 (deepest of unit^room^bed), or NPU-1 for A20
    prior_location: str | None = None  # PV1-6
    visit: str | None = None  # PV1-19: Encounter id
    disposition: str | None = None  # PV1-36
    order: str | None = None  # ORC-2 / OBR-2: ServiceRequest id
    section: str | None = None  # OBR-24: LAB, RAD, CON, VS
    results: tuple[str, ...] = ()  # OBX-21: "Observation/<id>" or "DiagnosticReport/<id>"
    appointment: str | None = None  # SCH-1: Appointment id
    appointment_status: str | None = None  # SCH-25: Booked, Arrived, Complete, Cancelled
    bed_status: str | None = None  # NPU-2: U, O, K, C
    correlation_id: str | None = field(default=None, compare=False)  # interface-engine metadata, not in ER7

    @property
    def trigger(self) -> str:
        return self.message_type.split("^")[1]

    def er7(self) -> str:
        return render(self)


# ---------- ER7 (pipe-delimited) ----------


def _ts(value: datetime) -> str:
    return value.strftime("%Y%m%d%H%M%S")


def _parse_ts(value: str) -> datetime:
    value = value.split("+")[0].split("-")[0].split(".")[0]
    return datetime.strptime(value.ljust(14, "0")[:14], "%Y%m%d%H%M%S")


def _segment(name: str, fields: dict[int, str | None]) -> str:
    width = max(fields) if fields else 0
    values = [fields.get(i) or "" for i in range(1, width + 1)]
    return "|".join([name, *values]).rstrip("|")


def _location(loc: str | None) -> str | None:
    """Location id → PL data type: point of care^room^bed^facility."""
    if not loc:
        return None
    parts = loc.split("-")
    unit = parts[0]
    room = "-".join(parts[:2]) if len(parts) >= 2 else ""
    bed = loc if len(parts) >= 3 else ""
    return f"{unit}^{room}^{bed}^{FACILITY}"


def _location_id(pl: str) -> str | None:
    comps = pl.split("^")
    for value in reversed(comps[:3]):
        if value:
            return value
    return None


def render(m: Hl7Message) -> str:
    segments = [
        _segment("MSH", {1: "^~\\&", 2: m.sending_app, 3: FACILITY, 4: "IOC", 5: "IOC", 6: _ts(m.event_at),
                         8: f"{m.message_type}^{STRUCTURE.get(m.message_type, '')}".rstrip("^"),
                         9: m.control_id, 10: "P", 11: VERSION}),
        _segment("EVN", {1: m.trigger, 2: _ts(m.event_at), 4: m.reason, 5: m.operator, 6: _ts(m.event_at)}),
    ]
    if m.message_type == "ADT^A20":
        segments.append(_segment("NPU", {1: _location(m.location), 2: m.bed_status}))
        return "\r".join(segments)
    if m.patient or m.mrn:
        pid = f"{m.patient}^^^{FACILITY}^PI" if m.patient else f"{m.mrn}^^^{FACILITY}^MR"
        segments.append(_segment("PID", {1: "1", 3: pid}))
    if m.message_type.startswith("SIU"):
        segments.insert(2, _segment("SCH", {1: m.appointment, 25: m.appointment_status}))
    if m.visit or m.patient_class:
        segments.append(_segment("PV1", {1: "1", 2: m.patient_class, 3: _location(m.location),
                                         6: _location(m.prior_location),
                                         19: f"{m.visit}^^^{FACILITY}^VN" if m.visit else None,
                                         36: m.disposition}))
    if m.order:
        control = {"ORM^O01": "NW", "ORU^R01": "RE"}.get(m.message_type, "SC")
        segments.append(_segment("ORC", {1: control, 2: m.order}))
        segments.append(_segment("OBR", {1: "1", 2: m.order, 24: m.section}))
    for i, result in enumerate(m.results, start=1):
        segments.append(_segment("OBX", {1: str(i), 2: "RP", 21: result}))
    return "\r".join(segments)


def parse(text: str) -> Hl7Message:
    """ER7 text → Hl7Message (segments separated by CR, LF or CRLF)."""
    lines = [line for line in text.replace("\r\n", "\r").replace("\n", "\r").split("\r") if line.strip()]
    if not lines or not lines[0].startswith("MSH"):
        raise Hl7Error("not an HL7 v2 message (no MSH segment)")
    segs: dict[str, list[list[str]]] = {}
    for line in lines:
        fields = line.split("|")
        if fields[0] == "MSH":
            fields = ["MSH", "|", *fields[1:]]  # MSH-1 is the field separator itself
        segs.setdefault(fields[0], []).append(fields)

    def get(seg: str, i: int, occurrence: int = 0) -> str:
        rows = segs.get(seg) or []
        if occurrence >= len(rows) or i >= len(rows[occurrence]):
            return ""
        return rows[occurrence][i]

    msg_type = "^".join(get("MSH", 9).split("^")[:2])
    control = get("MSH", 10)
    if not msg_type or not control:
        raise Hl7Error("MSH-9 message type and MSH-10 control id are required")
    when = get("EVN", 6) or get("EVN", 2) or get("MSH", 7)
    if not when:
        raise Hl7Error("no event time (EVN-6, EVN-2 or MSH-7)")
    patient = mrn = None
    pid = get("PID", 3)
    if pid:
        comps = pid.split("^")
        id_type = comps[4] if len(comps) > 4 else ""
        if id_type == "MR":
            mrn = comps[0]
        else:
            patient = comps[0]
    visit = get("PV1", 19).split("^")[0] or None
    results = tuple(get("OBX", 21, i) for i in range(len(segs.get("OBX") or [])) if get("OBX", 21, i))
    location = _location_id(get("NPU", 1)) if msg_type == "ADT^A20" else _location_id(get("PV1", 3))
    return Hl7Message(
        message_type=msg_type, control_id=control, event_at=_parse_ts(when), sending_app=get("MSH", 3) or EHR_APP,
        operator=get("EVN", 5) or None, reason=get("EVN", 4) or None, patient=patient, mrn=mrn,
        patient_class=get("PV1", 2) or None, location=location, prior_location=_location_id(get("PV1", 6)),
        visit=visit, disposition=get("PV1", 36) or None, order=get("ORC", 2) or get("OBR", 2) or None,
        section=get("OBR", 24) or None, results=results, appointment=get("SCH", 1) or None,
        appointment_status=get("SCH", 25) or None, bed_status=get("NPU", 2) or None)


def ack(message_type: str | None, control_id: str | None, code: str, text: str = "") -> str:
    """An ACK: AA accepted, AE error (could not be processed), AR rejected (not understood)."""
    trigger = message_type.split("^")[1] if message_type and "^" in message_type else ""
    msh = _segment("MSH", {1: "^~\\&", 2: "IOC", 3: "IOC", 4: EHR_APP, 5: FACILITY, 6: _ts(datetime.now()),
                           8: f"ACK^{trigger}^ACK" if trigger else "ACK", 9: uuid.uuid4().hex[:20], 10: "P",
                           11: VERSION})
    return "\r".join([msh, _segment("MSA", {1: code, 2: control_id or "", 3: text[:80] or None})])


# ---------- message → domain event ----------


def _refs(m: Hl7Message, patient_id: str | None) -> dict[str, str | list[str]]:
    refs: dict[str, str | list[str]] = {}
    if patient_id:
        refs["patient"] = f"Patient/{patient_id}"
    if m.visit:
        refs["encounter"] = f"Encounter/{m.visit}"
    if m.location:
        refs["location"] = f"Location/{m.location}"
    if m.prior_location:
        refs["from_location"] = f"Location/{m.prior_location}"
    if m.order:
        refs["order"] = f"ServiceRequest/{m.order}"
    if m.results:
        refs["results"] = list(m.results)
    if m.appointment:
        refs["appointment"] = f"Appointment/{m.appointment}"
    return refs


def to_event(m: Hl7Message, *, patient_id: str | None = None) -> DomainEvent:
    """Map one message to its domain event (pure: no lookups, no I/O)."""
    event_type = ROUTES.get(m.message_type)
    if event_type is None:
        raise Hl7Error(f"no route for message type {m.message_type}")
    patient_id = patient_id or m.patient
    attrs: dict[str, str | int | bool | None] = {}
    if m.patient_class:
        if m.patient_class not in PATIENT_CLASS:
            raise Hl7Error(f"unknown patient class PV1-2 {m.patient_class!r}")
        attrs["encounter_class"] = PATIENT_CLASS[m.patient_class]
    if event_type == "patient.discharged" and m.disposition:
        attrs["disposition"] = m.disposition
    if event_type == "encounter.updated":
        if (m.reason or "") not in A08_REASONS:
            raise Hl7Error(f"ADT^A08 needs EVN-4 one of {sorted(A08_REASONS)}")
        attrs["change"] = A08_REASONS[m.reason]
        if m.disposition:
            attrs["disposition"] = m.disposition
    if event_type == "bed.status_changed":
        if not m.location or not m.bed_status:
            raise Hl7Error("ADT^A20 needs NPU-1 bed and NPU-2 status")
        attrs["status"] = m.bed_status
    if event_type == "order.placed" and not m.order:
        raise Hl7Error("ORM^O01 needs ORC-2 placer order number")
    if event_type == "result.available" and not (m.order or m.results):
        raise Hl7Error("ORU^R01 needs OBR-2 placer order number or OBX-21 results")
    if event_type in ("order.placed", "result.available"):
        attrs["category"] = CATEGORY.get(m.section or "", m.section)
    if event_type.startswith("appointment."):
        if not m.appointment:
            raise Hl7Error(f"{m.message_type} needs SCH-1 appointment id")
        attrs["status"] = m.appointment_status
    if event_type.startswith("patient.") and not (patient_id and m.visit):
        raise Hl7Error(f"{m.message_type} needs PID-3 and PV1-19")
    return DomainEvent(
        event_id=event_id_for(f"hl7:{m.sending_app}:{m.control_id}"), type=event_type, occurred_at=m.event_at,
        correlation_id=m.correlation_id or event_id_for(f"corr:{m.sending_app}:{m.control_id}").replace("-", ""),
        actor=f"user:{m.operator}" if m.operator else f"system:{m.sending_app.lower()}", source=m.message_type,
        message_id=m.control_id, patient=patient_id, encounter=m.visit, refs=_refs(m, patient_id), attrs=attrs)


class Hl7EventAdapter:
    """The interface engine's converter: HL7 message in, domain event published (or a dead letter)."""

    def __init__(self, bus=None) -> None:
        from app.ehr.events import bus as default_bus
        from app.integrations.contract import Adapter

        self.bus = bus or default_bus
        self.contract = Adapter("hl7-adt-feed")  # inbound logging and dead letters under the contract

    def _patient_for_mrn(self, mrn: str) -> str | None:
        from app.ehr.gateway import Actor, FhirGateway

        found = FhirGateway(Actor.system("hl7-adapter"), module="event_bus").get_patient(mrn)
        return found.id if found else None

    def _map(self, message: Hl7Message) -> DomainEvent:
        patient_id = message.patient
        if patient_id is None and message.mrn:
            patient_id = self._patient_for_mrn(message.mrn)
            if patient_id is None:
                raise Hl7Error("unknown MRN in PID-3")
        return to_event(message, patient_id=patient_id)

    def _received(self, message: Hl7Message) -> Hl7Message:
        return replace(message, correlation_id=self.contract.receive(message.message_type, message.correlation_id))

    def convert(self, message: Hl7Message) -> DomainEvent:
        """Map without publishing (the simulator publishes its events in batches); raises Hl7Error."""
        return self._map(self._received(message))

    def receive(self, message: Hl7Message) -> tuple[DomainEvent | None, str]:
        """One inbound message: publish its event, or park the message. Returns (event, ACK code)."""
        message = self._received(message)
        try:
            event = self._map(message)
        except Hl7Error as e:
            self.contract.dead_letter(message.message_type, {"control_id": message.control_id,
                                                             "message_type": message.message_type},
                                      message.correlation_id or "", str(e), 1)
            return None, "AE"
        return self.bus.publish(event), "AA"

    def receive_er7(self, text: str) -> tuple[DomainEvent | None, str]:
        """Raw ER7 text (the inbound endpoint). Returns (event, ACK message)."""
        try:
            message = parse(text)
        except Hl7Error as e:
            self.contract.dead_letter("unparsed", {"er7": text[:4000]}, "", str(e), 1)  # payload encrypted at rest
            return None, ack(None, None, "AR", str(e))
        event, code = self.receive(message)
        return event, ack(message.message_type, message.control_id, code,
                          "" if event else "message could not be mapped; parked for review")
