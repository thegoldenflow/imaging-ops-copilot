"""FHIR R4 resources stored in PostgreSQL: the default FhirGateway backend.

One row per resource in `fhir_resources`. The resource body is stored as
encrypted JSON (AES-GCM, like the PHI columns); search runs on plain columns
extracted from the body when it is written (`index_columns`). Those columns hold
ids, references, codes, statuses and dates only, never names or other
identifiers; Patient identifiers are searchable through blind indexes.

The store is a collection on the unit of work (`store.fhir`), so FHIR writes
commit or roll back with everything else in the request, the seed generator
fills it detached in memory, and a demo reset rewrites it. Modules never use it
directly: they go through `app.ehr.gateway.FhirGateway`.

Location ids follow the convention unit `ORTH`, room `ORTH-05`, bed `ORTH-05-A`,
so the unit of any bed or room is the part before the first dash.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Iterable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import BigInteger, Column, DateTime, Identity, Index, Table, Text, and_, delete, func, insert, or_, select, update

from app.core.db.schema import metadata
from app.ehr.codes import HCN_SYSTEM, MRN_SYSTEM
from app.fhir.dt import fhir_datetime, parse, ref_id

if TYPE_CHECKING:
    from app.core.store import Store

fhir_resources = Table(
    "fhir_resources", metadata,
    Column("resource_type", Text, primary_key=True),
    Column("id", Text, primary_key=True),
    Column("seq", BigInteger, Identity(), nullable=False),
    Column("version", BigInteger, nullable=False),
    Column("last_updated", DateTime, nullable=False),
    Column("patient", Text),
    Column("encounter", Text),
    Column("status", Text),
    Column("code", Text),
    Column("category", Text),
    Column("cls", Text),
    Column("date", DateTime),
    Column("date_end", DateTime),
    Column("location", Text),
    Column("unit", Text),
    Column("owner", Text),
    Column("focus", Text),
    Column("mrn_bidx", Text),
    Column("hcn_bidx", Text),
    Column("data", Text, nullable=False),  # encrypted JSON
)
Index("ix_fhir_patient", fhir_resources.c.resource_type, fhir_resources.c.patient)
Index("ix_fhir_encounter", fhir_resources.c.resource_type, fhir_resources.c.encounter)
Index("ix_fhir_status", fhir_resources.c.resource_type, fhir_resources.c.status)
Index("ix_fhir_date", fhir_resources.c.resource_type, fhir_resources.c.date)
Index("ix_fhir_unit", fhir_resources.c.resource_type, fhir_resources.c.unit)
Index("ix_fhir_mrn", fhir_resources.c.mrn_bidx)
Index("ix_fhir_seq", fhir_resources.c.seq)

INDEX_COLUMNS = ("patient", "encounter", "status", "code", "category", "cls", "date", "date_end", "location",
                 "unit", "owner", "focus", "mrn_bidx", "hcn_bidx")
_CONTEXT = "fhir_resources.data"


def unit_of(location_id: str | None) -> str | None:
    return location_id.split("-", 1)[0] if location_id else None


def _code(concept: dict | list | str | None) -> str | None:
    if isinstance(concept, list):
        concept = concept[0] if concept else None
    if isinstance(concept, str):  # a bare code (e.g. AllergyIntolerance.category)
        return concept
    codings = (concept or {}).get("coding") or []
    return codings[0].get("code") if codings else None


def _first(items: list | None) -> Any:
    return items[0] if items else None


def _identifier(resource: dict, system: str) -> str | None:
    for ident in resource.get("identifier") or []:
        if ident.get("system") == system:
            return ident.get("value")
    return None


def _subject(resource: dict) -> str | None:
    for key in ("subject", "patient", "for"):
        value = resource.get(key)
        if isinstance(value, dict) and (value.get("reference") or "").startswith("Patient/"):
            return ref_id(value)
    return None


def _period(period: dict | None) -> tuple[datetime | None, datetime | None]:
    period = period or {}
    return parse(period.get("start")), parse(period.get("end"))


def index_columns(resource: dict) -> dict[str, Any]:
    """The search columns for a resource (no identifying values)."""
    rtype = resource["resourceType"]
    cols: dict[str, Any] = dict.fromkeys(INDEX_COLUMNS)
    cols["patient"] = _subject(resource)
    enc = resource.get("encounter") or (resource.get("context") or {}).get("encounter")
    if isinstance(enc, list):
        enc = _first(enc)
    cols["encounter"] = ref_id(enc)
    status = resource.get("status")
    cols["status"] = status if isinstance(status, str) else _code(status)
    cols["code"] = _code(resource.get("code"))
    category = resource.get("category")
    cols["category"] = _code(_first(category) if isinstance(category, list) else category)

    if rtype == "Patient":
        cols["patient"] = resource.get("id")
        cols["status"] = "active" if resource.get("active", True) else "inactive"
        from app.core.db.crypto import cipher

        mrn, hcn = _identifier(resource, MRN_SYSTEM), _identifier(resource, HCN_SYSTEM)
        cols["mrn_bidx"] = cipher().blind_index(mrn) if mrn else None
        cols["hcn_bidx"] = cipher().blind_index(hcn) if hcn else None
    elif rtype == "Encounter":
        cols["cls"] = (resource.get("class") or {}).get("code")
        cols["code"] = _code(resource.get("serviceType"))
        cols["date"], cols["date_end"] = _period(resource.get("period"))
        locations = resource.get("location") or []
        current = next((loc for loc in reversed(locations) if loc.get("status") in ("active", "planned", None)), None)
        if current:
            cols["location"] = ref_id(current.get("location"))
            cols["unit"] = unit_of(cols["location"])
        cols["focus"] = ref_id(resource.get("partOf"))
    elif rtype == "Location":
        cols["status"] = (resource.get("operationalStatus") or {}).get("code") or resource.get("status")
        cols["code"] = _code(resource.get("physicalType"))
        cols["location"] = ref_id(resource.get("partOf"))
        cols["unit"] = unit_of(resource.get("id"))
    elif rtype in ("MedicationRequest", "MedicationStatement"):
        cols["code"] = _code(resource.get("medicationCodeableConcept"))
        cols["category"] = resource.get("intent") or cols["category"]
        if rtype == "MedicationRequest":
            cols["date"] = parse(resource.get("authoredOn"))
        else:
            cols["date"], cols["date_end"] = _period(resource.get("effectivePeriod"))
            cols["encounter"] = ref_id(resource.get("context"))
    elif rtype == "Observation":
        cols["date"] = parse(resource.get("effectiveDateTime"))
    elif rtype == "Condition":
        cols["status"] = _code(resource.get("clinicalStatus"))
        cols["date"] = parse(resource.get("onsetDateTime") or resource.get("recordedDate"))
        cols["date_end"] = parse(resource.get("abatementDateTime"))
    elif rtype == "AllergyIntolerance":
        cols["status"] = _code(resource.get("clinicalStatus"))
        cols["date"] = parse(resource.get("recordedDate"))
    elif rtype == "Procedure":
        cols["date"], cols["date_end"] = _period(resource.get("performedPeriod"))
        cols["location"] = ref_id(resource.get("location"))
    elif rtype == "DiagnosticReport":
        cols["date"] = parse(resource.get("effectiveDateTime") or resource.get("issued"))
    elif rtype == "ServiceRequest":
        cols["date"] = parse(resource.get("authoredOn"))
        cols["date_end"] = parse(resource.get("occurrenceDateTime"))
        cols["cls"] = resource.get("priority")
        cols["location"] = ref_id(_first(resource.get("locationReference")))
        cols["unit"] = unit_of(cols["location"])
    elif rtype in ("Appointment", "Slot"):
        cols["code"] = _code(_first(resource.get("serviceType")))
        cols["date"], cols["date_end"] = parse(resource.get("start")), parse(resource.get("end"))
        for participant in resource.get("participant") or []:
            actor = (participant.get("actor") or {}).get("reference") or ""
            if actor.startswith("Patient/"):
                cols["patient"] = ref_id(actor)
            elif actor.startswith("Location/"):
                cols["location"] = ref_id(actor)
            elif actor.startswith("Practitioner/"):
                cols["owner"] = actor
        cols["encounter"] = ref_id(_ext_value(resource, "encounter"))
        cols["focus"] = ref_id(_first(resource.get("basedOn")))
        cols["unit"] = unit_of(cols["location"])
    elif rtype == "Schedule":
        cols["date"], cols["date_end"] = _period(resource.get("planningHorizon"))
        cols["owner"] = (_first(resource.get("actor")) or {}).get("reference")
    elif rtype == "Task":
        cols["date"] = parse(resource.get("authoredOn"))
        cols["date_end"] = parse((resource.get("restriction") or {}).get("period", {}).get("end"))
        cols["owner"] = (resource.get("owner") or {}).get("reference")
        cols["focus"] = (resource.get("focus") or {}).get("reference")
        cols["category"] = _code(_first(resource.get("performerType")))
        cols["cls"] = resource.get("priority")
    elif rtype == "Flag":
        cols["date"], cols["date_end"] = _period(resource.get("period"))
    elif rtype == "Communication":
        cols["date"] = parse(resource.get("sent"))
        cols["focus"] = ((_first(resource.get("about")) or {}).get("reference"))
    elif rtype == "DocumentReference":
        cols["status"] = resource.get("docStatus") or resource.get("status")
        cols["code"] = _code(resource.get("type"))
        cols["date"] = parse(resource.get("date"))
    elif rtype == "CarePlan":
        cols["date"], cols["date_end"] = _period(resource.get("period"))
    elif rtype == "Consent":
        cols["date"] = parse(resource.get("dateTime"))
        cols["code"] = _code(_first(resource.get("category")))
        cols["category"] = cols["code"]
    elif rtype == "EpisodeOfCare":
        cols["date"], cols["date_end"] = _period(resource.get("period"))
    elif rtype == "Provenance":
        cols["date"] = parse(resource.get("recorded"))
        cols["focus"] = (_first(resource.get("target")) or {}).get("reference")
    elif rtype in ("Practitioner", "PractitionerRole", "Organization"):
        cols["status"] = "active" if resource.get("active", True) else "inactive"
        if rtype == "PractitionerRole":
            cols["code"] = _code(_first(resource.get("code")))
            cols["category"] = _code(_first(resource.get("specialty")))
            cols["owner"] = (resource.get("practitioner") or {}).get("reference")
            cols["unit"] = ref_id(_first(resource.get("location")))
    return cols


def _ext_value(resource: dict, name: str) -> Any:
    from app.ehr.codes import EXT

    for ext in resource.get("extension") or []:
        if ext.get("url") == EXT + name:
            return next((v for k, v in ext.items() if k.startswith("value")), None)
    return None


def _as_list(value: str | Iterable[str] | None) -> list[str] | None:
    if value is None:
        return None
    return [value] if isinstance(value, str) else list(value)


class FhirStore:
    """Resource CRUD and search, inside the store's unit of work."""

    def __init__(self, store: Store) -> None:
        self._store = store
        self._mem: dict[tuple[str, str], tuple[dict, dict]] = {}  # detached: key -> (resource, columns)
        self._seq = 0
        self._cache: dict[tuple[str, str], dict] = {}

    # ----- unit of work -----

    def expunge(self) -> None:
        self._cache.clear()

    def flush(self) -> int:
        return 0  # writes go to the database immediately

    # ----- encoding -----

    @staticmethod
    def _encrypt(resource: dict) -> str:
        from app.core.db.crypto import cipher

        return cipher().encrypt(json.dumps(resource, separators=(",", ":"), ensure_ascii=False), _CONTEXT)

    @staticmethod
    def _decrypt(token: str) -> dict:
        from app.core.db.crypto import cipher

        return json.loads(cipher().decrypt(token, _CONTEXT))

    def _row(self, resource: dict, cols: dict | None = None) -> dict:
        meta = resource.get("meta") or {}
        return {"resource_type": resource["resourceType"], "id": resource["id"],
                "version": int(meta.get("versionId", 1)), "last_updated": parse(meta.get("lastUpdated")) or datetime.now(),
                **(cols if cols is not None else index_columns(resource)), "data": self._encrypt(resource)}

    def encoded_rows(self) -> list[dict]:
        return [self._row(resource, cols) for resource, cols in self._mem.values()]

    # ----- writes -----

    def _stamp(self, resource: dict, version: int) -> dict:
        # The caller's dict is not reused: deep-copy it, except while generating (detached), where
        # the generator hands over freshly built dicts and copying 65k resources would cost seconds.
        resource = dict(resource) if self._store.detached else copy.deepcopy(resource)
        resource["meta"] = {**(resource.get("meta") or {}), "versionId": str(version),
                            "lastUpdated": self._stamp_time()}
        return resource

    def _stamp_time(self) -> str:
        if self._store.detached:  # one timestamp for the whole generated data set
            if not hasattr(self, "_detached_stamp"):
                self._detached_stamp = fhir_datetime(datetime.now())
            return self._detached_stamp
        return fhir_datetime(datetime.now())

    def create(self, resource: dict) -> dict:
        """Store a new resource. It keeps a given id; otherwise one is assigned."""
        rtype = resource["resourceType"]
        if not resource.get("id"):
            resource = {**resource, "id": self._store.next_id(rtype.lower()[:12]).lower()}
        stored = self._stamp(resource, 1)
        key = (rtype, stored["id"])
        if self._store.detached:
            if key in self._mem:
                raise ValueError(f"{rtype}/{stored['id']} already exists")
            self._mem[key] = (stored, index_columns(stored))
            return stored  # the generator does not modify what it gets back
        self._store.conn().execute(insert(fhir_resources).values(**self._row(stored)))
        self._store.touch()
        self._cache[key] = stored
        return copy.deepcopy(stored)

    def update(self, resource: dict) -> dict:
        """Replace a stored resource (version + 1)."""
        rtype, rid = resource["resourceType"], resource["id"]
        current = self.read(rtype, rid)
        if current is None:
            raise KeyError(f"{rtype}/{rid} not found")
        stored = self._stamp(resource, int(current["meta"]["versionId"]) + 1)
        if self._store.detached:
            self._mem[(rtype, rid)] = (stored, index_columns(stored))
        else:
            row = self._row(stored)
            t = fhir_resources
            self._store.conn().execute(update(t).where(t.c.resource_type == rtype, t.c.id == rid).values(
                **{k: v for k, v in row.items() if k not in ("resource_type", "id")}))
            self._store.touch()
        self._cache[(rtype, rid)] = stored
        return copy.deepcopy(stored)

    def delete(self, resource_type: str, resource_id: str) -> None:
        if self._store.detached:
            self._mem.pop((resource_type, resource_id), None)
        else:
            t = fhir_resources
            self._store.conn().execute(delete(t).where(t.c.resource_type == resource_type, t.c.id == resource_id))
            self._store.touch()
        self._cache.pop((resource_type, resource_id), None)

    # ----- reads -----

    def read(self, resource_type: str, resource_id: str) -> dict | None:
        key = (resource_type, resource_id)
        if key in self._cache:
            return copy.deepcopy(self._cache[key])
        if self._store.detached:
            found = self._mem.get(key)
            resource = found[0] if found else None
        else:
            t = fhir_resources
            token = self._store.conn().execute(
                select(t.c.data).where(t.c.resource_type == resource_type, t.c.id == resource_id)).scalar()
            resource = self._decrypt(token) if token else None
        if resource is None:
            return None
        self._cache[key] = resource
        return copy.deepcopy(resource)

    def search(self, resource_type: str, *, ids: Iterable[str] | None = None, order: str | None = None,
               limit: int | None = None, **params: Any) -> list[dict]:
        """Resources of one type matching every given parameter.

        Parameters (all optional): patient, encounter, status, code, category, cls,
        location, unit, owner, focus (a value or a list of values); mrn, hcn (exact
        identifier value); date_from / date_to (on `date`: from <= date < to);
        active_at (date <= t and (date_end is null or date_end > t)).
        order: "date", "-date" or None (insertion order)."""
        if self._store.detached:
            return [copy.deepcopy(r) for r in self._search_mem(resource_type, ids, order, limit, params)]
        t = fhir_resources
        stmt = select(t.c.data, t.c.id).where(t.c.resource_type == resource_type, *self._where(ids, params))
        stmt = stmt.order_by(*self._order(order))
        if limit:
            stmt = stmt.limit(limit)
        out = []
        for token, rid in self._store.conn().execute(stmt):
            key = (resource_type, rid)
            resource = self._cache.get(key)
            if resource is None:
                resource = self._cache[key] = self._decrypt(token)
            out.append(copy.deepcopy(resource))
        return out

    def count(self, resource_type: str, **params: Any) -> int:
        if self._store.detached:
            return len(self._search_mem(resource_type, params.pop("ids", None), None, None, params))
        t = fhir_resources
        stmt = select(func.count()).select_from(t).where(
            t.c.resource_type == resource_type, *self._where(params.pop("ids", None), params))
        return int(self._store.conn().execute(stmt).scalar_one())

    def ids(self, resource_type: str, **params: Any) -> list[str]:
        if self._store.detached:
            return [r["id"] for r in self._search_mem(resource_type, params.pop("ids", None), None, None, params)]
        t = fhir_resources
        stmt = select(t.c.id).where(t.c.resource_type == resource_type, *self._where(params.pop("ids", None), params))
        return list(self._store.conn().execute(stmt.order_by(t.c.seq)).scalars())

    # ----- query building -----

    @staticmethod
    def _blind(params: dict) -> dict:
        params = dict(params)
        from app.core.db.crypto import cipher

        for key, col in (("mrn", "mrn_bidx"), ("hcn", "hcn_bidx")):
            if params.get(key) is not None:
                params[col] = cipher().blind_index(params.pop(key))
            else:
                params.pop(key, None)
        return params

    def _where(self, ids, params: dict) -> list:
        t = fhir_resources
        params = self._blind(params)
        clauses = []
        if ids is not None:
            clauses.append(t.c.id.in_(list(ids)))
        for key, value in params.items():
            if value is None:
                continue
            if key == "date_from":
                clauses.append(t.c.date >= value)
            elif key == "date_to":
                clauses.append(t.c.date < value)
            elif key == "active_at":
                clauses.append(and_(t.c.date <= value, or_(t.c.date_end.is_(None), t.c.date_end > value)))
            elif key in INDEX_COLUMNS:
                values = _as_list(value)
                clauses.append(t.c[key].in_(values) if len(values) > 1 else t.c[key] == values[0])
            else:
                raise ValueError(f"Unsupported FHIR search parameter {key!r}")
        return clauses

    @staticmethod
    def _order(order: str | None) -> list:
        t = fhir_resources
        if order == "date":
            return [t.c.date.asc().nulls_last(), t.c.seq]
        if order == "-date":
            return [t.c.date.desc().nulls_last(), t.c.seq.desc()]
        return [t.c.seq]

    def _search_mem(self, resource_type, ids, order, limit, params) -> list[dict]:
        params = self._blind(params)
        wanted = set(ids) if ids is not None else None
        rows = []
        for (rtype, rid), (resource, cols) in self._mem.items():
            if rtype != resource_type or (wanted is not None and rid not in wanted):
                continue
            if all(self._match(cols, k, v) for k, v in params.items() if v is not None):
                rows.append((resource, cols))
        if order in ("date", "-date"):
            rows.sort(key=lambda rc: (rc[1]["date"] is None, rc[1]["date"] or datetime.min), reverse=order == "-date")
        out = [r for r, _ in rows]
        return out[:limit] if limit else out

    @staticmethod
    def _match(cols: dict, key: str, value: Any) -> bool:
        if key == "date_from":
            return cols["date"] is not None and cols["date"] >= value
        if key == "date_to":
            return cols["date"] is not None and cols["date"] < value
        if key == "active_at":
            return cols["date"] is not None and cols["date"] <= value and (cols["date_end"] is None or cols["date_end"] > value)
        if key not in INDEX_COLUMNS:
            raise ValueError(f"Unsupported FHIR search parameter {key!r}")
        return cols[key] in _as_list(value)
