"""Collections behind the Store, backed by PostgreSQL.

The modules were written against plain dicts and lists of Pydantic models
(`store.appointments[aid].status = "cancelled"`). These classes keep that
interface and add a unit of work underneath:

- Reads load rows into an identity map (one object per row per unit of work).
- Each loaded row keeps a snapshot of its column values. At flush, an object is
  compared with its snapshot and only changed columns are written, so code that
  mutates a model in place needs no explicit save.
- New keys are upserted, deleted keys deleted.
- A query that goes to the database (full load, due-item claim, blind-index
  lookup) first flushes the table, so it sees this unit of work's own changes.
- Decoded rows are kept in a per-process cache keyed by row and `xmin` (the id of
  the transaction that wrote that row version). A full-table load reads only keys
  and xmin, and fetches, decrypts and validates just the rows that changed; the
  others are copied from the cache. Every UPDATE gives a row a new xmin, except
  several writes in one transaction: those come from this process's own flushes,
  which drop the cached rows they write.

A detached store (no database) has every collection complete and empty from the
start; the seed generator and the evals build data that way in memory.
"""

from __future__ import annotations

import copy
import json
import types
from collections.abc import Callable, Iterator
from datetime import date, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Generic, Literal, TypeVar, Union, get_args, get_origin

from pydantic import BaseModel, TypeAdapter
from sqlalchemy import bindparam, delete, insert, literal_column, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.db import schema
from app.core.db.codec import from_json, to_json
from app.core.db.crypto import cipher

if TYPE_CHECKING:
    from app.core.store import Store

T = TypeVar("T", bound=BaseModel)
_MISSING: Any = object()
_POINT_READS = 16  # single-row reads per table and unit of work before loading the whole table
_XMIN = literal_column("xmin::text::bigint").label("xmin")
_IN_CHUNK = 5000

# table name -> row key -> (xmin, snapshot, pristine model). Shared by every unit of work in
# the process; entries are never handed out directly, only copies (see _RowCodec.clone).
_ROW_CACHE: dict[str, dict[str, tuple[int, dict, BaseModel]]] = {}


def _row_cache(name: str) -> dict[str, tuple[int, dict, BaseModel]]:
    return _ROW_CACHE.setdefault(name, {})


def _blind_value(value: Any) -> str:
    return value.isoformat() if isinstance(value, (date, datetime)) else str(value)


# ---------- Copies of cached rows ----------
# copy.deepcopy is slow on Pydantic models; these copy only what can be mutated.

_IMMUTABLE = (str, int, float, bool, type(None), date, datetime, Enum)
_set = object.__setattr__


def _shallow(obj: BaseModel) -> BaseModel:
    """A new model instance with the same field values (what model_copy() does, minus overhead)."""
    new = obj.__class__.__new__(obj.__class__)
    _set(new, "__dict__", obj.__dict__.copy())
    _set(new, "__pydantic_fields_set__", obj.__pydantic_fields_set__.copy())
    _set(new, "__pydantic_extra__", None if obj.__pydantic_extra__ is None else obj.__pydantic_extra__.copy())
    _set(new, "__pydantic_private__", None if obj.__pydantic_private__ is None else obj.__pydantic_private__.copy())
    return new


def _fresh(value: Any) -> Any:
    """A copy sharing nothing mutable with `value`."""
    if isinstance(value, _IMMUTABLE):
        return value
    kind = type(value)
    if kind is list:
        return [_fresh(v) for v in value]
    if kind is dict:
        return {k: _fresh(v) for k, v in value.items()}
    if kind is tuple:
        return tuple(_fresh(v) for v in value)
    if isinstance(value, BaseModel):
        new = _shallow(value)
        fields = new.__dict__
        for k, v in fields.items():
            fields[k] = _fresh(v)
        return new
    return copy.deepcopy(value)


def _is_immutable_type(annotation: Any) -> bool:
    if get_origin(annotation) is Literal:
        return True
    return isinstance(annotation, type) and issubclass(annotation, _IMMUTABLE)


