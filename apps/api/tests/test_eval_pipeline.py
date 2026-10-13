"""Model calls a suite makes for another agent on the same case (the live run run-2026-10-12T225622): the triage
suite extracts each requisition first, the order review suite reconciles the admission of a new medication, the
documentation suite reconciles the discharge. Those calls counted toward no agent. The agent's own metrics stay as
they are; the run file reports the whole case per suite (`pipeline`) and per case (`pipeline_cost_usd`)."""

from __future__ import annotations

import json

import pytest

from app.agents import registry
from app.aiops import evalrun, runs, suites
from app.llm.providers import MOCK_FIXTURES, ProviderUnavailable
from app.modules.documentation import evals as doc_evals
from app.modules.evals import run as imaging
from app.modules.med_safety import evals as ms

EXTRACTION, TRIAGE = (next(s for s in suites.IMAGING if s.id == f"imaging.{t}") for t in ("extraction", "triage"))


def _run(suite, store) -> dict[str, dict]:
    """The suite as the runner runs it (its collecting gateway, mock model)."""
    specs = [registry.require_agent(a) for a in suite.agents]
    out = evalrun.run_suite(suite, specs, gateway=evalrun._gateway(False), store=store, overrides={}, variant=None,
                            live=False)
    assert all(e["status"] != "error" for e in out.values()), {a: e.get("error") for a, e in out.items()}
    return out


def _requisitions(monkeypatch, tmp_path, ids: list[str]) -> None:
    items = {i["id"]: i for i in imaging._load("requisitions")}
    monkeypatch.setattr(imaging, "_load", lambda name: [items[i] for i in ids])
    monkeypatch.setattr(imaging, "RESULTS", tmp_path)  # the imaging suites also write evals/results/<task>.json


def test_triage_reports_the_extraction_it_runs_before_each_case(fresh_state, monkeypatch, tmp_path):
    """req-05 in the live run: case cost 0.003502 was the triage call alone; the extraction call ($0.008417) made for
    the same case counted nowhere."""
    _requisitions(monkeypatch, tmp_path, ["req-00", "req-01", "req-02"])
    entry = _run(TRIAGE, fresh_state)["requisition_triage"]
    m = entry["metrics"]
    assert m["model_calls"] == 3, "the agent's own metrics: its triage calls only"

    p = entry["pipeline"]
    extract = p["other_agents"]["requisition_extract"]
    assert extract["model_calls"] == 3 and extract["cases"] == 3 and extract["failed_calls"] == 0
    assert extract["cost_usd"] > 0 and extract["tokens_in"] > 0
    assert p["model_calls"] == 6 and p["cases"] == 3 and p["cost_estimated"] is True
    assert p["cost_per_case_usd"] == pytest.approx(m["cost_per_case_usd"] + extract["cost_usd"] / 3, abs=2e-6)
    assert p["latency_p50_ms"] is not None and p["latency_p95_ms"] is not None
    for case in entry["case_results"]:
        assert case["pipeline_cost_usd"] > case["cost_usd"]
        assert case["pipeline_latency_ms"] >= case["latency_ms"]


def test_a_failed_extraction_counts_against_triage_schema_validity_and_shows_in_the_pipeline(
        fresh_state, monkeypatch, tmp_path):
    """The first live run (run-2026-10-11T235646): 4 extraction calls failed after about 56 s. Their triage rows had
    no schema_valid, so triage schema_validity read 1.0, and the cases showed no latency or cost at all."""
    _requisitions(monkeypatch, tmp_path, ["req-01", "req-02"])
    real = MOCK_FIXTURES["requisition_extract"]

    def down_for_req_02(text, images, attempt):
        if "query fibrosis" in text:  # req-02's letter
            raise ProviderUnavailable("timeout")
        return real(text, images, attempt)

    monkeypatch.setitem(MOCK_FIXTURES, "requisition_extract", down_for_req_02)
    entry = _run(TRIAGE, fresh_state)["requisition_triage"]
    cases = {c["id"]: c for c in entry["case_results"]}
    assert cases["req-02"]["passed"] is False and cases["req-02"]["schema_valid"] is False
    assert entry["metrics"]["schema_validity"] == 0.5
    assert entry["metrics"]["model_calls"] == 1, "no triage call for req-02"

    extract = entry["pipeline"]["other_agents"]["requisition_extract"]
    assert extract["model_calls"] == 2 and extract["failed_calls"] == 1
    assert cases["req-02"]["latency_ms"] is None and cases["req-02"]["pipeline_latency_ms"] is not None
    assert cases["req-02"]["pipeline_cost_usd"] is not None


