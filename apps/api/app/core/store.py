"""The data store: one unit of work against PostgreSQL.

Each request (and each background-worker step) gets its own Store with its own
transaction; it commits when the request succeeds and rolls back on a server
error. Modules read and write through the collections on this object
(`store.appointments`, `store.modules["reports"]`, ...) exactly as they did with
the in-memory store; app/core/db/repo.py explains how changes are tracked.

A Store created without a connection source is detached: it lives in memory
only. The seed generator builds the demo data set that way, and a reset then
writes it into the database in one go.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Callable, Iterator
from contextvars import ContextVar
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, Engine, create_engine, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.audit import AuditLog
from app.core.db import schema
from app.core.db.repo import EntityTable, ImageTable, ListTable, ModuleSpace, clear_process_cache
from app.core.models import (
    Allergy,
    Appointment,
    Exam,
    ImagingStudy,
    LabResult,
    LlmCall,
    MessageOutbox,
    Patient,
    Referrer,
    Requisition,
    Scanner,
    Site,
    StaffUser,
    WaitlistEntry,
)
from app.ehr.fhirstore import FhirStore, fhir_resources

# ---------- Connections ----------

_engine: Engine | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        from app.core.config import settings

        _engine = create_engine(settings.database_url, pool_pre_ping=True, pool_size=10, max_overflow=10)
    return _engine


class _Tx:
    def __init__(self, conn: Connection, commit: Callable[[], None], rollback: Callable[[], None]) -> None:
        self.conn, self.commit, self.rollback = conn, commit, rollback


class EngineSource:
    """Production: a pooled connection and a real transaction per unit of work."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def begin(self) -> _Tx:
        conn = self.engine.connect()
        trans = conn.begin()

        def commit() -> None:
            try:
                trans.commit()
            finally:
                conn.close()

        def rollback() -> None:
            try:
                trans.rollback()
            finally:
                conn.close()

        return _Tx(conn, commit, rollback)

    @contextlib.contextmanager
    def autonomous(self) -> Iterator[Connection]:
        with self.engine.begin() as conn:
            yield conn


class ConnectionSource:
    """Tests: everything runs inside one outer transaction that the test rolls back."""

    def __init__(self, conn: Connection) -> None:
        self.conn = conn

    def begin(self) -> _Tx:
        savepoint = self.conn.begin_nested()
        return _Tx(self.conn, savepoint.commit, savepoint.rollback)

    @contextlib.contextmanager
    def autonomous(self) -> Iterator[Connection]:
        with self.conn.begin_nested():
            yield self.conn


# ---------- Store ----------


