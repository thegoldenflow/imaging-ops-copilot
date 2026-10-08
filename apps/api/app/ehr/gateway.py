"""FhirGateway (spec 6.2): the only way modules read or write the hospital EHR.

Modules never use the FHIR store or HTTP directly (tests/test_fhir_gateway.py
greps for it). A gateway is opened for one actor (a signed-in user, later an
agent or a system job) and one purpose module:

    fhir = FhirGateway(Actor.of(user, request), module="control_tower")
    board = fhir.get_bed_board("MEDA")

Reads are typed (`app.fhir.types` models). Writes follow the allow-list of the
AI layer, which sits beside the EHR and only writes work items and drafts:

    Task                 create and update, any status
    DocumentReference    create as docStatus=preliminary; change only while preliminary
                         (final goes through the signing service, WP4)
    Communication, Flag  create and update
    Appointment          create as status=proposed; change only while proposed
                         (booking needs a clerk's confirmation, WP4b)
    Encounter.location   append a bed move, bed managers only (append_encounter_location)

Anything else is refused with FhirAccessDenied and an audit record. Every call
is audited: actor, resource type, resource id and the purpose module (in the
audit record's reason until WP4 adds a module column).

Backends: `local` (the FHIR store in PostgreSQL, inside the request's unit of
work) and `hapi` (a FHIR server, app/ehr/hapi.py), chosen with FHIR_BACKEND.
Resources passed on to a model go through app/llm/fhir_deid.py first.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.core.models import Role, StaffUser
from app.core.store import Store, get_store
from app.ehr.clock import hospital_now
from app.ehr.codes import BED_STATUS, coding
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

ACTIVE_ENCOUNTER = ("arrived", "triaged", "in-progress", "onleave")
BED_MANAGER_ROLES = {Role.OPERATIONS_MANAGER}
WRITABLE = ("Task", "DocumentReference", "Communication", "Flag", "Appointment")


class FhirAccessDenied(PermissionError):
    """A write outside the gateway's allow-list (already audited)."""


class FhirConflict(ValueError):
    """A write the allow-list permits but the record's current state does not (e.g. the bed is taken)."""


@dataclass(frozen=True)
class Actor:
    id: str
    name: str
    role: str
    kind: str = "user"  # user, agent or system
    source_ip: str | None = None

    @classmethod
    def of(cls, user: StaffUser, request=None) -> Actor:
        from app.core.auth import client_ip

        return cls(user.id, user.name, str(user.role), "user", client_ip(request) if request is not None else None)

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


# ---------- the gateway ----------


