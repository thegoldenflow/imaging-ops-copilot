"""Shared domain entities. Field names loosely follow FHIR resources so a real
RIS data source can replace the in-memory store later with few changes."""

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class Role(StrEnum):
    FRONT_DESK = "front_desk"
    TECHNOLOGIST = "technologist"
    RADIOLOGIST = "radiologist"
    OPERATIONS_MANAGER = "operations_manager"
    MEDICAL_DIRECTOR = "medical_director"
    ADMIN = "admin"
    REFERRER = "referrer"


class Modality(StrEnum):
    MRI = "MRI"
    CT = "CT"
    US = "US"
    XR = "XR"


class AppointmentStatus(StrEnum):
    BOOKED = "booked"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"
    COMPLETED = "completed"


ACTIVE_STATUSES = {AppointmentStatus.BOOKED, AppointmentStatus.CONFIRMED}


class Site(BaseModel):  # FHIR Location
    id: str
    name: str
    address: str
    phone: str
    open_hour: int
    close_hour: int
    open_days: list[int]  # 0 = Monday
    modalities: list[Modality]
    # Position on a fictional map, in km, used for "nearby site" suggestions.
    x_km: float
    y_km: float
    parking: str


class Scanner(BaseModel):  # FHIR Device
    id: str
    site_id: str
    modality: Modality
    name: str
    slot_minutes: int


class Exam(BaseModel):
    code: str
    name: str
    modality: Modality
    minutes: int
    value: int  # relative value used in waitlist ranking (1-5)
    prep_hours: int  # minimum notice needed for preparation
    contrast: bool = False


class Patient(BaseModel):  # FHIR Patient; name, dob, health card, phone, address are PHI
    id: str
    given_name: str
    family_name: str
    dob: date
    sex: str
    phone: str
    email: str
    address: str
    health_card: str
    health_card_version: str
    preferred_language: str  # en, fr, zh, pa

    @property
    def full_name(self) -> str:
        return f"{self.given_name} {self.family_name}"


class Referrer(BaseModel):  # FHIR Practitioner
    id: str
    name: str
    specialty: str
    clinic: str
    phone: str
    fax: str
    is_key: bool


class Appointment(BaseModel):  # FHIR Appointment
    id: str
    patient_id: str
    referrer_id: str
    site_id: str
    scanner_id: str
    exam_code: str
    start: datetime
    end: datetime
    status: AppointmentStatus
    urgency: str  # P1 (most urgent) .. P4
    booked_at: datetime
    reminder_confirmed: bool = False
    cancel_reason: str | None = None
    no_show_risk: float | None = None
    risk_factors: list[str] = Field(default_factory=list)
    extra_reminder: bool = False
    requisition_id: str | None = None
    protocol_id: str | None = None


class WaitlistEntry(BaseModel):
    id: str
    patient_id: str
    referrer_id: str
    exam_code: str
    urgency: str
    acceptable_site_ids: list[str]
    earliest_date: date
    added_at: datetime
    notes: str = ""
    active: bool = True
    requisition_id: str | None = None
    protocol_id: str | None = None
    duration_minutes: int | None = None  # from the approved protocol


class ImagingStudy(BaseModel):  # FHIR ImagingStudy
    id: str
    appointment_id: str | None
    patient_id: str
    referrer_id: str
    exam_code: str
    performed_at: datetime
    image_key: str  # key into the in-memory image store
    study_uid: str
    indication: str = ""
    source_facility: str | None = None  # set for priors imported from outside archives
    prior_for_appointment_id: str | None = None


class Requisition(BaseModel):  # FHIR ServiceRequest
    id: str
    patient_id: str
    referrer_id: str
    received_at: datetime
    channel: str  # fax, portal, online_form
    text: str  # the requisition as received (contains PHI)
    status: str = "received"  # received, processing, ready, waitlisted, booked, failed
    appointment_id: str | None = None
    waitlist_id: str | None = None


class LabResult(BaseModel):  # FHIR Observation
    id: str
    patient_id: str
    code: str  # egfr
    value: float
    unit: str
    taken_at: datetime


class Allergy(BaseModel):  # FHIR AllergyIntolerance
    id: str
    patient_id: str
    substance: str
    reaction: str
    severity: str  # mild, moderate, severe
    contrast: bool


class StaffUser(BaseModel):  # FHIR PractitionerRole
    id: str
    name: str
    role: Role
    site_ids: list[str]  # empty = all sites
    referrer_id: str | None = None


class MessageOutbox(BaseModel):  # FHIR Communication
    id: str
    channel: str  # sms, email, phone
    kind: str  # reminder_72h, reminder_24h, waitlist_offer, prereg_link, prep, ...
    patient_id: str | None
    to: str
    language: str
    body: str
    appointment_id: str | None = None
    scheduled_for: datetime
    status: str = "scheduled"  # scheduled, sent, failed, cancelled
    sent_at: datetime | None = None
    note: str | None = None  # staff-facing, never sent to the patient


class AuditEvent(BaseModel):  # FHIR AuditEvent
    seq: int
    ts: datetime
    user_id: str
    user_name: str
    role: str
    action: str  # read, create, update, delete, login, export
    resource_type: str
    resource_id: str | None
    outcome: str  # allowed, denied
    source_ip: str | None
    reason: str
    prev_hash: str
    hash: str


class LlmCall(BaseModel):
    id: str
    ts: datetime
    task: str
    model: str
    mode: str  # anthropic or mock
    prompt_version: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    cost_usd: float
    outcome: str  # ok, retried_ok, needs_human, unavailable
    input_hash: str
    error: str | None = None
