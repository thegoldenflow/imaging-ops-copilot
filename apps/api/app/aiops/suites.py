"""Eval suites (spec 6.6): which eval set evaluates which agent, and how the one runner runs it.

The existing per-module sets keep their own case files, harnesses and reports; an adapter runs the module's harness
and turns its per-case rows into the common result shape (`{id, agent_id, passed, schema_valid, grounded,
expected, got}`) and its own metrics and gates into extra columns. Each suite also offers the common case view
(`{id, agent_id, source, input_refs, expected}`) of its cases.

How an agent finds its suite (first match wins):

1. a built-in adapter below (the phase 0-4 imaging sets, WP4's de-identification, WP4c's triage set, WP5-WP7, the
   System 4 phone calls);
2. `evals/<dir>/suite.yaml` (data: `agents`, optional `run` command, `report`), a module's own runner declared
   without code;
3. `evals/<agent_id>/cases.jsonl` in the common format with prompt variables in `input`: replayed through the
   agent's registered prompt (`common_replay`);
4. the module's report found through the registry (`eval_status.run_id`, a path named in `eval_status.note`, or
   `evals/<agent_id>/report.json`): read as is.

So a new agent (WP8's) joins the Eval Center without code: through its common-format cases or its report.
Platform suites (prompt injection, durable workflows, the day simulator) are listed too; the injection set also
adds a gate to each agent it covers.
"""

from __future__ import annotations

import contextlib
import json
import re
import shlex
import subprocess
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from app.agents import evalsets
from app.aiops.collect import mark
from app.modules.evals.paths import DATASETS, EVALS_DIR

API_DIR = Path(__file__).resolve().parents[2]


@dataclass
class SuiteResult:
    rows: list[dict] = field(default_factory=list)  # per case: id, agent_id, passed, schema_valid, grounded, ...
    extra: dict[str, dict] = field(default_factory=dict)  # agent -> the module's own metrics
    gates: dict[str, dict] = field(default_factory=dict)  # agent -> {gate: {value, threshold, passed}}
    notes: str = ""
    mode: str | None = None  # set when the module's report decides it (report suites)
    run_at: str | None = None


@dataclass
class Suite:
    id: str
    agents: tuple[str, ...]
    title: str
    source: str  # where the cases live
    kind: str  # builtin, declared, common, report, platform
    run: Callable[["Context"], SuiteResult]
    cases: Callable[[], list[dict]]  # the common case view
    pipeline_eval: bool = False  # the metric does not depend on the model's judgement (6.6 release gate)
    own_transaction: bool = False  # the harness opens and rolls back its own transactions
    needs_database: bool = True


@dataclass
class Context:
    store: object  # the rolled-back store the suite runs in
    live: bool


def _gate(value, threshold, passed: bool) -> dict:
    return {"value": value, "threshold": threshold, "passed": bool(passed)}


def _lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _eval_refs(case_id: str) -> list[str]:
    """The synthetic patient an eval case writes (inside the rolled-back transaction): ids from the case id."""
    return [f"Patient/ev-{case_id}-pat", f"Encounter/ev-{case_id}-stay"]


@contextlib.contextmanager
def _patched(module, name: str, wrapper_factory) -> Iterator[None]:
    """Mark each case where a harness starts it (its case builder), without changing the harness."""
    original = getattr(module, name)
    setattr(module, name, wrapper_factory(original))
    try:
        yield
    finally:
        setattr(module, name, original)


def _marking(key: Callable[..., str]):
    def factory(original):
        def wrapper(*args, **kwargs):
            mark(key(*args, **kwargs))
            return original(*args, **kwargs)
        return wrapper
    return factory


# ---------- phase 0-4 imaging sets (app/modules/evals/run.py) ----------