class Store:
    sites: EntityTable[Site]
    scanners: EntityTable[Scanner]
    exams: EntityTable[Exam]
    patients: EntityTable[Patient]
    referrers: EntityTable[Referrer]
    appointments: EntityTable[Appointment]
    waitlist: EntityTable[WaitlistEntry]
    studies: EntityTable[ImagingStudy]
    requisitions: EntityTable[Requisition]
    labs: EntityTable[LabResult]
    allergies: EntityTable[Allergy]
    staff: EntityTable[StaffUser]
    outbox: EntityTable[MessageOutbox]
    llm_calls: ListTable[LlmCall]

    def __init__(self, source: EngineSource | ConnectionSource | None = None) -> None:
        self._source = source
        self.detached = source is None
        self._tx: _Tx | None = None
        self._touched = False
        self.rewritten = False  # persist() ran in this transaction (tables exclusively locked)
        self._counters: dict[str, int] = {}  # detached only; the database uses sequences
        self._local_version = 0
        self._sessions: dict[str, str] = {}  # detached only
        for name, spec in schema.STORE_ENTITIES.items():
            setattr(self, name, (ListTable if spec.kind == "list" else EntityTable)(self, name))
        self.images = ImageTable(self)
        self.modules = ModuleSpace(self)
        self.fhir = FhirStore(self)  # hospital EHR resources (app/ehr)
        self.audit = AuditLog(self)

    def __repr__(self) -> str:
        return f"<Store {'detached' if self.detached else 'db'}>"

    # ----- connection and transaction -----

    def conn(self) -> Connection:
        if self.detached:
            raise RuntimeError("A detached store has no database connection")
        if self._tx is None:
            self._tx = self._source.begin()
        return self._tx.conn

    def autonomous(self):
        """A separate short transaction that commits on its own (audit events)."""
        return self._source.autonomous()

    def _collections(self) -> list:
        return [*(getattr(self, name) for name in schema.STORE_ENTITIES), self.images, self.modules, self.fhir]

    def flush(self) -> int:
        return sum(c.flush() for c in self._collections())

    def expunge(self) -> None:
        """Drop loaded objects and pending changes; the next read goes to the database."""
        for c in self._collections():
            c.expunge()

    def save(self) -> None:
        """Write pending changes and advance the change counter if anything changed (no commit)."""
        if self._tx is None and not self._touched:
            return
        if self.flush() or self._touched:
            self._bump_version()
        self._touched = False

    def commit(self) -> None:
        if self.detached:
            raise RuntimeError("A detached store cannot be committed; use persist()")
        self.save()
        if self._tx is None:
            return
        tx, self._tx, self.rewritten = self._tx, None, False
        tx.commit()

    def rollback(self) -> None:
        tx, self._tx, self._touched, self.rewritten = self._tx, None, False, False
        self.expunge()
        if tx is not None:
            tx.rollback()

    def close(self) -> None:
        if self._tx is not None:
            self.rollback()

    # ----- ids and change counter -----

    def next_id(self, prefix: str) -> str:
        if self.detached:
            n = self._counters.get(prefix, 0) + 1
            self._counters[prefix] = n
        else:
            n = self.conn().execute(text(f"SELECT nextval('{_ensure_sequence(prefix)}')")).scalar_one()
        return f"{prefix}-{n:05d}"

    def try_lock(self, key: int) -> bool:
        """Take a transaction-scoped advisory lock; False when another process holds it."""
        if self.detached:
            return True
        return bool(self.conn().execute(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": key}).scalar())

    def touch(self) -> None:
        self._touched = True
        self._local_version += 1

    @property
    def version(self) -> int:
        if self.detached:
            return self._local_version
        value = self.conn().execute(select(schema.app_meta.c.value).where(schema.app_meta.c.key == "version")).scalar()
        return int(value or 0)

    def _bump_version(self) -> None:
        self.conn().execute(text(
            "INSERT INTO app_meta (key, value) VALUES ('version', '1') "
            "ON CONFLICT (key) DO UPDATE SET value = (app_meta.value::bigint + 1)::text"))

    # ----- login sessions -----

    def add_session(self, token: str, user_id: str) -> None:
        if self.detached:
            self._sessions[token] = user_id
            return
        self.conn().execute(schema.auth_sessions.insert().values(token=token, user_id=user_id))

    def session_user(self, token: str) -> str | None:
        if not token:
            return None
        if self.detached:
            return self._sessions.get(token)
        table = schema.auth_sessions
        return self.conn().execute(select(table.c.user_id).where(table.c.token == token)).scalar()

    # ----- module state -----

    def module(self, name: str, factory: Callable[[], Any]) -> Any:
        return self.modules.module(name, factory)


_SEQUENCES_READY: set[str] = set()


def _sequence_name(prefix: str) -> str:
    return "idseq_" + re.sub(r"[^a-z0-9]+", "_", prefix.lower())


def _ensure_sequence(prefix: str) -> str:
    """Create the id sequence for a prefix (in its own committed transaction)."""
    name = _sequence_name(prefix)
    if name not in _SEQUENCES_READY:
        with get_engine().begin() as conn:
            conn.execute(text(f"CREATE SEQUENCE IF NOT EXISTS {name}"))
        _SEQUENCES_READY.add(name)
    return name


# ---------- Current unit of work ----------

_current: ContextVar[Store | None] = ContextVar("store", default=None)
_ambient: Store | None = None  # tests: one store shared by the test body and every request


def get_store() -> Store:
    store = _current.get() or _ambient
    if store is None:
        raise RuntimeError("No unit of work is active: wrap the code in `with unit_of_work():`")
    return store


def set_ambient_store(store: Store | None) -> None:
    global _ambient
    _ambient = store


def ambient_store() -> Store | None:
    return _ambient


@contextlib.contextmanager
def use_store(store: Store) -> Iterator[Store]:
    """Make `store` the current one for get_store() inside the block."""
    token = _current.set(store)
    try:
        yield store
    finally:
        _current.reset(token)


@contextlib.contextmanager
def unit_of_work() -> Iterator[Store]:
    """A Store that commits when the block succeeds and rolls back when it raises."""
    if _ambient is not None:
        with use_store(_ambient):
            yield _ambient
        _ambient.save()
        return
    store = Store(EngineSource(get_engine()))
    with use_store(store):
        try:
            yield store
        except BaseException:
            store.rollback()
            raise
        store.commit()


# ---------- Seeding and reset ----------


def persist(src: Store, into: Store) -> None:
    """Replace everything in the database with the contents of a detached store."""
    if not src.detached or into.detached:
        raise ValueError("persist() copies a detached store into a database store")
    conn = into.conn()
    into.expunge()
    tables = ", ".join(f'"{t}"' for t in schema.data_tables())
    conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY"))
    into.rewritten = True

    def insert_rows(table, rows: list[dict]) -> None:
        for i in range(0, len(rows), 2000):
            conn.execute(table.insert(), rows[i:i + 2000])

    for name in schema.STORE_ENTITIES:
        insert_rows(schema.mapping(name).table, getattr(src, name).encoded_rows())
    for table in src.modules.tables():
        insert_rows(schema.mapping(table.name).table, table.encoded_rows())
    insert_rows(schema.module_state, src.modules.encoded_rows())
    insert_rows(schema.images, src.images.encoded_rows())
    insert_rows(schema.audit_events, src.audit.encoded_rows())
    insert_rows(fhir_resources, src.fhir.encoded_rows())
    for prefix, n in src._counters.items():
        name = _ensure_sequence(prefix)
        conn.execute(text("SELECT setval(:seq, :n)"), {"seq": name, "n": n})
    stmt = pg_insert(schema.app_meta).values(key="seeded_at", value=datetime.now().isoformat(timespec="seconds"))
    conn.execute(stmt.on_conflict_do_update(index_elements=[schema.app_meta.c.key], set_={"value": stmt.excluded.value}))
    into.touch()


def build_store(seed: int | None = None) -> Store:
    """The demo data set, generated in memory (detached)."""
    from app.seed import populate

    store = Store()
    with use_store(store):
        populate(store, seed)
    return store


# Held (exclusively) while the demo data is rewritten; background steps take it shared and skip while a reset runs,
# so the TRUNCATE of every table never deadlocks with a step writing into them.
REWRITE_LOCK = 0x5EED_DA7A


def reset_store(seed: int | None = None) -> Store:
    """Regenerate the demo data and write it over the current database contents."""
    store = get_store()
    if not store.detached:
        store.conn().execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": REWRITE_LOCK})
    persist(build_store(seed), store)
    clear_process_cache()
    return store


def is_seeded(store: Store) -> bool:
    table = schema.app_meta
    return store.conn().execute(select(table.c.value).where(table.c.key == "seeded_at")).first() is not None
