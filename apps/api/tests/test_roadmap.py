"""Roadmap cards (spec 8.1) and safety tiers (spec 8.3, 6.7), WP10: the pages' data comes from two YAML files joined with
the agent and tool registries, the storyline's steps and the eval runs, and must never drift from them."""

import json
import subprocess
import sys
from pathlib import Path

from app.agents import registry
from app.aiops import runs
from app.modules.roadmap import service
from app.modules.storyline import script

API = Path(__file__).resolve().parents[1]
REPO = API.parents[1]
SPEC = (REPO / "docs" / "SPEC-hospital.md").read_text(encoding="utf-8")


def _spec_table(section: str, stop: str) -> list[list[str]]:
    """The rows of the first table in a spec section (without the header and the separator)."""
    body = SPEC.split(section, 1)[1].split(stop, 1)[0]
    rows = [line for line in body.splitlines() if line.startswith("|")]
    return [[c.strip() for c in row.strip("|").split("|")] for row in rows[2:]]


def test_one_card_per_row_of_the_spec_table_in_order_with_its_tier():
    rows = _spec_table("## 8.1", "每张卡片底部")
    cards = service.roadmap().cards
    assert len(rows) == len(cards) == 14
    assert [r[3] for r in rows] == [c.risk_tier for c in cards]
    # the four rows the demo handles on preset data in the spec (预置) or in the storyline (the MPI link)
    preset = [c.id for c in cards if c.demo == "preset"]
    assert preset == ["critical_value_routing", "deterioration_alert", "coding_assist", "mpi"]
    assert [r[5].startswith("预置") for r in rows] == [c.demo == "preset" and c.id != "mpi" for c in cards]


def test_cards_agree_with_the_registries_and_the_repository():
    assert service.check(REPO) == []
    footer = service.roadmap().footer
    assert footer.startswith("Roadmap item, not implemented.")
    assert "docs/data-model.md" in footer and "registry" in footer
    # the storyline's stub cards say the same thing
    assert "docs/data-model.md and the registry" in script.ROADMAP_NOTE


def test_the_storyline_stubs_link_to_their_step_and_registry_entry():
    view = {c["id"]: c for c in service.cards_view()["cards"]}
    for card_id, step_key in (("critical_value_routing", "inr"), ("deterioration_alert", "news2"),
                              ("coding_assist", "coding")):
        card = view[card_id]
        step = script.BY_KEY[step_key]
        assert card["step"] == {"n": step.n, "key": step_key, "title": step.title, "stub": True,
                                "href": f"/storyline#step-{step.n}"}
        entry = card["registry_entry"]
        assert entry["agent_id"] == card_id and entry["kind"] == "module" and entry["risk_tier"] == card["risk_tier"]
        assert entry["eval_status"] == "not_applicable" and entry["path"] == f"apps/api/config/agents/{card_id}.yaml"
        assert card["footer"] == service.roadmap().footer
    mpi = view["mpi"]
    assert mpi["registry_entry"] is None and mpi["step"]["n"] == script.BY_KEY["imaging"].n
    assert "pat-j0001" in mpi["demo_note"] and "PT-J0001" in mpi["demo_note"]
    assert {c["id"] for c in view.values() if c["demo"] == "card"} == set(view) - {
        "critical_value_routing", "deterioration_alert", "coding_assist", "mpi"}


def test_check_reports_drift_from_the_registry():
    spec = registry.agent("coding_assist")
    with registry.temporary("coding_assist", spec.model_copy(update={"risk_tier": "ops"})):
        assert "card coding_assist: risk tier documentation but the registry says ops" in service.check()


