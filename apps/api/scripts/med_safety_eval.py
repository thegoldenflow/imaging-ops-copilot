"""Medication-safety evals (spec 7.2): the order-review commentary and the reconciliation.

    cd apps/api && uv run python scripts/med_safety_eval.py             # both, mock model
    cd apps/api && uv run python scripts/med_safety_eval.py --live      # the configured model (LLM_PROVIDER)
    add --check to exit 1 when a gate is missed; --only order_review | medrec

The cases (evals/order_review/cases.jsonl, 60; evals/medrec/cases.jsonl, 20; written by
scripts/gen_med_safety_eval.py) become synthetic patients in the database of DATABASE_URL inside one transaction
that is rolled back, and run through the real path (rules, agents through the runtime and the Tool Gateway,
guards, evidence check). Writes evals/order_review/report.json + report.md and evals/medrec/report.json + report.md.
The mock model was written by the author of the labels, so mock results are optimistic: they show the pipeline,
not a model's quality.
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.core.config  # noqa: E402,F401  (loads apps/api/.env)
from app.modules.med_safety import evals  # noqa: E402

TITLES = {"order_review": "Order review: AI commentary (spec 7.2)", "medrec": "Medication reconciliation (spec 7.2)"}


def run(live: bool, only: str | None) -> dict:
    from app.core.store import ConnectionSource, Store, get_engine, set_ambient_store
    from app.llm.gateway import LlmGateway, get_gateway, set_gateway
    from app.llm.providers import MockProvider

    if not live:
        set_gateway(LlmGateway(MockProvider(latency_s=0)))
    mode = get_gateway().mode
    conn = get_engine().connect()
    outer = conn.begin()
    store = Store(ConnectionSource(conn))
    set_ambient_store(store)
    reports = {}
    try:
        if only in (None, "order_review"):
            started = time.monotonic()
            reports["order_review"] = evals.run_order_review(store, evals.load_cases("order_review"))
            reports["order_review"]["seconds"] = round(time.monotonic() - started, 1)
        if only in (None, "medrec"):
            started = time.monotonic()
            reports["medrec"] = evals.run_medrec(store, evals.load_cases("medrec"))
            reports["medrec"]["seconds"] = round(time.monotonic() - started, 1)
    finally:
        set_ambient_store(None)
        outer.rollback()
        conn.close()
    for report in reports.values():
        report["mode"] = mode
        report["run_at"] = datetime.now().isoformat(timespec="seconds")
    return reports


def _md_order_review(r: dict) -> str:
    m = r["metrics"]
    n = m["new_without_indication"]
    lines = [f"# {TITLES['order_review']}", "",
             f"Run {r['run_at']}, model mode **{r['mode']}**, {m['cases']} labelled synthetic cases, "
             f"{r['seconds']} s. Result: **{'passed' if r['passed'] else 'FAILED'}**.", "",
             "| Metric | Value | Gate |", "| --- | --- | --- |",
             f"| Schema validity (commentary matches the output schema) | {m['schema_validity']:.2%} | 100% |",
             f"| Grounding (every evidence reference resolves in FHIR) | {m['grounding_rate']:.2%} | 100% |",
             f"| Precision of flagged concern types | {m['precision']:.3f} ({m['tp']} / {m['tp'] + m['fp']}) | >= 0.80 |",
             f"| Recall of flagged concern types | {m['recall']:.3f} ({m['tp']} / {m['tp'] + m['fn']}) | >= 0.85 |",
             f"| Latency p50 / p95 (rules + commentary, ms) | {m['latency_p50_ms']} / {m['latency_p95_ms']} | - |", "",
             "## \"New without indication\" (reported separately)", "",
             f"The {n['cases']} cases whose order is a new medication (not on the home list) were also reconciled: "
             f"the order's row is \"new\" when the order names a diagnosis on the list (rule) or the "
             f"medication_reconciliation agent finds one with enough confidence, else \"new without indication\".", "",
             "| Metric | Value |", "| --- | --- |",
             f"| Accuracy (indicated / not) | {n['accuracy']:.3f} |",
             f"| Precision of \"without indication\" | {n['precision']:.3f} ({n['flagged']} flagged) |",
             f"| Recall of \"without indication\" | {n['recall']:.3f} ({n['labelled_without_indication']} labelled) |",
             f"| Decided by | {', '.join(f'{k}: {v}' for k, v in n['decided_by'].items())} |", "",
             "## Per concern type", "", "| Type | TP | FP | FN | Precision | Recall |", "| --- | --- | --- | --- | --- | --- |"]
    lines += [f"| {t} | {v['tp']} | {v['fp']} | {v['fn']} | {v['precision']:.2f} | {v['recall']:.2f} |"
              for t, v in r["per_type"].items()]
    lines += ["", "## Misses and extra flags", "", "| Case | Title | Expected | Predicted | Missed | Extra |",
              "| --- | --- | --- | --- | --- | --- |"]
    lines += [f"| {x['id']} | {x['title']} | {', '.join(x['expected']) or '-'} | {', '.join(x['predicted']) or '-'} | "
              f"{', '.join(x['false_negative']) or '-'} | {', '.join(x['false_positive']) or '-'} |"
              for x in r["results"] if x["false_negative"] or x["false_positive"]]
    lines += ["", "## Notes", "",
              "- The cases and labels are synthetic and written by the author of the mock model "
              "(app/modules/med_safety/mocks.py), so a mock run is optimistic: it shows the pipeline (context, "
              "schema, guards, evidence resolution, the review queue), not a model's clinical quality. "
              "`scripts/med_safety_eval.py --live` runs the configured model on the same path.",
              "- Flagged concerns are compared by type per case (interaction, renal, allergy, dose, "
              "duplicate_therapy, duplicate_test, indication, monitoring, age_related, other).",
              "- The rule layer itself is covered by unit tests (tests/test_med_rules.py: at least five positive and "
              "five negative cases per rule, 100% line coverage).", ""]
    return "\n".join(lines)


def _md_medrec(r: dict) -> str:
    m = r["metrics"]
    lines = [f"# {TITLES['medrec']}", "",
             f"Run {r['run_at']}, model mode **{r['mode']}**, {m['cases']} synthetic reconciliations "
             f"(admission and discharge), {r['seconds']} s. Result: **{'passed' if r['passed'] else 'FAILED'}**.", "",
             "| Metric | Value | Gate |", "| --- | --- | --- |",
             f"| Classification accuracy (every row's discrepancy type) | {m['accuracy']:.3f} ({m['correct']} / {m['rows']}) | >= 0.90 |",
             "", "Row types: match, omission, dose_change, duplicate_therapy (rules), new, new_without_indication "
             "(the indication judged by the medication_reconciliation agent).", "",
             "## Confusion (expected -> produced)", "", "| Expected | Produced | Rows |", "| --- | --- | --- |"]
    lines += [f"| {c['expected']} | {c['predicted']} | {c['count']} |" for c in r["confusion"]]
    lines += ["", "## Cases", "", "| Case | Kind | Title | Rows | Correct | Wrong |", "| --- | --- | --- | --- | --- | --- |"]
    lines += [f"| {x['id']} | {x['kind']} | {x['title']} | {x['rows']} | {x['correct']} | "
              f"{'; '.join(f'{k}: {v['expected']} -> {v['predicted']}' for k, v in x['wrong'].items()) or '-'} |"
              for x in r["results"]]
    lines += ["", "## Notes", "",
              "- Synthetic cases and labels by the author of the mock model: optimistic; `--live` runs the configured "
              "model. Omission, dose or frequency change and duplicate therapy are rules and deterministic; only the "
              "indication of new medications depends on the model.", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--only", choices=["order_review", "medrec"])
    args = parser.parse_args()
    reports = run(args.live, args.only)
    for name, report in reports.items():
        folder = evals.EVALS / name
        (folder / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n",
                                            encoding="utf-8")
        md = _md_order_review(report) if name == "order_review" else _md_medrec(report)
        (folder / "report.md").write_text(md, encoding="utf-8")
        print(f"{name}: {'passed' if report['passed'] else 'FAILED'} {json.dumps(report['metrics'], default=str)}")
    return 1 if args.check and not all(r["passed"] for r in reports.values()) else 0


if __name__ == "__main__":
    from _stdio import utf8_stdio

    utf8_stdio()
    sys.exit(main())