def _imaging(task: str, agents: tuple[str, ...], dataset: str, title: str) -> Suite:
    def run(ctx: Context) -> SuiteResult:
        from app.llm.gateway import get_gateway
        from app.modules.evals import run as imaging

        result = imaging.TASKS[task](get_gateway())
        metrics = {k: v for k, v in result["metrics"].items() if isinstance(v, (int, float))}
        return SuiteResult(rows=[{"agent_id": agents[0], **c} for c in result["cases"]],
                           extra={a: metrics for a in agents}, notes=result.get("notes", ""))

    def cases() -> list[dict]:
        out = []
        for item in json.loads((DATASETS / f"{dataset}.json").read_text(encoding="utf-8")):
            expected = {k: item[k] for k in ("labels", "protocol_id", "categories", "sentiment", "themes",
                                              "expected_doc", "expected_fact", "expected_diseases", "intent")
                        if k in item}
            for agent in agents:
                out.append({"id": item["id"], "agent_id": agent, "source": item.get("source", "authored"),
                            "input_refs": [f"evals/datasets/{dataset}.json#{item['id']}"], "expected": expected})
        if task == "referral_summary":
            return [{"id": f"seed-{s}", "agent_id": agents[0], "source": "authored",
                     "input_refs": [f"seed:{s}"], "expected": "every number traceable to a fact"} for s in (7, 42, 2026)]
        return out

    return Suite(id=f"imaging.{task}", agents=agents, title=title, kind="builtin",
                 source=f"evals/datasets/{dataset}.json", run=run, cases=cases)


IMAGING = [
    _imaging("extraction", ("requisition_extract",), "requisitions", "Requisition extraction (System 5 intake)"),
    _imaging("triage", ("requisition_triage",), "requisitions", "Priority triage (System 5)"),
    _imaging("protocol", ("protocol_suggest",), "protocols", "Protocol assignment (System 6)"),
    _imaging("implants", ("mri_implant_extract",), "implants", "MRI implant extraction (System 8)"),
    _imaging("feedback", ("feedback_classify",), "feedback", "Patient feedback classification (System 19)"),
    _imaging("policy_qa", ("policy_qa",), "policy_qa", "Policy Q&A (System 21)"),
    _imaging("referral_summary", ("referral_weekly_summary",), "referral_summary", "Referral weekly summary (System 16)"),
    _imaging("clinical_kg", ("clinical_kg_parse", "clinical_kg_answer"), "clinical_kg", "Clinical knowledge Q&A"),
]


# ---------- de-identification (6.3) ----------


def _deid_run(ctx: Context) -> SuiteResult:
    from app.llm import deid_eval

    cases = deid_eval.load_cases()
    with _patched(deid_eval, "deidentifier_for", _marking(lambda case, *a, **k: case["id"])):
        report = deid_eval.evaluate(cases, second_pass=True)
    leaks = {x["case"] for x in report["pipeline_leaks"]}
    false_pos = {x["case"] for x in report["pipeline_false_positives"]}
    rows = [{"agent_id": "deid_check", "id": c["id"], "passed": c["id"] not in leaks and c["id"] not in false_pos,
             "got": {"leaks": sum(1 for x in report["pipeline_leaks"] if x["case"] == c["id"]),
                     "false_positives": sum(1 for x in report["pipeline_false_positives"] if x["case"] == c["id"])}}
            for c in cases]
    p = report["pipeline"]
    th = report["thresholds"]
    return SuiteResult(rows=rows, extra={"deid_check": {"recall": p["recall"], "precision": p["precision"],
                                                        "rule_layer_recall": report["rule_layer"]["recall"]}},
                       gates={"deid_check": {"recall": _gate(p["recall"], th["recall"], p["recall"] >= th["recall"]),
                                             "precision": _gate(p["precision"], th["precision"],
                                                                p["precision"] >= th["precision"])}},
                       notes="Span-level recall and precision of the whole pipeline (rule layer + second pass); a case "
                             "passes when nothing leaked and nothing was wrongly redacted.")


def _deid_cases() -> list[dict]:
    from app.llm import deid_eval

    return [{"id": c["id"], "agent_id": "deid_check", "source": "authored", "input_refs": [],
             "expected": "every planted identifier replaced", "input": {"text": c["text"][:200]}}
            for c in deid_eval.load_cases()]


DEID = Suite(id="deid", agents=("deid_check",), title="Free-text de-identification (6.3)", kind="builtin",
             source="evals/deid/cases.jsonl", run=_deid_run, cases=_deid_cases, pipeline_eval=True)


# ---------- patient message triage (WP4b / WP4c): the common format, replayed through the prompt ----------