def test_every_registry_entry_is_in_exactly_one_tier_and_every_tool_in_one_level():
    view = service.safety_view()
    assert [t["id"] for t in view["tiers"]] == ["ops", "documentation", "clinical_ds"]
    listed = [e["agent_id"] for t in view["tiers"] for e in t["entries"]]
    assert sorted(listed) == sorted(registry.agents()) and len(listed) == len(set(listed))
    for t in view["tiers"]:
        specs = [registry.agent(e["agent_id"]) for e in t["entries"]]
        assert all(s.risk_tier == t["id"] for s in specs)
        assert t["signoff_roles"] == sorted({r for s in specs for r in s.required_signoff_role})
    tools = [x["tool_id"] for lv in view["side_effect_levels"] for x in lv["tools"]]
    assert sorted(tools) == sorted(registry.tools()) and len(tools) == len(set(tools))
    for lv in view["side_effect_levels"]:
        assert all(registry.tool(x["tool_id"]).side_effect_level == lv["id"] for x in lv["tools"])
    privileged = next(lv for lv in view["side_effect_levels"] if lv["id"] == "privileged")
    assert privileged["tools"] and all(x["max_attempts"] == 1 for x in privileged["tools"])
    # the two axes are independent: an ops entry reaches a privileged tool (the bed move, the OR booking)
    ops = next(t for t in view["tiers"] if t["id"] == "ops")
    assert "control_tower" in ops["reach"]["privileged"]
    assert sorted(m["level"] for m in view["level_mapping"]) == ["0-1", "2", "3", "4"]
    assert view["platform"]["entries"] == len(registry.agents()) and view["platform"]["tools"] == len(registry.tools())


def test_tiers_and_positioning_follow_the_spec():
    rows = _spec_table("## 8.3", "**第二个轴")
    assert [r[0] for r in rows] == [t.id for t in service.safety().tiers]
    for sentence in service.safety().positioning:
        assert f'"{sentence.rstrip(".")}"' in SPEC
    boundaries = [b.id for b in service.safety().honest_boundaries]
    assert {"synthetic", "pipeline_not_performance", "drug_tables", "no_hl7_engine"} <= set(boundaries)


def _run_file(folder: Path, run_id: str, mode: str) -> None:
    record = {"run_id": run_id, "started_at": "2026-10-11T09:00:00", "mode": mode, "variant": None,
              "agents": {"deid_check": {"mode": mode, "status": "met", "gates": {
                  "recall": {"value": 0.99, "threshold": 0.98, "passed": True},
                  "precision": {"value": 0.97, "threshold": 0.9, "passed": True}}, "case_results": []}},
              "platform": {"injection": {"passed": True, "cases": 38, "cases_passed": 38,
                                         "unauthorised_tool_calls": 0}}}
    (folder / f"{run_id}.json").write_text(json.dumps(record), encoding="utf-8")
    runs.invalidate()


def test_guardrails_and_boundaries_read_the_latest_runs(tmp_path):
    with runs.use_folder(tmp_path / "runs") as folder:
        folder.mkdir(parents=True)
        empty = service.safety_view()
        assert all(g["live"] is None for g in empty["guardrails"])
        assert empty["platform"]["runs"]["runs"] == 0
        _run_file(folder, "run-2026-10-11T090000", "mock")
        view = service.safety_view()
        live = {g["id"]: g["live"] for g in view["guardrails"]}
        assert live["phi_minimisation"]["gates"]["recall"]["value"] == 0.99
        assert live["injection"] == {"run_id": "run-2026-10-11T090000", "mode": "mock",
                                     "started_at": "2026-10-11T09:00:00", "passed": True, "cases": 38,
                                     "cases_passed": 38, "unauthorised_tool_calls": 0}
        mock = next(b for b in view["honest_boundaries"] if b["id"] == "mock_baselines")
        assert mock["facts"]["live_runs"] == 0 and mock["text"].startswith("Every committed eval result is a mock")
        _run_file(folder, "run-2026-10-11T100000", "anthropic")
        mock = next(b for b in service.safety_view()["honest_boundaries"] if b["id"] == "mock_baselines")
        assert mock["facts"]["live_runs"] == 1 and "real model" in mock["text"]
        assert not mock["text"].startswith("Every committed eval result is a mock")


def test_the_api_serves_both_views_to_any_signed_in_user(client, login):
    assert client.get("/api/roadmap").status_code == 401
    for user in ("U-FD", "U-NURS-05", "U-PHAR-01", "U-CLER-01", "U-ADMIN", "U-REF"):
        headers = login(user)
        cards = client.get("/api/roadmap", headers=headers).json()
        assert cards["counts"]["cards"] == 14 and all(c["footer"] == cards["footer"] for c in cards["cards"])
        safety = client.get("/api/roadmap/safety", headers=headers).json()
        assert [t["id"] for t in safety["tiers"]] == ["ops", "documentation", "clinical_ds"]


def test_the_safety_doc_is_current():
    out = subprocess.run([sys.executable, str(API / "scripts" / "safety_doc.py"), "--check"], cwd=API,
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr

