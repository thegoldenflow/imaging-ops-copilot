"""AI Ops (spec 6.6): model registry, prompt registry, the one eval runner, the release gate, observability and
alerts. The prompt gate has its own file (tests/test_prompt_gate.py)."""

import json
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.agents import models, registry
from app.agents import prompts as prompt_files
from app.agents import trace as traces
from app.aiops import alerts, evalrun, observe, release, runs, suites
from app.llm.gateway import get_gateway

WEB = Path(__file__).resolve().parents[2] / "web" / "src" / "features" / "ai-ops"
NURSE = "U-NURS-05"


@pytest.fixture
def run_folder(tmp_path):
    with runs.use_folder(tmp_path / "runs") as folder:
        folder.mkdir(parents=True, exist_ok=True)
        yield folder


def _write_run(folder: Path, run_id: str, agent_id: str, *, mode="mock", task_success=0.95, gates=None,
               prompt_version=None, status="met", started="2026-10-10T10:00:00", variant=None, pipeline=False):
    spec = registry.require_agent(agent_id)
    version = prompt_version if prompt_version is not None else spec.prompt_version
    entry = {"prompt_version": version, "prompt_sha256": evalrun.prompt_hash(version), "mode": mode,
             "metrics": {"task_success": task_success, "cases": 10, "cases_passed": int(task_success * 10)},
             "gates": gates or {}, "status": status, "threshold": release.threshold(spec), "variant": variant,
             "pipeline_eval": pipeline, "case_results": []}
    record = {"run_id": run_id, "started_at": started, "mode": mode, "variant": variant, "agents": {agent_id: entry}}
    (folder / f"{run_id}.json").write_text(json.dumps(record), encoding="utf-8")
    runs.invalidate()
    return record


# ---------- model registry ----------


def test_every_agent_policy_resolves_to_a_registered_model_for_both_providers():
    for agent_id, spec in registry.agents().items():
        if not spec.is_agent:
            continue
        assert models.problems(spec) == [], agent_id
        for mode in ("anthropic", "gemini", "mock"):
            entry = models.resolve(spec, mode)
            assert entry is not None and entry.tier == spec.model_policy.tier, (agent_id, mode)
            assert entry.provider == ("gemini" if mode == "gemini" else "anthropic")
    # ids come from the environment (settings), never from the registry file
    assert models.models()["claude-reasoning"].model_id == get_gateway().model_for("reasoning")
    assert "model_id:" not in (models.MODELS_FILE.read_text(encoding="utf-8").replace("model_id_env", ""))


def test_a_policy_outside_the_model_registry_is_refused():
    spec = registry.require_agent("order_review")  # clinical_ds
    voice = spec.model_copy(update={"model_policy": spec.model_policy.model_copy(update={"tier": "voice"})})
    assert any("not approved for the clinical_ds tier" in p for p in models.problems(voice))
    pinned = spec.model_copy(update={"model_policy": spec.model_policy.model_copy(update={"model": "gpt-unknown"})})
    assert models.problems(pinned) == ["order_review: model 'gpt-unknown' is not in config/models.yaml"]
    # the registry refuses such an entry at load
    with pytest.raises(registry.RegistryError):
        registry._cross_check({"order_review": voice}, registry.tools())
    # the LLM gateway refuses a call whose policy names no registered model (outcome unavailable, logged)
    from app.agents.library.patient_message_triage import TriageOutput

    with registry.temporary("aiops_probe", {"kind": "embedded_agent", "prompt_version": None,
                                            "model_policy": {"tier": "fast", "max_tokens": 100,
                                                             "model": "gpt-unknown"}}):
        out = get_gateway().structured(task="aiops_probe", prompt=prompt_files.load("patient_message_triage@1"),
                                       variables={"context": "[]", "channel": "portal", "rule_flags": "none",
                                                  "message": "hello"}, schema_cls=TriageOutput)
    assert out.status == "unavailable" and "config/models.yaml" in out.error


def test_a_pinned_registered_model_is_used_for_the_call():
    spec = registry.require_agent("requisition_triage")
    pinned = spec.model_copy(update={"model_policy": spec.model_policy.model_copy(update={"model": "claude-fast"})})
    assert get_gateway().model_for_agent(pinned) == models.models()["claude-fast"].model_id
    assert get_gateway().model_for_agent(spec) == models.models()["claude-reasoning"].model_id