def _triage_run(ctx: Context) -> SuiteResult:
    report = evalsets.regression("patient_message_triage", write=False)
    rows = [{"agent_id": "patient_message_triage", "id": r["id"], "source": r["source"], "passed": r["passed"],
             "schema_valid": r["status"] == "ok", "expected": r["expected"], "got": r["got"]}
            for r in report["results"]]
    return SuiteResult(rows=rows, extra={"patient_message_triage": {"accuracy": report["accuracy"]}},
                       notes="Category and urgency (after the rule layer) of synthetic patient messages; cases from "
                             "human review are appended by the low-confidence workflow.")


def common_view(agent_id: str) -> list[dict]:
    out = []
    for case in evalsets.load(agent_id):
        refs = case.get("input_refs")
        if refs is None:
            refs = sorted(set(re.findall(r'"resourceType":"(\w+)","id":"([\w.-]+)"', json.dumps(case.get("input"),
                                                                                                  separators=(",", ":")))))
            refs = [f"{t}/{i}" for t, i in refs]
        out.append({"id": case["id"], "agent_id": agent_id, "source": case.get("source", "authored"),
                    "input_refs": refs, "expected": case.get("expected")})
    return out


TRIAGE = Suite(id="patient_message_triage", agents=("patient_message_triage",),
               title="Patient message triage (WP4b reference agent)", kind="builtin",
               source="evals/patient_message_triage/cases.jsonl", run=_triage_run,
               cases=lambda: common_view("patient_message_triage"), needs_database=False)


# ---------- WP5 Control Tower narrator (+ the rule engine as an extra) ----------


def _control_tower_run(ctx: Context) -> SuiteResult:
    from app.modules.control_tower import evals as ct
    from app.modules.control_tower import service

    script = _script("control_tower_eval")
    with _patched(service, "narrate", _marking(lambda store, exc_id, *a, **k: exc_id)):
        scores, _, _ = script.collect_and_narrate(live=True)  # live=True: keep the runner's gateway (mock or live)
    rules = ct.rule_eval()
    rows = [{"agent_id": "control_tower", "id": s["id"], "passed": s["schema_valid"] and s["evidence_resolvable"]
             and s["guard_passed"], "schema_valid": s["schema_valid"], "grounded": s["evidence_resolvable"],
             "expected": {"rule": s["rule"], "severity": s["severity"]},
             "got": {"source": s.get("source"), "attempts": s.get("attempts"), "problems": s.get("problems")}}
            for s in scores]
    summary = ct.narrator_summary(scores, "runner")
    return SuiteResult(rows=rows, extra={"control_tower": {
        "guard_pass_rate": summary["guard_pass_rate"], "template_fallbacks": summary["template_fallbacks"],
        "rule_scenarios_passed": rules["passed"], "rule_scenarios": rules["cases"]}},
        gates={"control_tower": {"rule_engine": _gate(f"{rules['passed']}/{rules['cases']}", "all", rules["all_passed"]),
                                 "thirty_exceptions": _gate(len(scores), 30, len(scores) >= 30)}},
        notes="30 exceptions collected while the day simulator plays the scripted scenarios (rolled back), each "
              "narrated through the runtime: schema, evidence resolvable in FHIR, guards. The human accuracy rating "
              "(evals/control_tower/narrator/rating_sheet.md) is outside the runner.")


def _control_tower_cases() -> list[dict]:
    return [{"id": c["id"], "agent_id": "control_tower", "source": "authored", "input_refs": c.get("evidence_refs", []),
             "expected": {"rule": c["rule"], "severity": c["severity"], "facts": "numbers only from the engine"}}
            for c in _lines(EVALS_DIR / "control_tower" / "narrator" / "cases.jsonl")]


CONTROL_TOWER = Suite(id="control_tower", agents=("control_tower",), title="Control Tower narrator (7.1)",
                      kind="builtin", source="evals/control_tower/narrator/cases.jsonl", run=_control_tower_run,
                      cases=_control_tower_cases, own_transaction=True)


def _script(name: str):
    """A module eval script as a module (its functions run in this process)."""
    import importlib.util

    path = API_DIR / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_eval_script_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------- WP6 medication safety ----------


