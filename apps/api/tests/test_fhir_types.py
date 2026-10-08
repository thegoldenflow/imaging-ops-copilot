"""WP2: FHIR type definitions (spec 6.1): one example per resource, seeded data validates."""

import json
from pathlib import Path

import pytest

from app.fhir.types import RESOURCE_TYPES, validate

EXAMPLES = Path(__file__).resolve().parents[1] / "app" / "fhir" / "examples"


def test_every_resource_type_has_an_example():
    assert {p.stem for p in EXAMPLES.glob("*.json")} == set(RESOURCE_TYPES)


@pytest.mark.parametrize("name", sorted(RESOURCE_TYPES))
def test_example_validates_and_round_trips(name):
    data = json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))
    model = validate(data)
    assert model.to_fhir() == data  # nothing lost or added, aliases (class, for) included


def test_seeded_resources_validate(fresh_state):
    for rtype in RESOURCE_TYPES:
        for resource in fresh_state.fhir.search(rtype, limit=150):
            validate(resource)