# ---------- prompt registry ----------


def test_every_agent_prompt_is_a_file_with_the_four_header_lines():
    for agent_id, spec in registry.agents().items():
        if not spec.prompt_version:
            continue
        header = prompt_files.header(spec.prompt_version)
        assert tuple(header) == prompt_files.HEADER, agent_id
        assert prompt_files.load(spec.prompt_version).version == spec.prompt_version
    # no Prompt constants left in module code: the embedded agents hold pointer-following prompts
    from app.modules.triage.service import TRIAGE_PROMPT

    assert isinstance(TRIAGE_PROMPT, prompt_files.RegisteredPrompt)
    for path in (Path(__file__).resolve().parents[1] / "app").rglob("*.py"):
        assert not re.search(r"^\w+ = Prompt\(", path.read_text(encoding="utf-8"), re.M), path


def test_moving_the_registry_pointer_switches_and_rolls_back_an_embedded_prompt_without_a_restart(tmp_path,
                                                                                                    monkeypatch):
    from app.modules.triage.service import TRIAGE_PROMPT

    folder = tmp_path / "prompts"
    shutil.copytree(prompt_files.PROMPTS_DIR, folder)
    v1 = (folder / "requisition_triage" / "1.md").read_text(encoding="utf-8")
    (folder / "requisition_triage" / "2.md").write_text(v1.replace("A radiologist reviews every decision.",
                                                                    "A radiologist reviews every decision (v2)."),
                                                         encoding="utf-8")
    monkeypatch.setattr(prompt_files, "PROMPTS_DIR", folder)
    prompt_files._parse.cache_clear()
    try:
        assert TRIAGE_PROMPT.version == "requisition_triage@1"
        spec = registry.require_agent("requisition_triage")
        with registry.temporary("requisition_triage", spec.model_copy(update={"prompt_version": "requisition_triage@2"})):
            assert TRIAGE_PROMPT.version == "requisition_triage@2" and TRIAGE_PROMPT.system.endswith("(v2).")
        assert TRIAGE_PROMPT.version == "requisition_triage@1"  # rolled back: the pointer moved back
    finally:
        prompt_files._parse.cache_clear()


# ---------- the runner ----------


def test_the_runner_evaluates_an_agent_and_writes_a_run_file_with_the_six_metrics(fresh_state, run_folder):
    record = evalrun.run(["patient_message_triage"], store=fresh_state, platform=False, progress=lambda _m: None)
    path = run_folder / f"{record['run_id']}.json"
    assert path.exists()
    entry = json.loads(path.read_text(encoding="utf-8"))["agents"]["patient_message_triage"]
    m = entry["metrics"]
    for key in ("schema_validity", "grounding_rate", "task_success", "human_override_rate", "latency_p50_ms",
                "latency_p95_ms", "cost_per_case_usd"):
        assert key in m
    assert m["cases"] == len(suites.TRIAGE.cases()) and m["task_success"] == round(m["cases_passed"] / m["cases"], 4)
    assert m["cost_per_case_usd"] > 0 and m["cost_estimated"] is True  # mock calls: list-price estimate
    assert entry["prompt_version"] == "patient_message_triage@1" and entry["model_key"] == "claude-fast"
    assert entry["prompt_sha256"] == evalrun.prompt_hash("patient_message_triage@1")
    assert entry["mode"] == "mock" and record["baseline"] is True
    # a failing case carries the trace captured before the rollback
    failing = [c for c in entry["case_results"] if not c["passed"]]
    assert failing and failing[0]["traces"][0]["agent_id"] == "patient_message_triage"
    # the release gate follows: a mock baseline that met the threshold stays pending, with this run's id
    spec = registry.agent("patient_message_triage")
    assert spec.eval_status.source == "run" and spec.eval_status.run_id == f"evals/runs/{record['run_id']}.json"
    assert spec.eval_status.status == ("pending" if entry["met"] else "failed")