def _order_review_run(ctx: Context) -> SuiteResult:
    from app.ehr.seed import medcases
    from app.modules.med_safety import evals as ms

    with _patched(medcases, "materialize", _marking(lambda store, case, *a, **k: case["id"])):
        report = ms.run_order_review(ctx.store, ms.load_cases("order_review"))
    rows = [{"agent_id": "order_review", "id": r["id"], "passed": r["schema_valid"] and r["grounded"]
             and not r["false_positive"] and not r["false_negative"], "schema_valid": r["schema_valid"],
             "grounded": r["grounded"], "expected": r["expected"], "got": r["predicted"],
             "latency_ms": r["latency_ms"]} for r in report["results"]]
    m = report["metrics"]
    return SuiteResult(rows=rows, extra={"order_review": {k: m[k] for k in ("precision", "recall", "tp", "fp", "fn",
                                                                            "latency_p50_ms", "latency_p95_ms")}},
                       gates={"order_review": {k: _gate(m[k], report["gates"][k], m[k] >= report["gates"][k])
                                               for k in report["gates"]}},
                       notes="A case passes when the flagged concern types equal the labels (no miss, no extra) with a "
                             "valid, grounded commentary.")


def _medrec_run(ctx: Context) -> SuiteResult:
    from app.ehr.seed import medcases
    from app.modules.med_safety import evals as ms

    with _patched(medcases, "materialize", _marking(lambda store, case, *a, **k: case["id"])):
        report = ms.run_medrec(ctx.store, ms.load_cases("medrec"))
    rows = [{"agent_id": "medication_reconciliation", "id": r["id"], "passed": r["correct"] == r["rows"],
             "schema_valid": r["ai_status"] != "needs_human", "expected": f"{r['rows']} rows",
             "got": r["wrong"] or "all rows right"} for r in report["results"]]
    m = report["metrics"]
    gate = report["gates"]["accuracy"]
    return SuiteResult(rows=rows, extra={"medication_reconciliation": {"row_accuracy": m["accuracy"], "rows": m["rows"]}},
                       gates={"medication_reconciliation": {"row_accuracy": _gate(m["accuracy"], gate,
                                                                                  m["accuracy"] >= gate)}},
                       notes="A case passes when every reconciliation row has the labelled discrepancy type.")


def _ms_cases(name: str, agent: str) -> Callable[[], list[dict]]:
    def cases() -> list[dict]:
        return [{"id": c["id"], "agent_id": agent, "source": c.get("source", "authored"),
                 "input_refs": _eval_refs(c["id"]), "materialized": True, "expected": c.get("expected")}
                for c in _lines(EVALS_DIR / name / "cases.jsonl")]
    return cases


ORDER_REVIEW = Suite(id="order_review", agents=("order_review",), title="Order review commentary (7.2)",
                     kind="builtin", source="evals/order_review/cases.jsonl", run=_order_review_run,
                     cases=_ms_cases("order_review", "order_review"))
MEDREC = Suite(id="medrec", agents=("medication_reconciliation",), title="Medication reconciliation (7.2)",
               kind="builtin", source="evals/medrec/cases.jsonl", run=_medrec_run,
               cases=_ms_cases("medrec", "medication_reconciliation"))


# ---------- WP7 documentation (three agents, one set) ----------


