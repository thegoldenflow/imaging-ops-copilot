"""FHIR R4 resource types (spec 6.1): one module per resource, only the fields this project uses."""

from pydantic import BaseModel

from app.fhir.types.allergy_intolerance import AllergyIntolerance
from app.fhir.types.appointment import Appointment
from app.fhir.types.care_plan import CarePlan
from app.fhir.types.communication import Communication
from app.fhir.types.condition import Condition
from app.fhir.types.consent import Consent
from app.fhir.types.diagnostic_report import DiagnosticReport
from app.fhir.types.document_reference import DocumentReference
from app.fhir.types.encounter import Encounter
from app.fhir.types.episode_of_care import EpisodeOfCare
from app.fhir.types.flag import Flag
from app.fhir.types.location import Location
from app.fhir.types.medication_request import MedicationRequest
from app.fhir.types.medication_statement import MedicationStatement
from app.fhir.types.observation import Observation
from app.fhir.types.organization import Organization
from app.fhir.types.patient import Patient
from app.fhir.types.practitioner import Practitioner
from app.fhir.types.practitioner_role import PractitionerRole
from app.fhir.types.procedure import Procedure
from app.fhir.types.provenance import Provenance
from app.fhir.types.schedule import Schedule
from app.fhir.types.service_request import ServiceRequest
from app.fhir.types.slot import Slot
from app.fhir.types.task import Task

RESOURCE_TYPES: dict[str, type[BaseModel]] = {cls.__name__: cls for cls in (Patient, Encounter, Location, Organization, Appointment, Schedule, Slot, ServiceRequest, MedicationRequest, MedicationStatement, AllergyIntolerance, Observation, DiagnosticReport, Procedure, Condition, CarePlan, DocumentReference, Task, Flag, Communication, Practitioner, PractitionerRole, Provenance, Consent, EpisodeOfCare)}


def validate(resource: dict) -> BaseModel:
    """Parse a resource with its type: KeyError for an unknown type, ValidationError when invalid."""
    return RESOURCE_TYPES[resource["resourceType"]].model_validate(resource)