class FhirGateway:
    def __init__(self, actor: Actor, module: str, backend=None) -> None:
        self.actor = actor
        self.module = module
        self.backend = backend or backend_from_settings()

    # ----- audit -----

    def _audit(self, action: str, resource_type: str, resource_id: str | None, *, outcome: str = "allowed",
               detail: str | None = None) -> None:
        get_store().audit.record(
            user_id=self.actor.id, user_name=self.actor.name, role=self.actor.role, action=action,
            resource_type=resource_type, resource_id=resource_id, outcome=outcome, source_ip=self.actor.source_ip,
            reason=self.module if detail is None else f"{self.module}: {detail}")

    def _deny(self, action: str, resource_type: str | None, resource_id: str | None, why: str):
        self._audit(action, resource_type or "unknown", resource_id, outcome="denied", detail=why)
        raise FhirAccessDenied(why)

    # ----- reads -----

    def _patient_id(self, mrn: str) -> str | None:
        found = self.backend.search("Patient", mrn=mrn, limit=1)
        return found[0]["id"] if found else None

    def read(self, resource_type: str, resource_id: str) -> FhirModel | None:
        resource = self.backend.read(resource_type, resource_id)
        self._audit("read", resource_type, resource_id)
        return validate(resource) if resource else None

    def get_patient(self, mrn: str) -> Patient | None:
        found = self.backend.search("Patient", mrn=mrn, limit=1)
        self._audit("read", "Patient", found[0]["id"] if found else None)
        return Patient.model_validate(found[0]) if found else None

    def search_encounters(self, *, mrn: str | None = None, patient_id: str | None = None,
                          cls: str | list[str] | None = None, status: str | list[str] | None = None,
                          unit: str | None = None, active_at: datetime | None = None, since: datetime | None = None,
                          until: datetime | None = None, limit: int | None = None) -> list[Encounter]:
        """Encounters, newest first. `unit` is the unit of the current location; `since` / `until` bound the start."""
        if mrn is not None:
            patient_id = self._patient_id(mrn)
            if patient_id is None:
                self._audit("read", "Encounter", None)
                return []
        found = self.backend.search("Encounter", patient=patient_id, cls=cls, status=status, unit=unit,
                                    active_at=active_at, date_from=since, date_to=until, order="-date", limit=limit)
        self._audit("read", "Encounter", patient_id or unit)
        return [Encounter.model_validate(r) for r in found]

    def get_active_encounter(self, mrn: str) -> Encounter | None:
        """The patient's current visit or stay (the most recent if there are two, e.g. ED and admission)."""
        found = self.search_encounters(mrn=mrn, status=list(ACTIVE_ENCOUNTER), limit=1)
        return found[0] if found else None

    def get_bed_board(self, unit_id: str) -> BedBoard:
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

    def get_orders(self, encounter_id: str) -> list[ServiceRequest]:
        """Lab, imaging, consult, bed and surgery requests of the encounter, oldest first."""
        found = self.backend.search("ServiceRequest", encounter=encounter_id, order="date")
        self._audit("read", "ServiceRequest", encounter_id)
        return [ServiceRequest.model_validate(r) for r in found]

    def get_medications(self, encounter_id: str) -> list[MedicationRequest]:
        found = self.backend.search("MedicationRequest", encounter=encounter_id, order="date")
        self._audit("read", "MedicationRequest", encounter_id)
        return [MedicationRequest.model_validate(r) for r in found]

    def get_home_meds(self, mrn: str, *, include_stopped: bool = False) -> list[MedicationStatement]:
        """The home medication list (the provincial drug history stand-in)."""
        patient_id = self._patient_id(mrn)
        found = [] if patient_id is None else self.backend.search(
            "MedicationStatement", patient=patient_id, status=None if include_stopped else "active", order="date")
        self._audit("read", "MedicationStatement", patient_id)
        return [MedicationStatement.model_validate(r) for r in found]

    def get_observations(self, encounter_id: str, codes: list[str] | None = None,
                         since: datetime | None = None) -> list[Observation]:
        """Observations of the encounter, oldest first; `codes` match the code or a component code (vital panels)."""
        found = self.backend.search("Observation", encounter=encounter_id, date_from=since, order="date")
        if codes:
            wanted = set(codes)
            found = [r for r in found if _codes(r) & wanted]
        self._audit("read", "Observation", encounter_id)
        return [Observation.model_validate(r) for r in found]

    def get_documents(self, encounter_id: str) -> list[DocumentReference]:
        found = self.backend.search("DocumentReference", encounter=encounter_id, order="date")
        self._audit("read", "DocumentReference", encounter_id)
        return [DocumentReference.model_validate(r) for r in found]

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
            return "appointments are created as status=proposed; booking needs a clerk's confirmation"
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
        if rtype == "Appointment" and (current.get("status") != "proposed" or new.get("status") != "proposed"):
            return "only a proposed appointment can be changed, and it stays proposed"
        return None

    @staticmethod
    def _plain(resource: dict | BaseModel) -> dict:
        return resource.to_fhir() if isinstance(resource, FhirModel) else dict(resource)

    def create(self, resource: dict | FhirModel) -> FhirModel:
        data = self._plain(resource)
        if why := self._create_refusal(data):
            self._deny("create", data.get("resourceType"), data.get("id"), why)
        validate(data)
        stored = self.backend.create(data)
        self._audit("create", stored["resourceType"], stored["id"])
        return validate(stored)

    def update(self, resource: dict | FhirModel) -> FhirModel:
        data = self._plain(resource)
        rtype, rid = data.get("resourceType"), data.get("id")
        if rtype not in WRITABLE:  # refuse before reading anything
            self._deny("update", rtype, rid, self._update_refusal({}, data))
        current = self.backend.read(rtype, rid) if rid else None
        if current is None:
            raise LookupError(f"{rtype}/{rid} not found")
        if why := self._update_refusal(current, data):
            self._deny("update", rtype, rid, why)
        validate(data)
        stored = self.backend.update(data)
        self._audit("update", rtype, rid)
        return validate(stored)

    def delete(self, resource_type: str, resource_id: str) -> None:
        """The AI layer never deletes EHR records; always refused (and audited)."""
        self._deny("delete", resource_type, resource_id, "the gateway does not delete EHR records")

    def append_encounter_location(self, encounter_id: str, bed_id: str, *, at: datetime | None = None) -> Encounter:
        """Move a patient to a free bed: the current location entry is closed and the bed appended.
        As the EHR's own transfer does, the new bed becomes occupied and the old one goes to housekeeping."""
        if self.actor.role not in BED_MANAGER_ROLES:
            self._deny("update", "Encounter", encounter_id, "only bed managers may move patients between beds")
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
        self._audit("update", "Encounter", encounter_id, detail=f"moved to {bed_id}")
        return Encounter.model_validate(stored)