def _documentation_run(ctx: Context) -> SuiteResult:
    from app.ehr.seed import doccases
    from app.modules.documentation import evals as doc

    with _patched(doccases, "materialize", _marking(lambda store, case, *a, **k: case["id"])):
        report = doc.run(ctx.store, doc.load_cases())

    def failed(r: dict, step: str) -> dict:  # a document the model could not draft: the reason in `got`
        return {"schema_valid": False, "passed": False,
                "got": {"status": r.get(f"{step}_status"), "error": r.get(f"{step}_error") or r.get("missing")}}

    rows = []
    for r in report["rows"]:
        expected = "every item traceable, signed"
        if r.get("summary_status") == "signed":
            rows.append({"agent_id": "discharge_summary", "id": r["id"], "schema_valid": True,
                         "grounded": not r["untraceable_after"],
                         "passed": not r["untraceable_after"] and min(r["summary_proxy"].values()) >= 4,
                         "expected": expected, "got": {"status": r["summary_status"],
                                                       "untraceable": r["untraceable_after"], "raw_red": r["raw_red"]}})
        else:
            rows.append({"agent_id": "discharge_summary", "id": r["id"], "grounded": False, "expected": expected,
                         **failed(r, "summary")})
        expected = "no red item, grade <= 8, Chinese plain"
        if r.get("instructions_status") == "drafted":
            rows.append({"agent_id": "patient_instructions", "id": r["id"], "schema_valid": True,
                         "passed": r["instructions_red"] == 0 and r["instructions_signed"] and r["fk_grade"] <= 8
                         and r["zh_no_abbreviations"] and r["zh_terms_explained"], "expected": expected, "got": {
                             "red": r["instructions_red"], "fk_grade": r["fk_grade"], "language": r.get("language")}})
        else:
            rows.append({"agent_id": "patient_instructions", "id": r["id"], "expected": expected,
                         **failed(r, "instructions")})
        expected = "no red item, proxies >= 4"
        if r.get("handoff_status") == "drafted":
            rows.append({"agent_id": "nursing_handoff", "id": r["id"], "schema_valid": True,
                         "passed": r["handoff_red"] == 0 and min(r["handoff_proxy"].values()) >= 4,
                         "expected": expected, "got": {"red": r["handoff_red"], "proxies": r["handoff_proxy"]}})
        else:
            rows.append({"agent_id": "nursing_handoff", "id": r["id"], "expected": expected, **failed(r, "handoff")})
    m, g = report["metrics"], report["gates"]

    def gates(*names):
        return {n: _gate(m.get(n, ""), "gate", g[n]) for n in names}

    return SuiteResult(rows=rows, extra={
        "discharge_summary": {k: m[k] for k in ("raw_hallucination_rate", "validator_recall",
                                                "untraceable_after_validator", "summary_seconds_p50")},
        "patient_instructions": {k: m[k] for k in ("fk_grade_mean", "zh_terms_explained", "bilingual_consistency")},
        "nursing_handoff": {"handoff_red": m["handoff_red"]}},
        gates={"discharge_summary": gates("untraceable_after_validator", "validator_recall", "schema_validity",
                                          "injected_blocked"),
               "patient_instructions": gates("bilingual_consistency"),
               "nursing_handoff": {"automated_proxies_mean_ge_4": _gate(m["automated_proxies"]["handoff"], ">= 4",
                                                                        g["handoff_proxies_mean_ge_4"])}},
        notes="30 synthetic stays through the real path: summary (traceable items, signed), bilingual instructions "
              "(readability, plain Chinese) and SBAR handoff. A document the model could not draft fails that case "
              "for its agent, with the reason (the instructions fail with the summary they are drafted from). The "
              "human rating sheet is outside the runner.")


def _documentation_cases() -> list[dict]:
    out = []
    for c in _lines(EVALS_DIR / "documentation" / "cases.jsonl"):
        for agent in ("discharge_summary", "patient_instructions", "nursing_handoff"):
            out.append({"id": c["id"], "agent_id": agent, "source": c.get("source", "authored"),
                        "input_refs": _eval_refs(c["id"]), "materialized": True, "expected": c.get("expected")})
    return out


DOCUMENTATION = Suite(id="documentation", agents=("discharge_summary", "patient_instructions", "nursing_handoff"),
                      title="Documentation agents (7.3)", kind="builtin", source="evals/documentation/cases.jsonl",
                      run=_documentation_run, cases=_documentation_cases)


# ---------- System 4 front desk: the phone agent and the call summary (one set of scripted calls) ----------


def _frontdesk_run(ctx: Context) -> SuiteResult:
    from app.modules.frontdesk import evals as fd

    report = fd.run(ctx.store, fd.load_cases())
    keep = ("agent_id", "id", "source", "passed", "schema_valid", "grounded", "latency_ms", "expected", "got")
    rows = [{k: r[k] for k in keep if k in r} for r in report["voice"] + report["summaries"]]
    return SuiteResult(rows=rows, extra=report["metrics"], gates={
        agent: {name: _gate(g["value"], g["threshold"], g["passed"]) for name, g in gates.items()}
        for agent, gates in report["gates"].items()}, notes=report["notes"])


def _frontdesk_cases() -> list[dict]:
    from app.modules.frontdesk import evals as fd

    return fd.common_cases()


