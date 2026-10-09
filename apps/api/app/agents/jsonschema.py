"""A small JSON Schema validator for the tool registry (spec 6.4: tools declare input and output JSON Schema).

Covers the subset the tool schemas use: `type` (one or a list), `properties`,
`required`, `additionalProperties` (false or a schema), `items`, `enum`, `const`,
`minLength` / `maxLength`, `pattern`, `minimum` / `maximum`, `minItems` /
`maxItems`, `anyOf`. Anything else in a schema is rejected when the registry
loads (`check_schema`), so a schema never silently means less than it says.
No library: the repository has no JSON Schema package and this subset is small.
"""

from __future__ import annotations

import re
from typing import Any

KEYWORDS = {"type", "properties", "required", "additionalProperties", "items", "enum", "const", "minLength",
            "maxLength", "pattern", "minimum", "maximum", "minItems", "maxItems", "anyOf", "description", "format",
            "title", "default"}
TYPES = {"object", "array", "string", "integer", "number", "boolean", "null"}


def _is(value: Any, kind: str) -> bool:
    if kind == "object":
        return isinstance(value, dict)
    if kind == "array":
        return isinstance(value, list)
    if kind == "string":
        return isinstance(value, str)
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "boolean":
        return isinstance(value, bool)
    return value is None  # null


def check_schema(schema: Any, path: str = "#") -> list[str]:
    """Problems with a schema itself (unknown keywords or types)."""
    if not isinstance(schema, dict):
        return [f"{path}: a schema must be an object"]
    problems = [f"{path}: unsupported keyword {k!r}" for k in schema if k not in KEYWORDS]
    kinds = schema.get("type")
    for kind in kinds if isinstance(kinds, list) else [kinds] if kinds else []:
        if kind not in TYPES:
            problems.append(f"{path}: unknown type {kind!r}")
    for name, sub in (schema.get("properties") or {}).items():
        problems += check_schema(sub, f"{path}/properties/{name}")
    if isinstance(schema.get("additionalProperties"), dict):
        problems += check_schema(schema["additionalProperties"], f"{path}/additionalProperties")
    if "items" in schema:
        problems += check_schema(schema["items"], f"{path}/items")
    for i, sub in enumerate(schema.get("anyOf") or []):
        problems += check_schema(sub, f"{path}/anyOf/{i}")
    if "pattern" in schema:
        try:
            re.compile(schema["pattern"])
        except re.error as e:
            problems.append(f"{path}: bad pattern ({e})")
    return problems


def validate(value: Any, schema: dict, path: str = "$") -> list[str]:
    """Errors for `value` against `schema` (empty list: valid)."""
    errors: list[str] = []
    if "anyOf" in schema:
        if all(validate(value, sub, path) for sub in schema["anyOf"]):
            errors.append(f"{path}: matches none of the allowed shapes")
    kinds = schema.get("type")
    if kinds is not None:
        kinds = kinds if isinstance(kinds, list) else [kinds]
        if not any(_is(value, k) for k in kinds):
            return errors + [f"{path}: expected {' or '.join(kinds)}, got {type(value).__name__}"]
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: must be {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: must be one of {schema['enum']}")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: shorter than {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: longer than {schema['maxLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{path}: does not match {schema['pattern']}")
    if _is(value, "number"):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: below {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: above {schema['maximum']}")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: more than {schema['maxItems']} items")
        if "items" in schema:
            for i, item in enumerate(value):
                errors += validate(item, schema["items"], f"{path}[{i}]")
    if isinstance(value, dict):
        props = schema.get("properties") or {}
        for name in schema.get("required") or []:
            if name not in value:
                errors.append(f"{path}.{name}: required")
        extra = schema.get("additionalProperties", True)
        for name, item in value.items():
            if name in props:
                errors += validate(item, props[name], f"{path}.{name}")
            elif extra is False:
                errors.append(f"{path}.{name}: not allowed")
            elif isinstance(extra, dict):
                errors += validate(item, extra, f"{path}.{name}")
    return errors
