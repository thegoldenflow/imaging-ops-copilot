"""Hospital role-based access (spec 6.3), enforced by FhirGateway and the hospital API.

| Role | Sees | Writes |
| --- | --- | --- |
| physician | every resource of the patients of their units (or whom they attend); others need break-glass | Task; DocumentReference drafts of modules they sign for; signs per the registry |
| nurse | the same for their unit | Task, Flag, Communication; DocumentReference drafts of modules they sign for |
| pharmacist | hospital-wide: patient, encounter, medications, allergies, lab results, medication documents | DocumentReference drafts of modules they sign for (medication reconciliation, order review) |
| clerk | hospital-wide: demographics, encounters without clinical content, scheduling, consents, registration tasks | Appointment (proposed), registration Task, Consent |
| operations_manager | hospital-wide aggregates: beds, encounters without clinical content, OR and bed requests, flags; a patient only as MRN | action Task (performer `ops`), Encounter.location (bed move) |
| admin | no patient data: locations, organisation, staff (plus the audit log and the registry, outside the EHR) | nothing in the EHR |

Patient scope for the unit-scoped roles (physician, nurse): the patient has an
encounter that is active, or finished within the last 72 hours (hospital clock),
whose current or last location is in one of the user's units, or the user's
practitioner is a participant of such an encounter. A patient outside that scope
needs a break-glass grant (app/ehr/breakglass.py).

Imaging-centre roles (front desk, technologist, radiologist, medical director,
referrer) have no access to the hospital EHR. System actors (the HL7 adapter,
scripts) are trusted and only audited; agents get their own policies in WP4b.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.ehr.codes import DOC_TYPES
from app.ehr.fhirstore import unit_of
from app.fhir.dt import parse, ref_id

ACTIVE_ENCOUNTER = ("arrived", "triaged", "in-progress", "onleave")
RECENT_DISCHARGE = timedelta(hours=72)

ALL_TYPES = frozenset({"Patient", "Encounter", "Location", "Organization", "Practitioner", "PractitionerRole",
                       "Appointment", "Schedule", "Slot", "ServiceRequest", "MedicationRequest",
                       "MedicationStatement", "AllergyIntolerance", "Observation", "DiagnosticReport", "Procedure",
                       "Condition", "CarePlan", "DocumentReference", "Task", "Flag", "Communication", "Provenance",
                       "Consent", "EpisodeOfCare"})
DIRECTORY = frozenset({"Location", "Organization", "Practitioner", "PractitionerRole"})

# Task.performerType / PractitionerRole codes per role.
ROLE_CODE = {"physician": "doctor", "nurse": "nurse", "pharmacist": "pharmacist", "clerk": "clerk",
             "operations_manager": "ops"}
MEDICATION_DOCS = frozenset({DOC_TYPES["medrec"][1], DOC_TYPES["discharge_med_list"][1]})
# Encounter and scheduling elements that carry clinical content.
CLINICAL_ELEMENTS = {
    "Encounter": ("reasonCode", "reasonReference", "diagnosis"),
    "Appointment": ("reasonCode", "reasonReference", "description", "comment", "patientInstruction"),
    "ServiceRequest": ("reasonCode", "reasonReference", "note", "patientInstruction", "orderDetail", "supportingInfo"),
}


@dataclass(frozen=True)
class RolePolicy:
    scope: str  # unit: own units' patients (others need break-glass); hospital: every patient; none: no patients
    read: frozenset[str]
    write: frozenset[str]  # resource types (and "Encounter.location") the role may create or change
    signs: bool = False  # may sign documents (which ones: the module's required_signoff_role)
    break_glass: bool = False
    reduce: tuple[str, ...] = ()  # resource types shown without clinical content
    mrn_only: bool = False  # sees a patient only as MRN


POLICIES: dict[str, RolePolicy] = {
    "physician": RolePolicy("unit", ALL_TYPES, frozenset({"Task", "DocumentReference"}), signs=True,
                            break_glass=True),
    "nurse": RolePolicy("unit", ALL_TYPES, frozenset({"Task", "Flag", "Communication", "DocumentReference"}),
                        signs=True, break_glass=True),
    "pharmacist": RolePolicy("hospital", DIRECTORY | {"Patient", "Encounter", "MedicationRequest",
                                                      "MedicationStatement", "AllergyIntolerance", "Observation",
                                                      "DocumentReference"},
                             frozenset({"DocumentReference"}), signs=True),
    "clerk": RolePolicy("hospital", DIRECTORY | {"Patient", "Encounter", "Appointment", "Schedule", "Slot",
                                                 "Consent", "Task"},
                        frozenset({"Appointment", "Task", "Consent"}), reduce=("Encounter",)),
    "operations_manager": RolePolicy("hospital", DIRECTORY | {"Patient", "Encounter", "Appointment", "Schedule",
                                                              "Slot", "ServiceRequest", "Task", "Flag"},
                                     frozenset({"Task", "Encounter.location"}),
                                     reduce=("Encounter", "Appointment", "ServiceRequest"), mrn_only=True),
    "admin": RolePolicy("none", DIRECTORY, frozenset()),
}
NO_ACCESS = RolePolicy("none", frozenset(), frozenset())
CONSENT_RECORDERS = frozenset({"clerk", "nurse", "physician"})


def policy_for(role: str, kind: str = "user") -> RolePolicy | None:
    """The role's policy; None for trusted system actors (no role restrictions, still audited)."""
    if kind == "system":
        return None
    return POLICIES.get(str(role), NO_ACCESS)


# ---------- per-resource rules ----------


def _codes(concept) -> set[str]:
    concepts = concept if isinstance(concept, list) else [concept]
    return {c.get("code") for cc in concepts if isinstance(cc, dict) for c in cc.get("coding") or []}


def performer_codes(task: dict) -> set[str]:
    return _codes(task.get("performerType") or [])


def doc_type(document: dict) -> str | None:
    codes = _codes(document.get("type"))
    return next(iter(codes), None)


def can_see(role: str, resource: dict) -> bool:
    """Finer rules inside a readable type (the type itself is checked separately)."""
    rtype = resource.get("resourceType")
    if role == "pharmacist":
        if rtype == "Observation":
            return "laboratory" in _codes(resource.get("category") or [])
        if rtype == "DocumentReference":
            return doc_type(resource) in MEDICATION_DOCS
    if role == "clerk" and rtype == "Task":
        return ROLE_CODE["clerk"] in performer_codes(resource)
    if role == "operations_manager":
        if rtype == "Task":
            return ROLE_CODE["operations_manager"] in performer_codes(resource)
        if rtype == "ServiceRequest":
            return bool({"bed-request", "surgery"} & _codes(resource.get("category") or []))
    return True


def reduce(policy: RolePolicy | None, resource: dict) -> dict:
    """The resource as the role may see it: the operations manager sees a patient as MRN only, and
    encounters, appointments and requests lose their clinical elements for the non-clinical roles."""
    if policy is None:
        return resource
    rtype = resource.get("resourceType")
    if rtype == "Patient" and policy.mrn_only:
        from app.ehr.codes import MRN_SYSTEM

        return {"resourceType": "Patient", "id": resource["id"], "active": resource.get("active", True),
                "identifier": [i for i in resource.get("identifier") or [] if i.get("system") == MRN_SYSTEM]}
    if rtype in policy.reduce:
        return {k: v for k, v in resource.items() if k not in CLINICAL_ELEMENTS.get(rtype, ()) and k != "text"}
    return resource


def write_refusal(policy: RolePolicy | None, role: str, resource: dict, signoff_roles: list[str]) -> str | None:
    """Why the role may not create or change this resource (None: allowed)."""
    if policy is None:
        return None
    rtype = resource.get("resourceType")
    if rtype not in policy.write:
        return f"the {role} role may not write {rtype}"
    if rtype == "Task" and role in ("clerk", "operations_manager"):
        if ROLE_CODE[role] not in performer_codes(resource):
            kind = "registration" if role == "clerk" else "action"
            return f"the {role} role writes only {kind} tasks (performerType {ROLE_CODE[role]})"
    if rtype == "DocumentReference" and role not in signoff_roles:
        return f"documents of this module are signed by {', '.join(signoff_roles) or 'nobody'}, not the {role} role"
    return None


# ---------- patient scope ----------


def _last_location(encounter: dict) -> str | None:
    locations = encounter.get("location") or []
    current = next((loc for loc in reversed(locations) if loc.get("status") == "active"), None)
    chosen = current or (locations[-1] if locations else None)
    return ref_id(chosen["location"]) if chosen else None


def in_scope(encounters: list[dict], unit_ids: tuple[str, ...], practitioner_id: str | None, now: datetime) -> bool:
    """Whether one of the patient's current or recent encounters is in the user's units or attended by them."""
    for enc in encounters:
        status = enc.get("status")
        if status not in ACTIVE_ENCOUNTER:
            end = parse((enc.get("period") or {}).get("end"))
            if status != "finished" or end is None or end < now - RECENT_DISCHARGE:
                continue
        if unit_of(_last_location(enc)) in unit_ids:
            return True
        if practitioner_id and any(ref_id(p.get("individual")) == practitioner_id
                                   for p in enc.get("participant") or []):
            return True
    return False


def patient_of(resource: dict | None) -> str | None:
    """The patient a resource belongs to (None for directory resources)."""
    if not resource:
        return None
    if resource.get("resourceType") == "Patient":
        return resource.get("id")
    for key in ("subject", "patient", "for"):
        value = resource.get(key)
        if isinstance(value, dict) and (value.get("reference") or "").startswith("Patient/"):
            return ref_id(value)
    for participant in resource.get("participant") or []:  # Appointment
        actor = (participant.get("actor") or {}).get("reference") or ""
        if actor.startswith("Patient/"):
            return actor.split("/", 1)[1]
    return None


def encounter_of(resource: dict | None) -> str | None:
    if not resource:
        return None
    if resource.get("resourceType") == "Encounter":
        return resource.get("id")
    enc = resource.get("encounter") or (resource.get("context") or {}).get("encounter")
    if isinstance(enc, list):
        enc = enc[0] if enc else None
    return ref_id(enc) if isinstance(enc, dict) else None