FRONTDESK = Suite(id="frontdesk", agents=("voice_agent", "call_summary"), title="Front-desk phone calls (System 4)",
                  kind="builtin", source="evals/voice_agent/cases.jsonl", run=_frontdesk_run, cases=_frontdesk_cases)

BUILTIN: list[Suite] = [*IMAGING, DEID, TRIAGE, CONTROL_TOWER, ORDER_REVIEW, MEDREC, DOCUMENTATION, FRONTDESK]


# ---------- platform suites (not one agent's quality) ----------


def injection_run(store) -> dict:
    """The prompt-injection set (6.4): {passed, cases, unauthorised, rows[{id, agent, passed}]}."""
    from app.agents import injection_eval

    report = injection_eval.evaluate(store)
    return {"passed": report["passed"], "cases": report["cases"], "cases_passed": report["cases_passed"],
            "unauthorised_tool_calls": report["unauthorised_tool_calls"],
            "rows": [{"id": r["id"], "agent_id": r["agent"], "passed": r["passed"], "category": r["category"]}
                     for r in report["results"]]}


PLATFORM_REPORTS = {
    "injection": ("Prompt injection (6.4)", "evals/injection/report.json"),
    "workflows": ("Durable workflows (6.5)", "evals/workflows/report.json"),
    "simulator": ("Day simulator (6.2)", "evals/simulator/day_run.json"),
}


def platform_report(name: str) -> dict | None:
    title, path = PLATFORM_REPORTS[name]
    file = EVALS_DIR.parent / path
    if not file.exists():
        return None
    data = json.loads(file.read_text(encoding="utf-8"))
    passed = data.get("passed")
    if passed is None and "status" in data:
        passed = data["status"] == "passed"
    return {"id": name, "title": title, "source": path, "passed": passed, "run_at": data.get("run_at"),
            "summary": {k: v for k, v in data.items() if isinstance(v, (int, float, str, bool)) and k != "run_at"}}


# ---------- discovery for agents without a built-in adapter ----------