def _copier(annotation: Any) -> Callable[[Any], Any]:
    """How to copy a JSON field: containers of immutable values need only a container copy."""
    origin, args = get_origin(annotation), get_args(annotation)
    if origin in (Union, types.UnionType) and len(args) == 2 and type(None) in args:
        return _copier(next(a for a in args if a is not type(None)))
    if origin in (list, set, frozenset, tuple, dict) and args and all(a is ... or _is_immutable_type(a) for a in args):
        return lambda v: v if v is None or isinstance(v, (tuple, frozenset)) else v.copy()
    return _fresh


class _RowCodec:
    """Model <-> plain row (snapshot form) <-> database row (encrypted form). One per table."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.m = schema.mapping(name)
        self._scalar = [f for f in self.m.fields if f not in self.m.json_fields]
        self._json = [f for f in self.m.fields if f in self.m.json_fields]
        self._dump: set[str] = set()  # JSON fields seen holding models: serialized through model_dump
        self._adapter: TypeAdapter | None = None
        self._copiers = [(f, _copier(self.m.model.model_fields[f].annotation)) for f in self._json]

    def plain(self, obj: BaseModel) -> dict:
        # Runs for every loaded object at each flush, so it reads attributes directly:
        # scalar columns hold immutable values and can be compared as they are.
        values = obj.__dict__
        plain = {f: values[f] for f in self._scalar}
        for f in self._json:
            if f not in self._dump:
                try:
                    plain[f] = to_json(values[f])
                    continue
                except TypeError:  # a nested model; to_json does not take those
                    self._dump.add(f)
            plain[f] = to_json(obj.model_dump(include={f})[f])
        return plain

    def encode(self, plain: dict) -> dict:
        m = self.m
        row = dict(plain)
        if m.encrypted or m.blind:
            c = cipher()
            for col, src in m.blind:
                row[col] = None if plain[src] is None else c.blind_index(_blind_value(plain[src]))
            for f in m.encrypted:
                value = plain[f]
                if value is not None:
                    payload = value if f in m.json_fields else to_json(value)
                    row[f] = c.encrypt(json.dumps(payload), f"{self.name}.{f}")
        return row

    def decode_many(self, rows) -> list[tuple[dict, BaseModel]]:
        """Database rows -> (snapshot, model) pairs. Validating the whole batch in one call
        is several times faster than one model_validate per row (full-table loads)."""
        m = self.m
        plains = []
        for row in rows:
            plain = {f: row[f] for f in m.fields}
            for f in m.encrypted:
                value = plain[f]
                if value is not None:
                    value = json.loads(cipher().decrypt(value, f"{self.name}.{f}"))
                    plain[f] = value if f in m.json_fields else from_json(value)
            plains.append(plain)
        if m.json_fields:
            datas = [{f: (from_json(v) if f in m.json_fields else v) for f, v in p.items()} for p in plains]
        else:
            datas = plains  # validation does not modify its input
        if self._adapter is None:
            self._adapter = TypeAdapter(list[m.model])
        return list(zip(plains, self._adapter.validate_python(datas)))

    def clone(self, obj: T) -> T:
        """An independent copy: scalar values are immutable and shared, JSON values are copied."""
        new = _shallow(obj)
        values = new.__dict__
        for f, copier in self._copiers:
            values[f] = copier(values[f])
        return new

    def update_values(self, plain: dict, changed: set[str]) -> dict:
        """Encoded values for the changed columns, plus blind indexes that depend on them."""
        encoded = self.encode(plain)
        cols = set(changed) | {col for col, src in self.m.blind if src in changed}
        return {c: encoded[c] for c in cols}


_CODECS: dict[str, _RowCodec] = {}


def _codec(name: str) -> _RowCodec:
    if name not in _CODECS:
        _CODECS[name] = _RowCodec(name)
    return _CODECS[name]


class EntityTable(Generic[T]):
    """A dict of models keyed by id (or another key), stored one row per entry."""

    def __init__(self, store: Store, name: str) -> None:
        self._store = store
        self._codec = _codec(name)
        self.name = name
        self.model = self._codec.m.model
        self.expunge()

    def expunge(self) -> None:
        """Forget loaded objects (after a flush, or to discard pending changes)."""
        self._map: dict[str, T] = {}
        self._snap: dict[str, dict] = {}
        self._absent: set[str] = set()
        self._deleted: set[str] = set()
        self._cleared = False
        self._complete = self._store.detached
        self._misses = 0

    @property
    def c(self):
        return self._codec.m.table.c

    # ---------- loading ----------

    def _from_cache(self, key: str, xmin: int) -> bool:
        """Adopt a copy of the cached row if it is this row version."""
        hit = _row_cache(self.name).get(key)
        if hit is None or hit[0] != xmin:
            return False
        self._map[key], self._snap[key] = self._codec.clone(hit[2]), hit[1]
        return True

    def _adopt(self, rows) -> list[T]:
        """The objects for these rows: already loaded ones as they are, the rest from the
        row cache or decoded. Rows deleted in this unit of work are left out."""
        rows = [r for r in rows if r["row_key"] not in self._deleted]
        fresh = [r for r in rows if r["row_key"] not in self._map and not self._from_cache(r["row_key"], r["xmin"])]
        cache = _row_cache(self.name)
        for row, (plain, obj) in zip(fresh, self._codec.decode_many(fresh)):
            key = row["row_key"]
            self._map[key], self._snap[key] = obj, plain
            cache[key] = (row["xmin"], plain, self._codec.clone(obj))
        return [self._map[r["row_key"]] for r in rows]

    def _select(self, *where, lock: bool = False):
        table = self._codec.m.table
        query = select(table, _XMIN).where(*where).order_by(table.c.seq)
        if lock:
            query = query.with_for_update(skip_locked=True)
        return self._store.conn().execute(query).mappings()

    def _load_all(self) -> None:
        if self._complete:
            return
        self.flush()
        table = self._codec.m.table
        versions = self._store.conn().execute(select(table.c.row_key, _XMIN).order_by(table.c.seq)).all()
        wanted = [key for key, xmin in versions if key not in self._map and not self._from_cache(key, xmin)]
        if len(wanted) > len(versions) // 2:
            self._adopt(self._select().all())
        else:
            for i in range(0, len(wanted), _IN_CHUNK):
                self._adopt(self._select(table.c.row_key.in_(wanted[i:i + _IN_CHUNK])).all())
        # Database order; a row deleted by another transaction between the two reads is skipped.
        self._map = {key: self._map[key] for key, _ in versions if key in self._map}
        self._complete = True
        self._absent.clear()

    # ---------- dict interface ----------

    def get(self, key: str, default: Any = None) -> T | Any:
        if key in self._map:
            return self._map[key]
        if self._complete or key in self._absent or key in self._deleted:
            return default
        self._misses += 1
        if self._misses > _POINT_READS:
            # Code that looks up many keys one by one (a loop over studies reading their
            # appointments) would issue a query per key; read the whole table instead.
            self._load_all()
            return self._map.get(key, default)
        row = self._select(self._codec.m.table.c.row_key == key).first()
        if row is None:
            self._absent.add(key)
            return default
        return self._adopt([row])[0]

    def forget(self, key: str) -> None:
        """Drop this unit of work's copy of one row, so the next read sees what other transactions committed since
        (e.g. after taking a lock that serialises work on it). Pending changes are written first."""
        self.flush()
        self._map.pop(key, None)
        self._snap.pop(key, None)
        self._absent.discard(key)
        self._complete = self._store.detached

    def __getitem__(self, key: str) -> T:
        obj = self.get(key, _MISSING)
        if obj is _MISSING:
            raise KeyError(key)
        return obj

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and self.get(key, _MISSING) is not _MISSING

    def __setitem__(self, key: str, obj: T) -> None:
        if not isinstance(key, str):
            raise TypeError(f"{self.name}: keys must be strings, not {type(key).__name__}")
        if not isinstance(obj, self.model):
            raise TypeError(f"{self.name} holds {self.model.__name__}, not {type(obj).__name__}")
        self._map[key] = obj
        self._absent.discard(key)
        self._deleted.discard(key)

    def __delitem__(self, key: str) -> None:
        if key not in self:
            raise KeyError(key)
        self._map.pop(key, None)
        self._snap.pop(key, None)
        self._deleted.add(key)

    def pop(self, key: str, default: Any = _MISSING) -> T | Any:
        obj = self.get(key, _MISSING)
        if obj is _MISSING:
            if default is _MISSING:
                raise KeyError(key)
            return default
        del self[key]
        return obj

    def setdefault(self, key: str, default: T) -> T:
        obj = self.get(key, _MISSING)
        if obj is _MISSING:
            self[key] = default
            return default
        return obj

    def update(self, other: dict[str, T]) -> None:
        for key, obj in other.items():
            self[key] = obj

    def clear(self) -> None:
        self._map, self._snap = {}, {}
        self._absent, self._deleted = set(), set()
        self._cleared = self._complete = True

    def keys(self) -> list[str]:
        self._load_all()
        return list(self._map)

    def values(self) -> list[T]:
        self._load_all()
        return list(self._map.values())

    def items(self) -> list[tuple[str, T]]:
        self._load_all()
        return list(self._map.items())

    def __iter__(self) -> Iterator[str]:
        return iter(self.keys())

    def __len__(self) -> int:
        self._load_all()
        return len(self._map)

    def __bool__(self) -> bool:
        return not self.is_empty()

    def __repr__(self) -> str:
        return f"<EntityTable {self.name}>"

    # ---------- queries ----------

    def is_empty(self) -> bool:
        if self._complete or self._map:
            return not self._map
        self.flush()
        table = self._codec.m.table
        return self._store.conn().execute(select(table.c.seq).limit(1)).first() is None

    def find_by(self, field: str, value: Any) -> list[T]:
        """Rows whose field equals value. Encrypted fields are matched through their blind index."""
        def match(obj: T) -> bool:
            return getattr(obj, field) == value

        if self._complete:
            return [o for o in self._map.values() if match(o)]
        self.flush()
        m = self._codec.m
        if field in m.encrypted:
            col = next((c for c, src in m.blind if src == field), None)
            if col is None:
                raise ValueError(f"{self.name}.{field} is encrypted and has no blind index")
            condition = m.table.c[col] == cipher().blind_index(_blind_value(value))
        else:
            condition = m.table.c[field] == value
        return [o for o in self._adopt(self._select(condition).all()) if match(o)]

    def claim_due(self, column: str, now: datetime, statuses: tuple[str, ...] | None = None) -> list[T]:
        """Rows due at `now` (column <= now, optional status filter), locked for this unit of work.

        Rows another worker has locked are skipped, so two workers never process
        the same item.
        """
        def due(obj: T) -> bool:
            when = getattr(obj, column)
            return when is not None and when <= now and (statuses is None or getattr(obj, "status") in statuses)

        if self._store.detached:
            return [o for o in self._map.values() if due(o)]
        self.flush()
        table = self._codec.m.table
        where = [table.c[column].is_not(None), table.c[column] <= now]
        if statuses is not None:
            where.append(table.c.status.in_(statuses))
        return [o for o in self._adopt(self._select(*where, lock=True).all()) if due(o)]

    # ---------- writing ----------

    def encoded_rows(self) -> list[dict]:
        """Every entry as an insertable row (used to persist a detached store)."""
        return [self._codec.encode(self._codec.plain(obj)) | {"row_key": key} for key, obj in self._map.items()]

    def flush(self) -> int:
        if self._store.detached:
            return 0
        conn = self._store.conn()
        table = self._codec.m.table
        cache = _row_cache(self.name)
        written = 0
        if self._cleared:
            conn.execute(delete(table))
            cache.clear()
            self._cleared, written = False, written + 1
        if self._deleted:
            conn.execute(delete(table).where(table.c.row_key.in_(sorted(self._deleted))))
            for key in self._deleted:
                cache.pop(key, None)
            written += len(self._deleted)
            self._deleted = set()
        inserts: list[dict] = []
        updates: dict[frozenset[str], list[dict]] = {}
        for key, obj in self._map.items():
            plain = self._codec.plain(obj)
            old = self._snap.get(key)
            if old is None:
                inserts.append(self._codec.encode(plain) | {"row_key": key})
            elif plain != old:
                changed = {f for f, v in plain.items() if old.get(f) != v}
                values = self._codec.update_values(plain, changed)
                updates.setdefault(frozenset(values), []).append({**values, "_key": key})
            else:
                continue
            self._snap[key] = plain
            cache.pop(key, None)  # this transaction may write the row again under the same xmin
        if inserts:
            stmt = pg_insert(table)
            columns = [c.name for c in table.columns if c.name not in ("row_key", "seq")]
            stmt = stmt.on_conflict_do_update(index_elements=[table.c.row_key],
                                              set_={c: stmt.excluded[c] for c in columns})
            conn.execute(stmt, inserts)
            written += len(inserts)
        for cols, rows in updates.items():
            stmt = update(table).where(table.c.row_key == bindparam("_key")).values({c: bindparam(c) for c in cols})
            conn.execute(stmt, rows)
            written += len(rows)
        return written


class ListTable(Generic[T]):
    """An append-only list of models, ordered by insertion."""

    def __init__(self, store: Store, name: str) -> None:
        self._store = store
        self._codec = _codec(name)
        self.name = name
        self.model = self._codec.m.model
        self.expunge()

    def expunge(self) -> None:
        self._items: list[T] = []
        self._seqs: list[int | None] = []  # None: not written yet
        self._snap: dict[int, dict] = {}
        self._complete = self._store.detached

    def _load(self) -> None:
        if self._complete:
            return
        self.flush()
        known = {seq: obj for seq, obj in zip(self._seqs, self._items)}
        table = self._codec.m.table
        rows = self._store.conn().execute(select(table).order_by(table.c.seq)).mappings().all()
        fresh = [r for r in rows if r["seq"] not in known]
        for row, (plain, obj) in zip(fresh, self._codec.decode_many(fresh)):
            known[row["seq"]], self._snap[row["seq"]] = obj, plain
        self._items = [known[r["seq"]] for r in rows]
        self._seqs, self._complete = [r["seq"] for r in rows], True

    def append(self, obj: T) -> None:
        if not isinstance(obj, self.model):
            raise TypeError(f"{self.name} holds {self.model.__name__}, not {type(obj).__name__}")
        self._items.append(obj)
        self._seqs.append(None)

    def extend(self, objs) -> None:
        for obj in objs:
            self.append(obj)

    def __len__(self) -> int:
        self._load()
        return len(self._items)

    def __iter__(self) -> Iterator[T]:
        self._load()
        return iter(list(self._items))

    def __reversed__(self) -> Iterator[T]:
        self._load()
        return reversed(list(self._items))

    def __getitem__(self, index):
        self._load()
        return self._items[index]

    def __bool__(self) -> bool:
        return len(self) > 0

    def __repr__(self) -> str:
        return f"<ListTable {self.name}>"

    def encoded_rows(self) -> list[dict]:
        return [self._codec.encode(self._codec.plain(obj)) for obj in self._items]

    def flush(self) -> int:
        if self._store.detached:
            return 0
        conn = self._store.conn()
        table = self._codec.m.table
        written = 0
        new = [i for i, seq in enumerate(self._seqs) if seq is None]
        if new:
            plains = [self._codec.plain(self._items[i]) for i in new]
            result = conn.execute(insert(table).returning(table.c.seq, sort_by_parameter_order=True),
                                  [self._codec.encode(p) for p in plains])
            for i, plain, seq in zip(new, plains, result.scalars().all()):
                self._seqs[i], self._snap[seq] = seq, plain
            written += len(new)
        for obj, seq in zip(self._items, self._seqs):
            if seq is None or seq not in self._snap:
                continue
            plain = self._codec.plain(obj)
            old = self._snap[seq]
            if plain != old:
                changed = {f for f, v in plain.items() if old.get(f) != v}
                conn.execute(update(table).where(table.c.seq == seq).values(self._codec.update_values(plain, changed)))
                self._snap[seq] = plain
                written += 1
        return written


class ImageTable:
    """Image bytes by key: key -> (bytes, media type). Entries are never edited, only added or removed."""

    def __init__(self, store: Store) -> None:
        self._store = store
        self.expunge()

    def expunge(self) -> None:
        self._data: dict[str, tuple[bytes, str]] = {}
        self._keys: set[str] | None = set() if self._store.detached else None
        self._new: dict[str, tuple[bytes, str]] = {}
        self._deleted: set[str] = set()

    def _all_keys(self) -> set[str]:
        if self._keys is None:
            table = schema.images
            self._keys = set(self._store.conn().execute(select(table.c.row_key)).scalars())
            self._keys |= set(self._new)
            self._keys -= self._deleted
        return self._keys

    def __contains__(self, key: object) -> bool:
        return key in self._all_keys()

    def get(self, key: str, default: Any = None):
        if key not in self._all_keys():
            return default
        if key not in self._data:
            table = schema.images
            row = self._store.conn().execute(select(table.c.data, table.c.media_type).where(table.c.row_key == key)).first()
            self._data[key] = (bytes(row.data), row.media_type)
        return self._data[key]

    def __getitem__(self, key: str) -> tuple[bytes, str]:
        value = self.get(key, _MISSING)
        if value is _MISSING:
            raise KeyError(key)
        return value

    def __setitem__(self, key: str, value: tuple[bytes, str]) -> None:
        data, media_type = value
        self._all_keys().add(key)
        self._data[key] = self._new[key] = (bytes(data), media_type)
        self._deleted.discard(key)

    def __delitem__(self, key: str) -> None:
        if key not in self:
            raise KeyError(key)
        self._all_keys().discard(key)
        self._data.pop(key, None)
        self._new.pop(key, None)
        self._deleted.add(key)

    def keys(self) -> list[str]:
        return sorted(self._all_keys())

    def __iter__(self) -> Iterator[str]:
        return iter(self.keys())

    def __len__(self) -> int:
        return len(self._all_keys())

    def items(self) -> list[tuple[str, tuple[bytes, str]]]:
        return [(k, self[k]) for k in self.keys()]

    def values(self) -> list[tuple[bytes, str]]:
        return [self[k] for k in self.keys()]

    def encoded_rows(self) -> list[dict]:
        return [{"row_key": k, "data": d, "media_type": t} for k, (d, t) in self._new.items()]

    def flush(self) -> int:
        if self._store.detached:
            return 0
        conn = self._store.conn()
        table = schema.images
        written = 0
        if self._deleted:
            conn.execute(delete(table).where(table.c.row_key.in_(sorted(self._deleted))))
            written += len(self._deleted)
            self._deleted = set()
        if self._new:
            stmt = pg_insert(table)
            stmt = stmt.on_conflict_do_update(index_elements=[table.c.row_key],
                                              set_={"data": stmt.excluded.data, "media_type": stmt.excluded.media_type})
            conn.execute(stmt, self.encoded_rows())
            written += len(self._new)
            self._new = {}
        return written


# Derived values shared by every unit of work in this process (trained models,
# search indexes). Rebuilt on demand, dropped on reset.
_PROCESS_CACHE: dict[str, Any] = {}


def clear_process_cache() -> None:
    _PROCESS_CACHE.clear()
    _ROW_CACHE.clear()


class ModuleSpace:
    """`store.modules`: the state each module keeps, resolved by name through the schema registry."""

    def __init__(self, store: Store) -> None:
        self._store = store
        self._tables: dict[str, EntityTable | ListTable] = {}
        self.expunge()

    def expunge(self) -> None:
        for table in self._tables.values():
            table.expunge()
        self._values: dict[str, Any] = {}
        self._snap: dict[str, Any] = {}
        self._deleted: set[str] = set()
        self._checked: set[str] = set()

    @staticmethod
    def _kind(name: str) -> str:
        if name in schema.MODULE_ENTITIES:
            return "table"
        if name in schema.CONFIGS:
            return "config"
        if name in schema.BLOBS:
            return "blob"
        if name in schema.CACHES:
            return "cache"
        raise KeyError(f"Unregistered module state {name!r}: add it to app/core/db/schema.py")

    def table(self, name: str) -> EntityTable | ListTable:
        if name not in self._tables:
            cls = ListTable if schema.MODULE_ENTITIES[name].kind == "list" else EntityTable
            self._tables[name] = cls(self._store, name)
        return self._tables[name]

    def _load(self, name: str) -> Any:
        if name in self._values:
            return self._values[name]
        if name in self._deleted or self._store.detached:
            return _MISSING
        table = schema.module_state
        row = self._store.conn().execute(select(table.c.data).where(table.c.name == name)).first()
        if row is None:
            return _MISSING
        value = from_json(row.data)
        if name in schema.CONFIGS:
            value = schema.config_model(name).model_validate(value)
        self._values[name], self._snap[name] = value, row.data
        return value

    def get(self, name: str, default: Any = None) -> Any:
        kind = self._kind(name)
        if kind == "table":
            return self.table(name)
        if kind == "cache":
            return _PROCESS_CACHE.get(name, default)
        value = self._load(name)
        return default if value is _MISSING else value

    def __getitem__(self, name: str) -> Any:
        value = self.get(name, _MISSING)
        if value is _MISSING:
            raise KeyError(name)
        return value

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and self.get(name, _MISSING) is not _MISSING

    def __setitem__(self, name: str, value: Any) -> None:
        kind = self._kind(name)
        if kind == "table":
            raise TypeError(f"{name} is a table; write its entries instead of replacing it")
        if kind == "cache":
            _PROCESS_CACHE[name] = value
            return
        if kind == "config" and not isinstance(value, schema.config_model(name)):
            raise TypeError(f"{name} must be a {schema.config_model(name).__name__}")
        self._values[name] = value
        self._deleted.discard(name)

    def pop(self, name: str, default: Any = None) -> Any:
        kind = self._kind(name)
        if kind == "table":
            self.table(name).clear()
            return default
        if kind == "cache":
            return _PROCESS_CACHE.pop(name, default)
        value = self._load(name)
        self._values.pop(name, None)
        self._deleted.add(name)
        return default if value is _MISSING else value

    def setdefault(self, name: str, default: Any) -> Any:
        value = self.get(name, _MISSING)
        if value is _MISSING:
            self[name] = default
            return default
        return value

    def module(self, name: str, factory: Callable[[], Any]) -> Any:
        """The named state, created from `factory` the first time it is needed."""
        kind = self._kind(name)
        if kind == "table":
            table = self.table(name)
            if name not in self._checked:
                self._checked.add(name)
                if factory not in (dict, list) and table.is_empty():
                    content = factory()
                    if isinstance(table, ListTable):
                        table.extend(content)
                    else:
                        table.update(content)
            return table
        if kind == "cache":
            if name not in _PROCESS_CACHE:
                _PROCESS_CACHE[name] = factory()
            return _PROCESS_CACHE[name]
        value = self._load(name)
        if value is _MISSING:
            value = factory()
            self[name] = value
        return value

    def _encode(self, name: str, value: Any) -> Any:
        return to_json(value.model_dump() if isinstance(value, BaseModel) else value)

    def encoded_rows(self) -> list[dict]:
        return [{"name": name, "data": self._encode(name, value)} for name, value in self._values.items()]

    def tables(self) -> list[EntityTable | ListTable]:
        return list(self._tables.values())

    def flush(self) -> int:
        written = sum(table.flush() for table in self._tables.values())
        if self._store.detached:
            return written
        conn = self._store.conn()
        table = schema.module_state
        if self._deleted:
            conn.execute(delete(table).where(table.c.name.in_(sorted(self._deleted))))
            written += len(self._deleted)
            self._deleted = set()
        for name, value in self._values.items():
            data = self._encode(name, value)
            if data == self._snap.get(name, _MISSING):
                continue
            stmt = pg_insert(table).values(name=name, data=data)
            conn.execute(stmt.on_conflict_do_update(index_elements=[table.c.name],
                                                    set_={"data": stmt.excluded.data, "updated_at": datetime.now()}))
            self._snap[name] = data
            written += 1
        return written