def test_a_candidate_prompt_run_is_a_variant_and_the_comparison_shows_per_case_differences(fresh_state, run_folder,
                                                                                         client, login):
    base = evalrun.run(["patient_message_triage"], store=fresh_state, platform=False, progress=lambda _m: None)
    cand = evalrun.run(prompt="patient_message_triage@2", store=fresh_state, platform=False, progress=lambda _m: None)
    assert cand["variant"] == {"agent": "patient_message_triage", "prompt_version": "patient_message_triage@2"}
    # a variant never changes the release status of the version in use
    assert registry.agent("patient_message_triage").eval_status.run_id == f"evals/runs/{base['run_id']}.json"
    res = client.get("/api/aiops/compare", params={"agent": "patient_message_triage", "a": base["run_id"],
                                                    "b": cand["run_id"]}, headers=login("U-ADMIN"))
    assert res.status_code == 200
    body = res.json()
    assert body["a"]["prompt_version"] == "patient_message_triage@1"
    assert body["b"]["prompt_version"] == "patient_message_triage@2"
    assert set(body["a"]["metrics"]) == set(body["metrics_order"])
    fall = next(d for d in body["differences"] if d["id"] == "authored-fall")
    assert fall["change"] == "regressed" and fall["a"]["passed"] and not fall["b"]["passed"]
    options = client.get("/api/aiops/compare/options/patient_message_triage", headers=login("U-ADMIN")).json()
    assert {r["run_id"] for r in options["runs"]} >= {base["run_id"], cand["run_id"]}


def test_agents_without_an_adapter_are_found_through_common_cases_or_their_module_report(tmp_path):
    """WP8's agents join without code: the generic replay of common-format cases, or the module's report."""
    from app.agents import evalsets

    with evalsets.use_root(tmp_path):
        folder = tmp_path / "prep_translation"
        folder.mkdir()
        spec = registry.require_agent("prep_translation")
        assert suites.suite_for(spec) is None  # no eval set yet
        (folder / "report.json").write_text(json.dumps({
            "passed": True, "mode": "mock", "metrics": {"intent_accuracy": 0.9},
            "results": [{"id": "c1", "passed": True},
                        {"id": "c2", "passed": False, "agent_id": "prep_translation"}]}))
        found = suites.suite_for(spec)
        assert found is not None and found.kind == "report"
        result = found.run(suites.Context(store=None, live=False))
        assert [r["passed"] for r in result.rows] == [True, False]
        assert result.extra["prep_translation"] == {"intent_accuracy": 0.9}
        (folder / "cases.jsonl").write_text(json.dumps({"id": "x", "source": "authored", "input": {
            "language": "fr", "text": "t"}, "expected": {"text": "s"}}) + "\n")
        assert suites.suite_for(spec).kind == "common"


# ---------- the release gate ----------


def test_eval_status_comes_from_the_latest_run_against_the_threshold(fresh_state, run_folder):
    agent = "requisition_triage"
    threshold = release.threshold(registry.require_agent(agent))
    _write_run(run_folder, "run-2026-10-10T100000", agent, task_success=threshold + 0.05)
    assert registry.agent(agent).eval_status.status == "pending"  # mock baseline: never passed
    _write_run(run_folder, "run-2026-10-10T110000", agent, mode="anthropic", task_success=threshold + 0.05,
               started="2026-10-10T11:00:00")
    status = registry.agent(agent).eval_status
    assert status.status == "passed" and status.source == "run" and status.mode == "anthropic"
    assert registry.agent(agent).evaluated is True
    _write_run(run_folder, "run-2026-10-10T120000", agent, mode="anthropic", task_success=threshold - 0.1,
               started="2026-10-10T12:00:00", status="below")
    failed = registry.agent(agent)
    assert failed.eval_status.status == "failed" and "Below the bar" in failed.eval_status.note
    # demo: watermark (not evaluated); prod: refused
    assert failed.evaluated is False and registry.gate(failed, "demo") is None
    assert "has not passed" in registry.gate(failed, "prod")
    # a module gate failing also fails the agent, whatever the pass rate
    _write_run(run_folder, "run-2026-10-10T130000", agent, mode="anthropic", task_success=1.0,
               gates={"recall": {"value": 0.5, "threshold": 0.8, "passed": False}}, started="2026-10-10T13:00:00")
    assert registry.agent(agent).eval_status.status == "failed"