def _declared() -> list[Suite]:
    out = []
    for path in sorted(EVALS_DIR.glob("*/suite.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        agents = tuple(data.get("agents") or [])
        if not agents:
            continue
        report = path.parent / (data.get("report") or "report.json")
        out.append(_report_suite(f"declared.{path.parent.name}", agents, report, run=data.get("run"),
                                 title=data.get("title") or f"{path.parent.name} (declared suite)", kind="declared"))
    return out


def _report_suite(sid: str, agents: tuple[str, ...], report: Path, *, run: str | None = None, title: str,
                  kind: str = "report") -> Suite:
    def go(ctx: Context) -> SuiteResult:
        if run:
            subprocess.run([sys.executable, *shlex.split(run)], cwd=API_DIR, check=True)
        return read_report(report, agents)

    def cases() -> list[dict]:
        rows = _report_rows(report)
        return [{"id": r.get("id"), "agent_id": r.get("agent_id") or agents[0], "source": r.get("source", "authored"),
                 "input_refs": r.get("input_refs") or [], "expected": r.get("expected")} for r in rows]

    rel = report.relative_to(EVALS_DIR.parent).as_posix() if report.is_relative_to(EVALS_DIR.parent) else str(report)
    return Suite(id=sid, agents=agents, title=title, source=rel, kind=kind, run=go, cases=cases,
                 needs_database=False)


def _report_rows(report: Path) -> list[dict]:
    if not report.exists():
        return []
    data = json.loads(report.read_text(encoding="utf-8"))
    for key in ("results", "rows", "cases"):
        rows = data.get(key)
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            return rows
    return []


def read_report(report: Path, agents: tuple[str, ...]) -> SuiteResult:
    """A module's own report as a suite result (see the interface in the module docstring)."""
    if not report.exists():
        raise FileNotFoundError(f"{report} does not exist (run the module's eval script first)")
    data = json.loads(report.read_text(encoding="utf-8"))
    rows = []
    for r in _report_rows(report):
        if "passed" not in r or not isinstance(r["passed"], bool):
            continue
        agent = r.get("agent_id") or r.get("agent") or agents[0]
        if agent not in agents:
            continue
        rows.append({"agent_id": agent, "id": str(r.get("id")), "passed": r["passed"],
                     "schema_valid": r.get("schema_valid"), "grounded": r.get("grounded"),
                     "latency_ms": r.get("latency_ms"), "expected": r.get("expected"), "got": r.get("got"),
                     "source": r.get("source", "authored")})
    metrics = {k: v for k, v in (data.get("metrics") or {}).items() if isinstance(v, (int, float))
               and not isinstance(v, bool)}
    gates: dict = {}
    for name, value in (data.get("gates") or {}).items():
        if isinstance(value, bool):
            gates[name] = _gate(value, "gate", value)
        elif isinstance(value, dict) and "passed" in value:
            gates[name] = _gate(value.get("value"), value.get("threshold"), value["passed"])
    if isinstance(data.get("passed"), bool) and not gates:
        gates["module_verdict"] = _gate(data["passed"], True, data["passed"])
    return SuiteResult(rows=rows, extra={a: metrics for a in agents}, gates={a: gates for a in agents},
                       mode=data.get("mode"), run_at=data.get("run_at"),
                       notes=f"Read from the module's report {report.name} (its own script ran it).")


def _registry_report(spec) -> Path | None:
    """The module report an agent's registry entry points at."""
    root = EVALS_DIR.parent
    candidates = []
    if spec.eval_status.run_id and spec.eval_status.source == "registry":
        candidates.append(root / spec.eval_status.run_id.split("@")[0])
    candidates += [root / c.rstrip(".,;)") for c in re.findall(r"evals/[\w./-]+", spec.eval_status.note or "")]
    candidates.append(evalsets.folder(spec.agent_id) / "report.json")
    for path in candidates:
        if path.is_dir():
            path = path / "report.json"
        if path.suffix == ".json" and path.exists() and "/runs/" not in path.as_posix():
            return path
    return None


def common_replay(agent_id: str) -> Suite:
    """Cases in the common format with prompt variables: the agent's registered prompt, judged on `expected`."""

    def run(ctx: Context) -> SuiteResult:
        from pydantic import ConfigDict, create_model

        from app.agents import registry
        from app.agents.prompts import load as load_prompt
        from app.llm.deid import Pseudonymizer
        from app.llm.gateway import get_gateway

        spec = registry.require_agent(agent_id)
        cases = evalsets.load(agent_id)
        keys = sorted({k for c in cases for k in (c.get("expected") or {})})
        schema = create_model(f"{agent_id}_Expected", __config__=ConfigDict(extra="allow"),
                              **{k: (object, None) for k in keys})
        prompt = load_prompt(spec.prompt_version)
        rows = []
        for case in cases:
            mark(case["id"])
            outcome = get_gateway().structured(task=agent_id, prompt=prompt, variables=case.get("input") or {},
                                               schema_cls=schema, pseudonymizer=Pseudonymizer())
            got = {k: (outcome.data or {}).get(k) for k in (case.get("expected") or {})} if outcome.data else None
            rows.append({"agent_id": agent_id, "id": case["id"], "source": case.get("source", "authored"),
                         "passed": outcome.status == "ok" and got == case.get("expected"),
                         "schema_valid": outcome.status == "ok", "expected": case.get("expected"), "got": got})
        mark(None)
        return SuiteResult(rows=rows, notes="Common-format cases replayed through the registered prompt; judged on "
                                            "the expected fields.")

    return Suite(id=f"common.{agent_id}", agents=(agent_id,), title=f"{agent_id} (common-format cases)",
                 source=f"evals/{agent_id}/cases.jsonl", kind="common", run=run,
                 cases=lambda: common_view(agent_id), needs_database=False)


def suite_for(spec) -> Suite | None:
    """The suite that evaluates an agent (None: no eval set)."""
    for suite in BUILTIN:
        if spec.agent_id in suite.agents:
            return suite
    for suite in _declared():
        if spec.agent_id in suite.agents:
            return suite
    cases = evalsets.load(spec.agent_id)
    if cases and all("input" in c and "expected" in c for c in cases) and spec.prompt_version:
        return common_replay(spec.agent_id)
    report = _registry_report(spec)
    if report is not None:
        return _report_suite(f"report.{spec.agent_id}", (spec.agent_id,), report,
                             title=f"{spec.agent_id} (module report {report.parent.name}/{report.name})")
    return None
