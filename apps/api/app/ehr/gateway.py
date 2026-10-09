"""FhirGateway (spec 6.2, 6.3, 6.4): the only way modules read or write the hospital EHR.

Modules never use the FHIR store or HTTP directly (tests/test_fhir_gateway.py
greps for it). A gateway is opened for one actor (a signed-in user, an agent
acting for one, or a system job) and one purpose module:

    fhir = FhirGateway(Actor.of(user, request), module="control_tower")
    board = fhir.get_bed_board("MEDA")

Access (6.3, app/ehr/access.py): reads are limited to the resource types the
actor's role may see, unit-scoped roles (physician, nurse) see only the patients
of their units (others raise BreakGlassRequired until the user opens a
break-glass grant, app/ehr/breakglass.py), and the non-clinical roles get reduced
views (the operations manager sees a patient as MRN only). System actors are
trusted and only audited.

Writes follow the allow-list of the AI layer, which sits beside the EHR and only
writes work items and drafts, then the agent registry (config/agents, 6.4: the
module needs an entry whose tools' fhir_writes cover the resource and status),
then the role's write rights and patient scope:

    Task                 create and update, any status
    DocumentReference    create as docStatus=preliminary; change only while preliminary
                         (final only through the signing service, sign_document / app/ehr/signing.py)
    Communication, Flag  create and update
    Appointment          create as status=proposed; change only while proposed;
                         booked only through book_appointment, by a clerk (the
                         privileged tool bookAppointment, after a recorded approval)
    Consent              record_consent (consent management, app/ehr/consent.py)
    Encounter.location   append a bed move, bed managers only (append_encounter_location)
    Provenance           record_provenance, the agent runtime only (6.4)

Anything else is refused with FhirAccessDenied and an audit record. Every call
is audited (6.3 fields): actor and role, action and event type, resource type and
id (or the scope: patient, encounter or unit), the patient's MRN hash, the
encounter, the purpose module, and for reads under break-glass the grant.

Backends: `local` (the FHIR store in PostgreSQL, inside the request's unit of
work) and `hapi` (a FHIR server, app/ehr/hapi.py), chosen with FHIR_BACKEND.
Resources passed on to a model go through app/llm/fhir_deid.py first.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.agents import registry
from app.core.audit import mrn_hash
from app.core.models import Role, StaffUser
from app.core.store import Store, get_store
from app.ehr import access, breakglass
from app.ehr.access import ACTIVE_ENCOUNTER
from app.ehr.clock import hospital_now
from app.ehr.codes import BED_STATUS, EXT, MRN_SYSTEM, coding
from app.fhir.dt import fhir_datetime, parse, ref, ref_id
from app.fhir.types import validate
from app.fhir.types.datatypes import FhirModel
from app.fhir.types.document_reference import DocumentReference
from app.fhir.types.encounter import Encounter
from app.fhir.types.medication_request import MedicationRequest
from app.fhir.types.medication_statement import MedicationStatement
from app.fhir.types.observation import Observation
from app.fhir.types.patient import Patient
from app.fhir.types.service_request import ServiceRequest

BED_MANAGER_ROLES = {Role.OPERATIONS_MANAGER}
WRITABLE = ("Task", "DocumentReference", "Communication", "Flag", "Appointment")
SOURCE_MODULE = EXT + "source-module"  # the module that created a document (its registry entry governs signing)


class FhirAccessDenied(PermissionError):
    """A read or write the actor may not make (already audited)."""


class BreakGlassRequired(FhirAccessDenied):
    """A patient outside the user's units: emergency access (break-glass) would open it."""

    def __init__(self, patient_id: str, message: str = "This patient is outside your units; emergency access "
                                                       "(break-glass) is required") -> None:
        super().__init__(message)
        self.patient_id = patient_id


class FhirConflict(ValueError):
    """A write the allow-list permits but the record's current state does not (e.g. the bed is taken)."""