def test_order_review_reports_the_reconciliation_it_runs_for_new_medications(fresh_state, monkeypatch):
    """The 'new without indication' sub-metric reconciles the admission (medication_reconciliation) on 44 of the 60
    cases; in the live run $0.09 of it in the failing cases' traces alone, outside order_review's model_calls 60."""
    cases = {c["id"]: c for c in ms.load_cases("order_review")}
    monkeypatch.setattr(ms, "load_cases", lambda name: [cases["or-010"], cases["or-009"]])  # new, home medication
    entry = _run(suites.ORDER_REVIEW, fresh_state)["order_review"]
    assert entry["metrics"]["model_calls"] == 2, "the agent's own metrics: one commentary per case"

    p = entry["pipeline"]
    medrec = p["other_agents"]["medication_reconciliation"]
    assert medrec["model_calls"] >= 1 and medrec["cases"] == 1 and medrec["cost_usd"] > 0
    assert p["model_calls"] == 2 + medrec["model_calls"] and p["cases"] == 2
    results = {c["id"]: c for c in entry["case_results"]}
    assert results["or-010"]["pipeline_cost_usd"] > results["or-010"]["cost_usd"]  # a new medication: reconciled
    assert results["or-009"]["pipeline_cost_usd"] == results["or-009"]["cost_usd"]  # on the home list: not


def test_documentation_reports_the_discharge_reconciliation_once_per_suite(fresh_state, monkeypatch):
    """Three agents per stay, and a fourth (the discharge reconciliation) counted by none of them: the block is the
    suite's, the same on each entry, and the whole case's cost includes all four."""
    cases = {c["id"]: c for c in doc_evals.load_cases()}
    monkeypatch.setattr(doc_evals, "load_cases", lambda: [cases["doc-02"]])
    out = _run(suites.DOCUMENTATION, fresh_state)

    blocks = [out[a]["pipeline"] for a in suites.DOCUMENTATION.agents]
    assert blocks[0] == blocks[1] == blocks[2]
    p = blocks[0]
    assert set(p["other_agents"]) == {"medication_reconciliation"}, "the suite's own agents are not 'other'"
    own_calls = sum(out[a]["metrics"]["model_calls"] for a in suites.DOCUMENTATION.agents)
    assert p["model_calls"] == own_calls + p["other_agents"]["medication_reconciliation"]["model_calls"]
    own_cost = sum(out[a]["metrics"]["cost_per_case_usd"] for a in suites.DOCUMENTATION.agents)
    assert p["cost_per_case_usd"] == pytest.approx(
        own_cost + p["other_agents"]["medication_reconciliation"]["cost_usd"], abs=5e-6)
    (summary,) = out["discharge_summary"]["case_results"]
    assert summary["pipeline_cost_usd"] == pytest.approx(p["cost_per_case_usd"], abs=2e-6)


def test_the_run_file_the_ai_ops_page_and_the_summary_show_the_whole_case(fresh_state, monkeypatch, tmp_path, client,
                                                                          login):
    _requisitions(monkeypatch, tmp_path, ["req-00", "req-01"])
    with runs.use_folder(tmp_path / "runs") as folder:
        folder.mkdir(parents=True, exist_ok=True)
        record = evalrun.run(["requisition_triage"], store=fresh_state, platform=False, progress=lambda _m: None)
        saved = json.loads((folder / f"{record['run_id']}.json").read_text(encoding="utf-8"))
        entry = saved["agents"]["requisition_triage"]
        assert entry["pipeline"]["other_agents"]["requisition_extract"]["model_calls"] == 2
        assert all(c["pipeline_cost_usd"] > c["cost_usd"] for c in entry["case_results"])

        rows = {r["agent_id"]: r for r in client.get("/api/aiops/evals", headers=login("U-RAD")).json()["agents"]}
        assert rows["requisition_triage"]["pipeline"] == entry["pipeline"]
        case = client.get(f"/api/aiops/runs/{record['run_id']}/agents/requisition_triage/cases/req-00",
                          headers=login("U-RAD")).json()
        assert case["pipeline_cost_usd"] == entry["case_results"][0]["pipeline_cost_usd"]

        _, details, _ = suites._script("eval_summary").agent_rows()
        line = next(d for d in details if d.startswith("- `requisition_triage`"))
        assert "Whole case, every agent the suite runs: 4 model calls" in line
        assert "not in this agent's metrics: requisition_extract 2 calls" in line


def test_a_suite_whose_calls_are_all_its_own_has_no_pipeline_block(fresh_state, monkeypatch, tmp_path):
    _requisitions(monkeypatch, tmp_path, ["req-00", "req-01"])
    entry = _run(EXTRACTION, fresh_state)["requisition_extract"]
    assert "pipeline" not in entry
    assert all("pipeline_cost_usd" not in c for c in entry["case_results"])
