"""Eval sets that grow from human review (spec 6.5 LowConfidenceReviewWorkflow; 6.6 runs them all).

`evals/<agent_id>/cases.jsonl` holds an agent's cases: the prompt variables exactly as the model got them
(de-identified: placeholders such as [PERSON_1], never names) and the expected output. Cases come from two
sources: `authored` (written with the agent) and `human_review` (a person confirmed or corrected a low-confidence
output; appended by the workflow). `regression(agent_id)` sends every case through the agent's current prompt and
the LLM gateway and compares the fields the agent is judged on; the report goes to `evals/<agent_id>/report.json`
and `report.md`. With the mock provider the run shows the pipeline; `--live` scripts run the configured model.

So far one agent learns this way: `patient_message_triage` (the only agent with a confidence and a registry
threshold). The 7.1–7.4 agents join when their modules set a `confidence_threshold`.
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path

from app.modules.evals.paths import EVALS_DIR

CASES = "cases.jsonl"
GATE = 0.80  # share of cases whose judged fields match
_root: list[Path] = []


def root() -> Path:
    """evals/ (EVALS_DIR); AGENT_EVALS_DIR moves only these agent eval sets (e.g. an end-to-end run that must not
    append to the repository's files)."""
    if _root:
        return _root[-1]
    return Path(os.environ["AGENT_EVALS_DIR"]) if os.environ.get("AGENT_EVALS_DIR") else EVALS_DIR


@contextlib.contextmanager
def use_root(path: Path) -> Iterator[Path]:
    """Tests and scratch runs: read and write the eval sets under another folder."""
    _root.append(Path(path))
    try:
        yield Path(path)
    finally:
        _root.pop()


def folder(agent_id: str) -> Path:
    return root() / agent_id


def cases_path(agent_id: str) -> Path:
    return folder(agent_id) / CASES


def load(agent_id: str) -> list[dict]:
    path = cases_path(agent_id)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def append_case(agent_id: str, case: dict) -> bool:
    """Append a case unless one with its id is there already (an activity Temporal retries appends once)."""
    if any(c.get("id") == case["id"] for c in load(agent_id)):
        return False
    path = cases_path(agent_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(case, ensure_ascii=False, sort_keys=True) + "\n")
    return True


# ---------- per agent: what a reviewer may correct, and how a case is judged ----------


def _triage_judge(case: dict, output: dict) -> dict:
    """patient_message_triage is judged on category and urgency (after the rule layer, as the agent does)."""
    flags = (case["input"].get("rule_flags") or "none") != "none"
    got = {"category": output.get("category"), "urgency": "urgent" if flags else output.get("urgency")}
    want = {k: case["expected"].get(k) for k in ("category", "urgency")}
    return {"got": got, "expected": want, "passed": got == want}


def _triage_schema():
    from app.agents.library.patient_message_triage import TriageOutput

    return TriageOutput


AGENTS: dict[str, dict] = {
    "patient_message_triage": {
        "correctable": {"category": ["clinical", "scheduling", "administrative", "other"],
                        "urgency": ["routine", "soon", "urgent"], "summary": []},
        "judged": ("category", "urgency"),
        "judge": _triage_judge,
        "schema": _triage_schema,
    },
}


def supported(agent_id: str) -> bool:
    return agent_id in AGENTS


def correctable(agent_id: str) -> dict[str, list[str]]:
    """Field -> allowed values ([] = free text) a reviewer may correct."""
    return AGENTS.get(agent_id, {}).get("correctable", {})


def case_from_review(review) -> dict:
    """An eval case from a reviewed low-confidence output (app/agents/reviews.py)."""
    from app.agents.reviews import corrected_output

    judged = AGENTS[review.agent_id]["judged"]
    expected = corrected_output(review)
    return {"id": f"hr-{review.run_id.lower()}", "source": "human_review", "agent_id": review.agent_id,
            "agent_version": review.agent_version, "prompt_version": review.prompt_version,
            "created": datetime.now().isoformat(timespec="seconds"), "input": review.input,
            "expected": {k: expected.get(k) for k in judged},
            "model_output": {k: review.output.get(k) for k in judged},
            "corrected": sorted((review.correction or {}).keys()), "reviewer_role": review.reviewer_role,
            "confidence": review.confidence, "threshold": review.threshold}


# ---------- the regression eval ----------


def regression(agent_id: str, *, heartbeat: Callable[[dict], None] | None = None, write: bool = True) -> dict:
    """Every case through the agent's current prompt (LLM gateway, mock or live), judged field by field."""
    from app.agents import registry
    from app.agents.prompts import load as load_prompt
    from app.llm.deid import Pseudonymizer
    from app.llm.gateway import get_gateway

    if agent_id not in AGENTS:
        raise LookupError(f"No eval set for {agent_id}")
    spec = registry.require_agent(agent_id)
    prompt = load_prompt(spec.prompt_version)
    schema, judge = AGENTS[agent_id]["schema"](), AGENTS[agent_id]["judge"]
    started = datetime.now()
    results = []
    cases = load(agent_id)
    for i, case in enumerate(cases, 1):
        outcome = get_gateway().structured(task=agent_id, prompt=prompt, variables=case["input"], schema_cls=schema,
                                           pseudonymizer=Pseudonymizer())
        if outcome.status == "ok":
            verdict = judge(case, outcome.data)
        else:
            verdict = {"got": None, "expected": case.get("expected"), "passed": False}
        results.append({"id": case["id"], "source": case.get("source"), "status": outcome.status, **verdict})
        if heartbeat:
            heartbeat({"done": i, "of": len(cases)})
    passed = sum(1 for r in results if r["passed"])
    by_source: dict[str, dict] = {}
    for r in results:
        s = by_source.setdefault(r["source"] or "authored", {"cases": 0, "passed": 0})
        s["cases"] += 1
        s["passed"] += int(r["passed"])
    accuracy = round(passed / len(results), 4) if results else None
    report = {"agent_id": agent_id, "agent_version": spec.version, "prompt_version": spec.prompt_version,
              "mode": get_gateway().mode, "run_at": started.isoformat(timespec="seconds"),
              "cases": len(results), "passed": passed, "accuracy": accuracy, "gate": GATE,
              "status": "passed" if accuracy is not None and accuracy >= GATE else "failed",
              "by_source": by_source, "results": results}
    if write:
        _write(agent_id, report)
    return report


def _write(agent_id: str, report: dict) -> None:
    out = folder(agent_id)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
                                     newline="\n")
    lines = [f"# Regression eval: {agent_id}", "",
             f"Run {report['run_at']} · agent {report['agent_version']} · prompt {report['prompt_version']} · "
             f"model mode {report['mode']}", "",
             f"**{report['status']}**: {report['passed']} of {report['cases']} cases match "
             f"(accuracy {report['accuracy']}, gate {report['gate']}).", "",
             "| Source | Cases | Passed |", "| --- | --- | --- |"]
    lines += [f"| {k} | {v['cases']} | {v['passed']} |" for k, v in sorted(report["by_source"].items())]
    lines += ["", "| Case | Source | Expected | Got | |", "| --- | --- | --- | --- | --- |"]
    for r in report["results"]:
        want = ", ".join(f"{k} {v}" for k, v in (r.get("expected") or {}).items())
        got = ", ".join(f"{k} {v}" for k, v in (r.get("got") or {}).items()) if r.get("got") else r["status"]
        lines.append(f"| {r['id']} | {r['source']} | {want} | {got} | {'pass' if r['passed'] else 'FAIL'} |")
    lines += ["", "Judged fields: " + ", ".join(AGENTS[agent_id]["judged"]) + ". Cases from human review are "
              "appended by the LowConfidenceReviewWorkflow (source human_review); a mismatch on one of them means "
              "the agent still disagrees with the person who corrected it. With the mock provider the result shows "
              "the pipeline, not a model's quality.", ""]
    (out / "report.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")