@dataclass(frozen=True)
class Actor:
    id: str
    name: str
    role: str
    kind: str = "user"  # user, agent or system
    source_ip: str | None = None
    unit_ids: tuple[str, ...] = ()  # hospital staff: the units whose patients they see
    practitioner_id: str | None = None

    @classmethod
    def of(cls, user: StaffUser, request=None) -> Actor:
        from app.core.auth import client_ip

        return cls(user.id, user.name, str(user.role), "user", client_ip(request) if request is not None else None,
                   tuple(user.unit_ids), user.practitioner_id)

    @classmethod
    def system(cls, name: str) -> Actor:
        return cls(f"system:{name}", name, "system", "system")


# ---------- backends ----------


class LocalBackend:
    """The FHIR store in PostgreSQL (app/ehr/fhirstore.py), in the current unit of work."""

    name = "local"

    def __init__(self, store: Store | None = None) -> None:
        self._store = store

    @property
    def _fhir(self):
        return (self._store or get_store()).fhir

    def read(self, resource_type: str, resource_id: str) -> dict | None:
        return self._fhir.read(resource_type, resource_id)

    def search(self, resource_type: str, **params: Any) -> list[dict]:
        return self._fhir.search(resource_type, **params)

    def create(self, resource: dict) -> dict:
        return self._fhir.create(resource)

    def update(self, resource: dict) -> dict:
        return self._fhir.update(resource)


_hapi_backend = None


def backend_from_settings():
    from app.core.config import settings

    if settings.fhir_backend == "hapi":
        global _hapi_backend
        if _hapi_backend is None:
            from app.ehr.hapi import HapiBackend

            _hapi_backend = HapiBackend()
        return _hapi_backend
    return LocalBackend()


# ---------- read models ----------


class BedView(BaseModel):
    id: str
    name: str
    room: str
    status: str  # v2-0116: O occupied, U unoccupied, K housekeeping, C closed
    encounter_id: str | None = None
    patient_id: str | None = None
    since: datetime | None = None  # in this bed since


class BedBoard(BaseModel):
    unit_id: str
    unit_name: str
    beds: list[BedView]
    waiting: list[str]  # encounters at the unit without a bed yet (ED waiting room)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for bed in self.beds:
            out[bed.status] = out.get(bed.status, 0) + 1
        return out


def _physical_type(location: dict) -> str | None:
    codings = (location.get("physicalType") or {}).get("coding") or []
    return codings[0].get("code") if codings else None


def _current_location(encounter: dict) -> dict | None:
    return next((loc for loc in reversed(encounter.get("location") or []) if loc.get("status") == "active"), None)


def _codes(observation: dict) -> set[str]:
    codes = {c.get("code") for c in (observation.get("code") or {}).get("coding") or []}
    for component in observation.get("component") or []:
        codes |= {c.get("code") for c in (component.get("code") or {}).get("coding") or []}
    return codes


class CensusEntry(BaseModel):
    """A patient on a unit: where, since when, and who (the name only for roles that see names)."""

    encounter_id: str
    encounter_class: str | None = None
    patient_id: str
    mrn: str | None = None
    name: str | None = None
    bed_id: str | None = None  # None: waiting at the unit without a bed (ED waiting room)
    since: datetime | None = None


# ---------- the gateway ----------


def _status(resource: dict) -> str | None:
    return resource.get("docStatus") if resource.get("resourceType") == "DocumentReference" else resource.get("status")


def source_module(document: dict | None) -> str | None:
    return next((e.get("valueCode") for e in (document or {}).get("extension") or []
                 if e.get("url") == SOURCE_MODULE), None)


