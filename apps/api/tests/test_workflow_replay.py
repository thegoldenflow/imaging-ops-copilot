"""Replay tests (spec 6.5): each workflow's captured history replays against the current code.

The histories in tests/fixtures/workflows/ were recorded by tests/test_workflows.py (CAPTURE_WORKFLOW_HISTORIES=1)
from complete runs. A code change that would make a running workflow take a different path (and so break when its
worker restarts) fails here; such a change needs `workflow.patched(...)`. `journey_v1.json` was recorded before the
journey's coding step existed: it replays through the `patched` branch for old executions.

No server and no database: Temporal's Replayer runs the workflow code against the recorded events.
"""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path

import pytest
from temporalio.client import WorkflowHistory
from temporalio.worker import Replayer

from app.workflows import model as M
from app.workflows.journey import CODING_PATCH
from app.workflows.worker import WORKFLOWS

HISTORIES = Path(__file__).parent / "fixtures" / "workflows"
EXPECTED = {"journey": M.JOURNEY, "journey_v1": M.JOURNEY, "journey_medrec_retry": M.JOURNEY,
            "capacity": M.CAPACITY, "capacity_rejected": M.CAPACITY, "review": M.REVIEW}


def _history(name: str) -> WorkflowHistory:
    path = HISTORIES / f"{name}.json"
    assert path.exists(), f"{path} is missing: capture it with CAPTURE_WORKFLOW_HISTORIES=1 pytest tests/test_workflows.py"
    return WorkflowHistory.from_json(f"{name}-replay", path.read_text(encoding="utf-8"))


def _type(history: WorkflowHistory) -> str:
    first = history.events[0].workflow_execution_started_event_attributes
    return first.workflow_type.name


def _patches(name: str) -> list[str]:
    """The ids of the `patched()` markers a history recorded."""
    data = json.loads((HISTORIES / f"{name}.json").read_text(encoding="utf-8"))
    out = []
    for e in data["events"]:
        marker = e.get("markerRecordedEventAttributes")
        if not marker or marker.get("markerName") != "core_patch":
            continue
        for payload in (marker.get("details") or {}).get("patch-data", {}).get("payloads", []):
            out.append(json.loads(base64.b64decode(payload["data"]))["id"])
    return out


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_captured_history_replays_against_the_current_code(name):
    history = _history(name)
    assert _type(history) == EXPECTED[name]
    asyncio.run(Replayer(workflows=WORKFLOWS).replay_workflow(history))


def test_every_workflow_has_a_replay_test():
    assert set(EXPECTED.values()) == set(M.TYPES)


def test_the_version_1_journey_has_no_patch_marker_and_the_current_one_has():
    """`patched()` writes a marker into new executions; the version-1 history has none, so it replays the old path."""
    assert _patches("journey_v1") == []
    assert _patches("journey") == [CODING_PATCH]
