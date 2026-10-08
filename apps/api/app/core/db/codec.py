"""JSON encoding that keeps Python types the schema does not describe.

JSONB columns and module state hold values whose Pydantic type is a loose `dict`
or `list` (extraction fields, reminder entries, peer-review state). Plain JSON
would turn their dates into strings and tuples into lists, so a value read back
would differ from the one written. Dates, datetimes, tuples and sets are tagged
instead, e.g. {"$date": "2026-10-07"}; everything else stays ordinary JSON.
"""

from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel

_TAGS = {"$date", "$dt", "$tuple", "$set", "$dict"}


def to_json(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, str):
        return str(value)
    if isinstance(value, datetime):  # before date: datetime is a date subclass
        return {"$dt": value.isoformat()}
    if isinstance(value, date):
        return {"$date": value.isoformat()}
    if isinstance(value, dict):
        if all(isinstance(k, str) for k in value) and not (len(value) == 1 and next(iter(value)) in _TAGS):
            return {k: to_json(v) for k, v in value.items()}
        return {"$dict": [[to_json(k), to_json(v)] for k, v in value.items()]}
    if isinstance(value, list):
        return [to_json(v) for v in value]
    if isinstance(value, tuple):
        return {"$tuple": [to_json(v) for v in value]}
    if isinstance(value, (set, frozenset)):
        return {"$set": [to_json(v) for v in sorted(value, key=repr)]}
    if isinstance(value, BaseModel):
        # Would come back as a plain dict; register the model as a table or config instead.
        raise TypeError(f"Untyped state cannot hold a {type(value).__name__} model")
    raise TypeError(f"Cannot store a {type(value).__name__} value")


def _key(value: Any) -> Any:
    # Lists are not hashable; a tuple key encoded as a list comes back as a tuple.
    return tuple(value) if isinstance(value, list) else value


def from_json(value: Any) -> Any:
    if isinstance(value, list):
        return [from_json(v) for v in value]
    if not isinstance(value, dict):
        return value
    if len(value) == 1:
        tag, inner = next(iter(value.items()))
        if tag == "$dt":
            return datetime.fromisoformat(inner)
        if tag == "$date":
            return date.fromisoformat(inner)
        if tag == "$tuple":
            return tuple(from_json(v) for v in inner)
        if tag == "$set":
            return {_key(from_json(v)) for v in inner}
        if tag == "$dict":
            return {_key(from_json(k)): from_json(v) for k, v in inner}
    return {k: from_json(v) for k, v in value.items()}