class FhirGateway:
    def __init__(self, actor: Actor, module: str, backend=None) -> None:
        self.actor = actor
        self.module = module
        self.backend = backend or backend_from_settings()
        self.policy = access.policy_for(actor.role, actor.kind)  # None: a trusted system actor
        self._patients: dict[str, dict | None] = {}  # patient id -> Patient resource (MRN for the audit hash)
        self._scope: dict[str, str | None] = {}  # patient id -> None (in scope) or the break-glass grant id
        self._enc_patient: dict[str, str | None] = {}  # encounter id -> patient id

    @property
    def role(self) -> str:
        return str(self.actor.role)

    # ----- audit -----

    def _mrn_hash(self, patient_id: str | None) -> str | None:
        if not patient_id:
            return None
        patient = self._patient_resource(patient_id)
        mrn = next((i.get("value") for i in (patient or {}).get("identifier") or [] if i.get("system") == MRN_SYSTEM),
                   None)
        return mrn_hash(mrn)

    def _audit(self, action: str, resource_type: str, resource_id: str | None, *, outcome: str = "allowed",
               detail: str | None = None, patient: str | None = None, encounter: str | None = None,
               event_type: str | None = None, module: str | None = None) -> None:
        grant = self._scope.get(patient) if patient and outcome == "allowed" else None
        reason = "; ".join(x for x in (f"break-glass {grant}" if grant else None, detail) if x)
        get_store().audit.record(
            user_id=self.actor.id, user_name=self.actor.name, role=self.actor.role, action=action,
            resource_type=resource_type, resource_id=resource_id, outcome=outcome, source_ip=self.actor.source_ip,
            reason=reason, event_type=event_type, patient_mrn_hash=self._mrn_hash(patient), encounter_id=encounter,
            module=module or self.module)

    def _deny(self, action: str, resource_type: str | None, resource_id: str | None, why: str, *,
              patient: str | None = None, encounter: str | None = None):
        self._audit(action, resource_type or "unknown", resource_id, outcome="denied", detail=why, patient=patient,
                    encounter=encounter)
        raise FhirAccessDenied(why)

    # ----- access checks (app/ehr/access.py) -----

    def _patient_resource(self, patient_id: str) -> dict | None:
        if patient_id not in self._patients:
            self._patients[patient_id] = self.backend.read("Patient", patient_id)
        return self._patients[patient_id]

    def _encounter_patient(self, encounter_id: str) -> str | None:
        if encounter_id not in self._enc_patient:
            enc = self.backend.read("Encounter", encounter_id)
            self._enc_patient[encounter_id] = access.patient_of(enc)
        return self._enc_patient[encounter_id]

    def _require_type(self, action: str, resource_type: str, resource_id: str | None = None) -> None:
        if self.policy is not None and resource_type not in self.policy.read:
            self._deny(action, resource_type, resource_id, f"the {self.role} role has no access to {resource_type}")

    def _require_unit(self, action: str, resource_type: str, unit: str) -> None:
        if self.policy is None or self.policy.scope == "hospital":
            return
        if self.policy.scope == "none" or unit not in self.actor.unit_ids:
            self._deny(action, resource_type, unit, f"unit {unit} is not one of the user's units")

    def is_in_scope(self, patient_id: str) -> bool:
        """Whether the patient is the actor's without break-glass (their units, or a hospital-wide role)."""
        if self.policy is None or self.policy.scope == "hospital":
            return True
        if self.policy.scope == "none":
            return False
        encounters = self.backend.search("Encounter", patient=patient_id)
        return access.in_scope(encounters, self.actor.unit_ids, self.actor.practitioner_id, hospital_now(get_store()))

    def _require_patient(self, action: str, resource_type: str, resource_id: str | None, patient_id: str | None,
                         encounter: str | None = None) -> None:
        """Unit scope: the patient must be one of the user's, or opened by a break-glass grant."""
        if self.policy is None or patient_id is None or patient_id in self._scope:
            return
        if self.policy.scope == "none":
            self._deny(action, resource_type, resource_id, f"the {self.role} role has no access to patient records",
                       patient=patient_id, encounter=encounter)
        if self.is_in_scope(patient_id):
            self._scope[patient_id] = None
            return
        # a break-glass grant is the person's own emergency access; an agent acting for them does not inherit it
        grant = breakglass.active_grant(get_store(), self.actor.id, patient_id) \
            if self.policy.break_glass and self.actor.kind == "user" else None
        if grant is not None:
            self._scope[patient_id] = grant.id
            return
        self._audit(action, resource_type, resource_id, outcome="denied", patient=patient_id, encounter=encounter,
                    detail="patient outside the user's units" + ("; break-glass required"
                                                                if self.policy.break_glass else ""))
        if self.policy.break_glass:
            raise BreakGlassRequired(patient_id)
        raise FhirAccessDenied("This patient is outside your units")

    def _visible(self, resources: list[dict]) -> list[dict]:
        if self.policy is None:
            return resources
        return [access.reduce(self.policy, r) for r in resources if access.can_see(self.role, r)]

    def resolve_mrn(self, mrn: str) -> str | None:
        """The patient id for an MRN, nothing else (break-glass requests name the patient by MRN)."""
        return self._patient_id(mrn)

    # ----- reads -----

    def _patient_id(self, mrn: str) -> str | None:
        found = self.backend.search("Patient", mrn=mrn, limit=1)
        if not found:
            return None
        self._patients.setdefault(found[0]["id"], found[0])
        return found[0]["id"]

    def read(self, resource_type: str, resource_id: str) -> FhirModel | None:
        self._require_type("read", resource_type, resource_id)
        resource = self.backend.read(resource_type, resource_id)
        patient, encounter = access.patient_of(resource), access.encounter_of(resource)
        if resource is not None and patient:
            self._require_patient("read", resource_type, resource_id, patient, encounter)
        if resource is not None and self.policy is not None and not access.can_see(self.role, resource):
            self._deny("read", resource_type, resource_id, f"the {self.role} role has no access to this {resource_type}",
                       patient=patient, encounter=encounter)
        self._audit("read", resource_type, resource_id, patient=patient, encounter=encounter)
        return validate(access.reduce(self.policy, resource)) if resource else None

    def get_patient(self, mrn: str) -> Patient | None:
        self._require_type("read", "Patient")
        pid = self._patient_id(mrn)
        if pid:
            self._require_patient("read", "Patient", pid, pid)
        self._audit("read", "Patient", pid, patient=pid)
        return Patient.model_validate(access.reduce(self.policy, self._patients[pid])) if pid else None

    def search_encounters(self, *, mrn: str | None = None, patient_id: str | None = None,
                          cls: str | list[str] | None = None, status: str | list[str] | None = None,
                          unit: str | None = None, active_at: datetime | None = None, since: datetime | None = None,
                          until: datetime | None = None, limit: int | None = None) -> list[Encounter]:
        """Encounters, newest first. `unit` is the unit of the current location; `since` / `until` bound the start."""
        self._require_type("read", "Encounter")
        if mrn is not None:
            patient_id = self._patient_id(mrn)
            if patient_id is None:
                self._audit("read", "Encounter", None)
                return []
        if patient_id is not None:
            self._require_patient("read", "Encounter", patient_id, patient_id)
        elif unit is not None:
            self._require_unit("read", "Encounter", unit)
        elif self.policy is not None and self.policy.scope != "hospital":
            self._deny("read", "Encounter", None, "name a patient or one of your units")
        found = self.backend.search("Encounter", patient=patient_id, cls=cls, status=status, unit=unit,
                                    active_at=active_at, date_from=since, date_to=until, order="-date", limit=limit)
        self._audit("read", "Encounter", patient_id or unit, patient=patient_id)
        return [Encounter.model_validate(r) for r in self._visible(found)]

    def get_active_encounter(self, mrn: str) -> Encounter | None:
        """The patient's current visit or stay (the most recent if there are two, e.g. ED and admission)."""
        found = self.search_encounters(mrn=mrn, status=list(ACTIVE_ENCOUNTER), limit=1)
        return found[0] if found else None

    def get_bed_board(self, unit_id: str) -> BedBoard:
        self._require_type("read", "Location", unit_id)
        self._require_type("read", "Encounter", unit_id)
        self._require_unit("read", "Location", unit_id)
        locations = self.backend.search("Location", unit=unit_id)
        unit = next((loc for loc in locations if loc["id"] == unit_id), None)
        if unit is None:
            raise LookupError(f"No unit {unit_id}")
        occupant: dict[str, dict] = {}
        waiting = []
        for enc in self.backend.search("Encounter", unit=unit_id, status=list(ACTIVE_ENCOUNTER), order="date"):
            current = _current_location(enc)
            if current is None:
                continue
            where = ref_id(current["location"])
            if where == unit_id:
                waiting.append(enc["id"])
            else:
                occupant[where] = {"encounter": enc, "since": parse((current.get("period") or {}).get("start"))}
        beds = []
        for loc in sorted((loc for loc in locations if _physical_type(loc) == "bd"), key=lambda loc: loc["id"]):
            here = occupant.get(loc["id"])
            beds.append(BedView(
                id=loc["id"], name=loc.get("name", loc["id"]), room=ref_id(loc.get("partOf")) or unit_id,
                status=(loc.get("operationalStatus") or {}).get("code", "U"),
                encounter_id=here["encounter"]["id"] if here else None,
                patient_id=ref_id(here["encounter"]["subject"]) if here else None,
                since=here["since"] if here else None))
        self._audit("read", "Location", unit_id)
        return BedBoard(unit_id=unit_id, unit_name=unit.get("name", unit_id), beds=beds, waiting=waiting)

    def get_census(self, unit_id: str) -> list[CensusEntry]:
        """The patients on a unit (in a bed or waiting at it) with MRN, and name for the roles that see names."""
        self._require_type("read", "Patient", unit_id)
        board = self.get_bed_board(unit_id)
        rows: list[tuple[str, str | None, datetime | None]] = [
            (b.encounter_id, b.id, b.since) for b in board.beds if b.encounter_id]
        rows += [(enc_id, None, None) for enc_id in board.waiting]
        encounters = {e["id"]: e for e in self.backend.search("Encounter", ids=[r[0] for r in rows])} if rows else {}
        pids = sorted({access.patient_of(e) for e in encounters.values()} - {None})
        patients = {p["id"]: p for p in self.backend.search("Patient", ids=pids)} if pids else {}
        names = self.policy is None or not self.policy.mrn_only
        out = []
        for enc_id, bed, since in rows:
            enc = encounters.get(enc_id)
            pid = access.patient_of(enc)
            if enc is None or pid is None:
                continue
            p = patients.get(pid) or {}
            name = next((n.get("text") or " ".join([*(n.get("given") or []), n.get("family") or ""]).strip()
                         for n in p.get("name") or [] if n.get("use") in (None, "official")), None)
            out.append(CensusEntry(
                encounter_id=enc_id, encounter_class=(enc.get("class") or {}).get("code"), patient_id=pid,
                mrn=next((i.get("value") for i in p.get("identifier") or [] if i.get("system") == MRN_SYSTEM), None),
                name=name if names else None, bed_id=bed,
                since=since or parse((enc.get("period") or {}).get("start"))))
        self._audit("read", "Patient", f"census:{unit_id}")
        return out

    def _encounter_items(self, resource_type: str, encounter_id: str, **search) -> list[dict]:
        self._require_type("read", resource_type, encounter_id)
        patient = self._encounter_patient(encounter_id)
        self._require_patient("read", resource_type, encounter_id, patient, encounter_id)
        found = self.backend.search(resource_type, encounter=encounter_id, order="date", **search)
        self._audit("read", resource_type, encounter_id, patient=patient, encounter=encounter_id)
        return self._visible(found)

    def get_orders(self, encounter_id: str) -> list[ServiceRequest]:
        """Lab, imaging, consult, bed and surgery requests of the encounter, oldest first."""
        return [ServiceRequest.model_validate(r) for r in self._encounter_items("ServiceRequest", encounter_id)]

    def get_medications(self, encounter_id: str) -> list[MedicationRequest]:
        return [MedicationRequest.model_validate(r) for r in self._encounter_items("MedicationRequest", encounter_id)]

    def get_home_meds(self, mrn: str, *, include_stopped: bool = False) -> list[MedicationStatement]:
        """The home medication list (the provincial drug history stand-in)."""
        self._require_type("read", "MedicationStatement")
        patient_id = self._patient_id(mrn)
        if patient_id is not None:
            self._require_patient("read", "MedicationStatement", patient_id, patient_id)
        found = [] if patient_id is None else self.backend.search(
            "MedicationStatement", patient=patient_id, status=None if include_stopped else "active", order="date")
        self._audit("read", "MedicationStatement", patient_id, patient=patient_id)
        return [MedicationStatement.model_validate(r) for r in self._visible(found)]

    def get_observations(self, encounter_id: str, codes: list[str] | None = None,
                         since: datetime | None = None) -> list[Observation]:
        """Observations of the encounter, oldest first; `codes` match the code or a component code (vital panels)."""
        found = self._encounter_items("Observation", encounter_id, date_from=since)
        if codes:
            wanted = set(codes)
            found = [r for r in found if _codes(r) & wanted]
        return [Observation.model_validate(r) for r in found]

    def get_documents(self, encounter_id: str) -> list[DocumentReference]:
        return [DocumentReference.model_validate(r) for r in self._encounter_items("DocumentReference", encounter_id)]

    def get_encounter_resources(self, resource_type: str, encounter_id: str) -> list[FhirModel]:
        """Any readable resource type recorded on the encounter (conditions, procedures, flags, ...), oldest first."""
        return [validate(r) for r in self._encounter_items(resource_type, encounter_id)]

    def get_consents(self, patient_id: str) -> list[dict]:
        """The patient's Consent resources (plain dicts; app/ehr/consent.py reads them)."""
        self._require_type("read", "Consent", patient_id)
        self._require_patient("read", "Consent", patient_id, patient_id)
        found = self.backend.search("Consent", patient=patient_id)
        self._audit("read", "Consent", patient_id, patient=patient_id)
        return self._visible(found)

    # ----- writes -----

    @staticmethod
    def _create_refusal(resource: dict) -> str | None:
        rtype = resource.get("resourceType")
        if rtype == "Encounter":
            return "encounters change only through append_encounter_location"
        if rtype not in WRITABLE:
            return f"{rtype} is not on the gateway's write allow-list"
        if rtype == "DocumentReference" and resource.get("docStatus") != "preliminary":
            return "documents are created as docStatus=preliminary; final needs the signing service"
        if rtype == "Appointment" and resource.get("status") != "proposed":
            return "appointments are created as status=proposed; booking goes through book_appointment"
        return None

    @staticmethod
    def _update_refusal(current: dict, new: dict) -> str | None:
        rtype = new.get("resourceType")
        if rtype == "Encounter":
            return "encounters change only through append_encounter_location"
        if rtype not in WRITABLE:
            return f"{rtype} is not on the gateway's write allow-list"
        if rtype == "DocumentReference":
            if current.get("docStatus") != "preliminary":
                return f"a {current.get('docStatus') or 'final'} document cannot be changed through the gateway"
            if new.get("docStatus") != "preliminary":
                return "documents become final only through the signing service"
            if source_module(current) and source_module(new) != source_module(current):
                return "a document keeps the module that created it"
        if rtype == "Appointment" and (current.get("status") != "proposed" or new.get("status") != "proposed"):
            return "only a proposed appointment can be changed, and it stays proposed"
        return None

    def _registry_entry(self, action: str, resource_type: str, resource_id: str | None, status: str | None, *,
                        module: str | None = None, patient: str | None = None, encounter: str | None = None):
        """The module's registry entry, refusing the write when there is none or it does not cover it."""
        module = module or self.module
        entry = registry.agent(module)
        if entry is None:
            self._deny(action, resource_type, resource_id, f"module {module} has no entry in the agent registry",
                       patient=patient, encounter=encounter)
        if not entry.allows(resource_type, status):
            self._deny(action, resource_type, resource_id,
                       f"module {module} may not write {resource_type} with status {status}", patient=patient,
                       encounter=encounter)
        return entry

    def _check_write(self, action: str, data: dict, current: dict | None = None) -> None:
        rtype, rid = data.get("resourceType"), data.get("id")
        patient = access.patient_of(current) or access.patient_of(data)
        encounter = access.encounter_of(data) or access.encounter_of(current)
        module = source_module(current or data) if rtype == "DocumentReference" else None
        entry = self._registry_entry(action, rtype, rid, _status(data), module=module, patient=patient,
                                     encounter=encounter)
        for resource in (current, data):
            if resource is not None and (why := access.write_refusal(self.policy, self.role, resource,
                                                                     list(entry.required_signoff_role))):
                self._deny(action, rtype, rid, why, patient=patient, encounter=encounter)
        if patient:
            self._require_patient(action, rtype, rid, patient, encounter)

    @staticmethod
    def _plain(resource: dict | BaseModel) -> dict:
        return resource.to_fhir() if isinstance(resource, FhirModel) else dict(resource)

    def create(self, resource: dict | FhirModel) -> FhirModel:
        data = self._plain(resource)
        if why := self._create_refusal(data):
            self._deny("create", data.get("resourceType"), data.get("id"), why, patient=access.patient_of(data))
        if data.get("resourceType") == "DocumentReference" and not source_module(data):
            data["extension"] = [*(data.get("extension") or []), {"url": SOURCE_MODULE, "valueCode": self.module}]
        self._check_write("create", data)
        validate(data)
        stored = self.backend.create(data)
        self._audit("create", stored["resourceType"], stored["id"], patient=access.patient_of(stored),
                    encounter=access.encounter_of(stored))
        return validate(stored)

    def update(self, resource: dict | FhirModel) -> FhirModel:
        data = self._plain(resource)
        rtype, rid = data.get("resourceType"), data.get("id")
        if rtype not in WRITABLE:  # refuse before reading anything
            self._deny("update", rtype, rid, self._update_refusal({}, data), patient=access.patient_of(data))
        current = self.backend.read(rtype, rid) if rid else None
        if current is None:
            raise LookupError(f"{rtype}/{rid} not found")
        if rtype == "DocumentReference" and not source_module(data) and source_module(current):
            data["extension"] = [*(data.get("extension") or []),
                                 {"url": SOURCE_MODULE, "valueCode": source_module(current)}]
        if why := self._update_refusal(current, data):
            self._deny("update", rtype, rid, why, patient=access.patient_of(current))
        self._check_write("update", data, current)
        validate(data)
        stored = self.backend.update(data)
        self._audit("update", rtype, rid, patient=access.patient_of(stored), encounter=access.encounter_of(stored))
        return validate(stored)

    def delete(self, resource_type: str, resource_id: str) -> None:
        """The AI layer never deletes EHR records; always refused (and audited)."""
        self._deny("delete", resource_type, resource_id, "the gateway does not delete EHR records")

    def append_encounter_location(self, encounter_id: str, bed_id: str, *, at: datetime | None = None) -> Encounter:
        """Move a patient to a free bed: the current location entry is closed and the bed appended.
        As the EHR's own transfer does, the new bed becomes occupied and the old one goes to housekeeping."""
        if self.policy is not None and "Encounter.location" not in self.policy.write:
            self._deny("update", "Encounter", encounter_id, "only bed managers may move patients between beds")
        self._registry_entry("update", "Encounter.location", encounter_id, "append")
        enc = self.backend.read("Encounter", encounter_id)
        if enc is None:
            raise LookupError(f"Encounter/{encounter_id} not found")
        if enc.get("status") not in ACTIVE_ENCOUNTER:
            raise FhirConflict(f"Encounter/{encounter_id} is {enc.get('status')}, not active")
        bed = self.backend.read("Location", bed_id)
        if bed is None:
            raise LookupError(f"Location/{bed_id} not found")
        if _physical_type(bed) != "bd":
            raise FhirConflict(f"{bed_id} is not a bed")
        bed_status = (bed.get("operationalStatus") or {}).get("code", "U")
        if bed_status != "U":
            raise FhirConflict(f"Bed {bed_id} is not free (status {bed_status})")
        at = at or hospital_now(get_store())
        current = _current_location(enc)
        previous = ref_id(current["location"]) if current else None
        if current:
            current["status"] = "completed"
            current["period"] = {**(current.get("period") or {}), "end": fhir_datetime(at)}
        enc.setdefault("location", []).append(
            {"location": ref("Location", bed_id), "status": "active", "period": {"start": fhir_datetime(at)}})
        stored = self.backend.update(enc)
        bed["operationalStatus"] = coding(BED_STATUS, "O", "Occupied")
        self.backend.update(bed)
        old_bed = self.backend.read("Location", previous) if previous else None
        if old_bed is not None and _physical_type(old_bed) == "bd":
            old_bed["operationalStatus"] = coding(BED_STATUS, "K", "Contaminated")  # waiting for housekeeping
            self.backend.update(old_bed)
        self._audit("update", "Encounter", encounter_id, detail=f"moved to {bed_id}",
                    patient=access.patient_of(stored), encounter=encounter_id)
        return Encounter.model_validate(stored)

    def book_appointment(self, appointment_id: str) -> FhirModel:
        """Proposed -> booked. A clerk's act (the registration desk), or the privileged tool bookAppointment
        running on the approving clerk's authority (6.4); the module's tools must allow booked appointments."""
        current = self.backend.read("Appointment", appointment_id)
        if current is None:
            raise LookupError(f"Appointment/{appointment_id} not found")
        patient = access.patient_of(current)
        self._registry_entry("update", "Appointment", appointment_id, "booked", patient=patient)
        if self.policy is not None and (self.role not in access.BOOKERS or self.actor.kind != "user"):
            self._deny("update", "Appointment", appointment_id, "only a clerk books appointments", patient=patient)
        self._require_patient("update", "Appointment", appointment_id, patient)
        if current.get("status") != "proposed":
            raise FhirConflict(f"Appointment/{appointment_id} is {current.get('status')}, not proposed")
        booked = {**current, "status": "booked"}
        validate(booked)
        stored = self.backend.update(booked)
        self._audit("update", "Appointment", appointment_id, detail="booked", patient=patient,
                    encounter=access.encounter_of(stored))
        return validate(stored)

    def record_provenance(self, resource: dict, *, replace: bool = False) -> FhirModel:
        """Write the Provenance of an AI output (6.4): the agent runtime only, as a system actor."""
        if resource.get("resourceType") != "Provenance":
            raise ValueError("record_provenance takes a Provenance")
        self._registry_entry("create", "Provenance", resource.get("id"), None)
        if self.actor.kind != "system":
            self._deny("create", "Provenance", resource.get("id"), "only the agent runtime records provenance")
        validate(resource)
        exists = bool(resource.get("id")) and self.backend.read("Provenance", resource["id"]) is not None
        if exists and not replace:
            raise FhirConflict(f"Provenance/{resource['id']} already exists")
        stored = self.backend.update(resource) if exists else self.backend.create(resource)
        self._audit("update" if exists else "create", "Provenance", stored["id"],
                    detail=f"target {((stored.get('target') or [{}])[0]).get('reference')}")
        return validate(stored)

    # ----- signing and consent (6.3) -----

    def sign_document(self, document_id: str, *, at: datetime | None = None):
        """Sign a draft (app/ehr/signing.py); the only way a DocumentReference becomes final."""
        from app.ehr.signing import sign

        return sign(self, document_id, at=at)

    def record_consent(self, patient_id: str, category: str, permit: bool, *, at: datetime) -> tuple[dict, str]:
        """Write the patient's decision for one consent category; returns (Consent, previous state:
        permit, deny or missing). Used by consent management (app/ehr/consent.py)."""
        from app.ehr.codes import CONSENT_CATEGORY, CONSENT_SCOPE, concept

        if category not in registry.CONSENT_CATEGORIES:
            raise ValueError(f"Unknown consent category {category!r}")
        self._registry_entry("consent_change", "Consent", None, "active", patient=patient_id)
        if self.policy is not None and self.role not in access.CONSENT_RECORDERS:
            self._deny("consent_change", "Consent", None, f"the {self.role} role may not record consent",
                       patient=patient_id)
        self._require_patient("consent_change", "Consent", None, patient_id)
        existing = [c for c in self.backend.search("Consent", patient=patient_id)
                    if any(cc.get("code") == category for cat in c.get("category") or []
                           for cc in cat.get("coding") or [])]
        current = max(existing, key=lambda c: c.get("dateTime") or "") if existing else None
        previous = (current.get("provision") or {}).get("type", "missing") if current else "missing"
        decision = "permit" if permit else "deny"
        if current is not None:
            current["status"] = "active"
            current["dateTime"] = fhir_datetime(at)
            current["provision"] = {**(current.get("provision") or {}), "type": decision}
            validate(current)
            stored = self.backend.update(current)
        else:
            new = {"resourceType": "Consent", "status": "active", "scope": concept(CONSENT_SCOPE, "patient-privacy"),
                   "category": [concept(CONSENT_CATEGORY, category, category)], "patient": ref("Patient", patient_id),
                   "dateTime": fhir_datetime(at),
                   "provision": {"type": decision, "purpose": [coding(CONSENT_CATEGORY, category)]}}
            validate(new)
            stored = self.backend.create(new)
        self._audit("consent_change", "Consent", stored["id"], event_type="consent_change", patient=patient_id,
                    detail=f"{category}: {previous} -> {decision}")
        return stored, previous