def test_the_runtime_refuses_a_failed_agent_in_prod_and_watermarks_it_in_demo(fresh_state, run_folder):
    from app.modules.triage import service as triage

    _write_run(run_folder, "run-2026-10-10T100000", "requisition_triage", mode="anthropic", task_success=0.1,
               status="below")
    variables = {"fields": "{}"}
    demo = get_gateway().structured(task="requisition_triage", prompt=triage.TRIAGE_PROMPT, variables=variables,
                                    schema_cls=triage.TriageOutput)
    assert demo.status == "ok" and demo.evaluated is False and demo.eval_status == "failed"
    with registry.mode("prod"):
        prod = get_gateway().structured(task="requisition_triage", prompt=triage.TRIAGE_PROMPT, variables=variables,
                                        schema_cls=triage.TriageOutput)
    assert prod.status == "unavailable" and "has not passed" in prod.error


def test_pipeline_evals_pass_on_a_mock_run_and_variants_never_count(fresh_state, run_folder):
    _write_run(run_folder, "run-2026-10-10T100000", "deid_check", task_success=0.95, pipeline=True)
    assert registry.agent("deid_check").eval_status.status == "passed"
    _write_run(run_folder, "run-2026-10-10T110000", "deid_check", task_success=0.1, status="below",
               variant={"model": "claude-reasoning"}, started="2026-10-10T11:00:00")
    assert registry.agent("deid_check").eval_status.status == "passed"


# ---------- observability ----------


def _trace(run_id: str, agent: str, *, mode="anthropic", cost=0.0, outcome="completed", tool_status=None,
           decision=None, minutes_ago=5, encounter=None):
    now = datetime.now() - timedelta(minutes=minutes_ago)
    t = traces.AgentTrace(run_id=run_id, agent_id=agent, agent_version="1.0.0", kind="run", mode=mode,
                          model="claude-sonnet-5-5", prompt_version=f"{agent}@1", eval_status="pending",
                          evaluated=False, encounter_id=encounter, outcome=outcome, started_at=now,
                          finished_at=now + timedelta(milliseconds=800))
    t.add_llm_call(traces.LlmCallRecord(call_id=f"LLM-{run_id}", agent_id=agent, model="claude-sonnet-5-5",
                                        prompt_version=f"{agent}@1", status="ok", tokens_in=1000, tokens_out=200,
                                        cost_usd=cost, latency_ms=700))
    if tool_status:
        t.tool_calls.append(traces.ToolCallRecord(tool_id="getEncounterBundle", side_effect_level="read",
                                                  args_hash="x", status=tool_status, at=now))
    if decision:
        t.human_action = {"role": "nurse", "decision": decision}
    traces.save(t)
    return t


def test_overview_aggregates_traces_by_agent_model_unit_and_day(fresh_state, client, login):
    _trace("RUN-T1", "order_review", cost=0.02, decision="accept")
    _trace("RUN-T2", "order_review", cost=0.03, decision="reject", tool_status="failed")
    _trace("RUN-T3", "control_tower", mode="mock", outcome="needs_human")
    view = observe.overview(fresh_state, days=1)
    row = next(r for r in view["by_agent"] if r["agent_id"] == "order_review")
    assert row["requests"] >= 2 and row["cost_usd"] >= 0.05 and row["tool_failures"] >= 1
    assert row["retrieval_failures"] >= 1 and row["adoption_rate"] is not None
    mock_row = next(r for r in view["by_agent"] if r["agent_id"] == "control_tower")
    assert mock_row["cost_estimated"] is True and mock_row["cost_usd"] > 0  # list price for mock calls
    assert {r["model"] for r in view["by_model"]} >= {"claude-sonnet-5-5"}
    assert any(r["day"] == datetime.now().date().isoformat() for r in view["by_day"])
    assert {"run_id", "agent_id", "failures"} <= set(view["failures"][0])
    assert any(f["run_id"] == "RUN-T2" for f in view["failures"])
    # the page's API: six cards, the agent table, the failures; ops roles only
    res = client.get("/api/aiops/overview", headers=login("U-OPS"))
    assert res.status_code == 200
    body = res.json()
    assert len(body["cards"]) == 6 and {c["key"] for c in body["cards"]} >= {"cost_usd", "human_override_rate"}
    agents = {a["agent_id"]: a for a in body["agents"]}
    assert "order_review" in agents and "registration" not in agents  # modules have no model
    assert {"version", "eval_status", "pass_rate", "override_rate", "latency_p95_ms", "cost_usd"} <= set(
        agents["order_review"])
    assert client.get("/api/aiops/overview", headers=login(NURSE)).status_code == 403
    # a failure reaches its trace: the list names the run, the trace endpoint returns it
    detail = client.get("/api/aiops/traces/RUN-T2", headers=login("U-ADMIN")).json()
    assert detail["trace"]["run_id"] == "RUN-T2" and "1 tool call(s) failed" in detail["record"]["failures"]


def test_eval_center_and_registry_views(client, login):
    body = client.get("/api/aiops/evals", headers=login("U-RAD")).json()
    rows = {r["agent_id"]: r for r in body["agents"]}
    assert rows["order_review"]["suite"] == "order_review" and rows["order_review"]["cases_by_source"]["authored"] == 60
    assert rows["cxr_draft"]["suite"] is None
    cases = client.get("/api/aiops/evals/order_review/cases", headers=login("U-RAD")).json()["cases"]
    assert {"id", "source", "input_refs", "expected"} <= set(cases[0])
    reg = client.get("/api/aiops/registry", headers=login("U-ADMIN")).json()
    assert {m["name"] for m in reg["models"]} >= {"claude-reasoning", "claude-fast", "claude-voice"}
    triage = next(p for p in reg["prompts"] if p["version"] == "patient_message_triage@1")
    assert triage["active"] and set(triage["header"]) == set(prompt_files.HEADER)


# ---------- alerts ----------


def test_a_cost_alert_opens_one_admin_task_per_day_and_an_admin_resolves_it(fresh_state, client, login):
    threshold = next(a.threshold for a in alerts.alerts() if a.id == "agent_daily_cost")
    _trace("RUN-A1", "order_review", cost=threshold * 2)
    first = alerts.check(fresh_state)
    hit = next(b for b in first["breaches"] if b["alert_id"] == "agent_daily_cost" and b["scope"] == "order_review")
    assert hit["task"] in first["opened"]
    task = next(t for t in alerts.open_tasks() if t["id"] == hit["task"])
    assert task["status"] == "requested" and task["scope"] == "order_review" and "AI Ops alert" in task["description"]
    assert float(task["value"]) >= threshold * 2
    # the Task was written through the Tool Gateway as the ai_ops module (traced)
    again = alerts.check(fresh_state)
    assert hit["task"] not in again["opened"]  # one Task per alert, scope and day
    assert client.post(f"/api/aiops/alerts/{hit['task']}/resolve", json={}, headers=login("U-OPS")).status_code == 403
    res = client.post(f"/api/aiops/alerts/{hit['task']}/resolve", json={"note": "budget raised"},
                      headers=login("U-ADMIN"))
    assert res.status_code == 200
    done = next(t for t in alerts.open_tasks() if t["id"] == hit["task"])
    assert done["status"] == "completed" and any("Resolved by" in n for n in done["notes"])


# ---------- no hardcoded numbers on the page ----------

ALLOWED_NUMBERS = {"0", "1", "2", "3", "4", "7", "30", "100", "1000", "60"}


def test_the_ai_ops_page_hardcodes_no_numbers():
    """6.6: every number on the page comes from trace aggregates or run files. Numeric literals in the page's
    source are limited to formatting (percent, rounding, ms to s), the window choices and layout."""
    files = sorted(WEB.glob("*.tsx")) + sorted(WEB.glob("*.ts"))
    assert files, "the AI Ops page sources are missing"
    found = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        text = re.sub(r"/\*.*?\*/|//[^\n]*", "", text, flags=re.S)  # comments
        text = re.sub(r'className="[^"]*"|className=\{`[^`]*`\}', "", text)  # Tailwind classes (sizes, spacing)
        text = re.sub(r'data-testid="[^"]*"', "", text)
        for m in re.finditer(r"(?<![\w.#-])(\d+(?:\.\d+)?)(?![\w%])", text):
            if m.group(1) not in ALLOWED_NUMBERS:
                found.append(f"{path.name}: {m.group(1)} in …{text[max(0, m.start() - 30):m.end() + 10]!r}")
    assert not found, "hardcoded numbers on the AI Ops page:\n" + "\n".join(found)
