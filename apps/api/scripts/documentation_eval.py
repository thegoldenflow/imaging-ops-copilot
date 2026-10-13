"""Documentation evals (spec 7.3): 30 synthetic stays x discharge summary, patient instructions, SBAR handoff.

    cd apps/api && uv run python scripts/documentation_eval.py            # mock model
    cd apps/api && uv run python scripts/documentation_eval.py --live     # the configured model (LLM_PROVIDER)
    add --check to exit 1 when a gate is missed

The cases (evals/documentation/cases.jsonl, written by scripts/gen_documentation_eval.py) become synthetic patients in
the database of DATABASE_URL inside one transaction that is rolled back and run through the real path (WP6's
discharge reconciliation, then the agents through the runtime and the Tool Gateway, the validators, the signing
service). Writes evals/documentation/report.json, report.md, documents.md (the 90 documents, for the human rating)
and rating_sheet.md (to be filled in by a person). The mock model was written by the author of the cases, so mock
results are optimistic: they show the pipeline, not a model's quality.
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.core.config  # noqa: E402,F401  (loads apps/api/.env)
from app.modules.documentation import evals  # noqa: E402

OUT = evals.EVALS / "documentation"
NOT_DRAFTED = "(not drafted: see the cases table in report.md)"


def run(live: bool) -> dict:
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
    started = time.monotonic()
    try:
        report = evals.run(store, evals.load_cases())
    finally:
        set_ambient_store(None)
        outer.rollback()
        conn.close()
    report.update({"mode": mode, "run_at": datetime.now().isoformat(timespec="seconds"),
                   "seconds": round(time.monotonic() - started, 1)})
    return report


def _proxy(r: dict, step: str) -> str:
    if r[f"{step}_status"] not in ("signed", "drafted"):
        return f"{r[f'{step}_status']}: {r.get(f'{step}_error') or '; '.join(r.get('missing') or [])}"
    x = r[f"{step}_proxy"]
    return f"{x['completeness']}/{x['accuracy']}/{x['readability']}/{x['format']}"


def _pct(x) -> str:
    return "n/a" if x is None else f"{x:.0%}"


def _row(r: dict) -> str:
    raw = after = draft = ""
    if r["summary_status"] in ("signed", "unsigned"):  # a draft the model produced (signed, or blocked at signing)
        raw = f"{r['raw_red']} of {r['items']}" + (f" ({', '.join(r['raw_red_codes'])})" if r["raw_red"] else "")
        draft = f"{r['summary_seconds']} s, {r['model_calls']} calls"
    if r["summary_status"] == "signed":
        after = len(r["untraceable_after"])
    return (f"| {r['id']} | {r['title']} | {raw} | {after} | {_proxy(r, 'summary')} | {_proxy(r, 'instructions')} "
            f"| {_proxy(r, 'handoff')} | {r.get('fk_grade', '')} | {draft} |")


def write(report: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    data = {k: v for k, v in report.items() if k != "documents"}
    (OUT / "report.json").write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    m, g = report["metrics"], report["gates"]
    p = m["automated_proxies"]
    lines = [
        "# Documentation agent: eval report (spec 7.3)", "",
        f"Run {report['run_at']}, model mode **{report['mode']}**, {m['cases']} synthetic stays, {report['seconds']} s. "
        f"Result: **{'passed' if report['passed'] else 'FAILED'}** (automated gates; the human rating is "
        f"outstanding: rating_sheet.md).", "",
        "The mock model was written by the author of the cases, so these numbers are optimistic: they show that the "
        "pipeline works (chunks, segment notes, synthesis, citations, validator, signing, bilingual rendering), not "
        "what a model scores. `scripts/documentation_eval.py --live` runs the configured model through the same path.",
        "", "## Gates", "", "| Gate | Value | Passed |", "| --- | --- | --- |",
        f"| Untraceable items after the validator (what is signed), gate 0 | {m['untraceable_after_validator']} | "
        f"{g['untraceable_after_validator']} |",
        f"| Validator recall on the oracle's untraceable items, gate 1.0 | {m['validator_recall']} | {g['validator_recall']} |",
        f"| Schema validity (summaries drafted and signed), gate 1.0 | {m['schema_validity']} | {g['schema_validity']} |",
        f"| Bilingual consistency on {m['bilingual_documents']} instructions (same fact items and numbers in EN, "
        f"zh-Hans, zh-Hant), gate 1.0 | {m['bilingual_consistency']} | {g['bilingual_consistency']} |",
        f"| Injected hallucinations blocked, gate 5 of 5 | {m['injected_blocked']} | {g['injected_blocked']} |",
        f"| Automated proxies of the four rating dimensions, every mean >= 4 | see below | "
        f"{g['automated_proxies_mean_ge_4']} |", "",
        "## Monitoring", "",
        f"- Raw (pre-validator) hallucination rate: **{m['raw_hallucination_rate']}** ({m['raw_untraceable_items']} "
        f"untraceable of {m['fact_items_checked']} fact items in the AI drafts; the oracle counted "
        f"{m['raw_oracle_items']}). Recorded, not a gate (spec 7.3). With the mock they all come from the three cases "
        "whose note names a medicine that was only discussed.",
        f"- Patient instructions: English Flesch-Kincaid grade mean {m['fk_grade_mean']}, max {m['fk_grade_max']} "
        f"(target 6-8; at or below 8 in {_pct(m['fk_within_or_below_8'])} of documents; below 6 counts as easier); Chinese "
        f"without abbreviations {_pct(m['zh_no_abbreviations'])}, every term explained at first use "
        f"{_pct(m['zh_terms_explained'])}; explanations from the knowledge graph {_pct(m['terms_from_knowledge_graph'])} "
        "(the rest are reviewed plain-language explanations or the medicine's class: the graph's facts are noisy).",
        f"- Validator red items left on the drafts: instructions {m['instructions_red']}, handoffs {m['handoff_red']}.",
        f"- Documents drafted: summaries drafted {m['summaries_drafted']} (signed {m['summaries_signed']}), "
        f"instructions {m['instructions_drafted']}, handoffs {m['handoffs_drafted']} of {m['cases']} cases (a document "
        "the model could not draft fails its case; a draft the validator blocked at signing is 'unsigned' and keeps "
        "its pre-validator metrics; the reason is in the cases table).",
        f"- Discharge summary drafting time (pipeline, zero-latency mock): median {m['summary_seconds_p50']} s, max "
        f"{m['summary_seconds_max']} s; model calls per summary {m['model_calls_per_summary']} (one per chunk of "
        "resources, then the synthesis). With a live model the time is about calls x per-call latency: under 30 s while "
        "a structured call takes under about 5.5 s. Not yet measured with a live model (this run used the mock).", "",
        "## Automated proxies (1-5, mean over the cases)", "",
        "| Document | Completeness | Accuracy | Readability | Format |", "| --- | --- | --- | --- | --- |",
        *[f"| {kind} | {v['completeness']} | {v['accuracy']} | {v['readability']} | {v['format']} |"
          for kind, v in p.items()], "",
        "Proxies: completeness = expected diagnoses, procedures, follow-ups, pending tests and every discharge "
        "medication present (instructions: every summary medicine and follow-up, red flags, daily care; handoff: four "
        "sections, open tasks and flags in the recommendation, vital signs in the assessment, pain and intake/output "
        "stated or declared not recorded); accuracy = the discharge medication table's categories and the expected "
        "codes (instructions: no red item; handoff: the share of statements and not-recorded declarations with no "
        "validator issue); readability = the course's grade level (instructions: grade <= 8, Chinese checks; handoff: "
        "statements of at most 45 words); format = dated, ordered course and every item cited (instructions: five "
        "blocks, identical item keys in three languages; handoff: a one-sentence situation, no statement stating an "
        "absence). The handoff's proxies each measure one thing (evals/documentation/README.md).", "",
        "## Injected hallucinations", "", "| Sample | Path | Blocked | Validator |", "| --- | --- | --- | --- |",
        *[f"| {x['sample']} | {x['path']} | {x['blocked']} | {'; '.join(x['issues'])[:180]} |" for x in report["injected"]],
        "", "## Bilingual consistency", "", "| Case | Language | Fact items | Keys match | Number mismatches |",
        "| --- | --- | --- | --- | --- |",
        *[f"| {c['id']} | {c['language']} | {c['items']} | {c['keys_match']} | {len(c['number_mismatches'])} |"
          for c in report["consistency"]], "",
        "## Cases", "",
        "| Case | Title | Raw untraceable | After validator | Summary C/A/R/F | Instructions C/A/R/F | Handoff C/A/R/F "
        "| FK grade | Draft |", "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        *[_row(r) for r in report["rows"]], "",
    ]
    (OUT / "report.md").write_text("\n".join(lines), encoding="utf-8")
    docs = ["# Documentation eval: the documents to rate", "",
            "Generated by scripts/documentation_eval.py (mock model, synthetic patients). Each case: the signed "
            "discharge summary (after the validator; red items removed as the physician must), the patient "
            "instructions in three languages, the SBAR handoff. Rate them in rating_sheet.md.", ""]
    for d in report["documents"]:
        docs += [f"## {d['id']}: {d['title']}", "", "### Discharge summary", "", "```", d["summary"] or NOT_DRAFTED,
                 "```", "", "### Patient instructions", ""]
        for lang, text in (d["instructions"] or {"all languages": NOT_DRAFTED}).items():
            docs += [f"#### {lang}", "", "```", text, "```", ""]
        docs += ["### SBAR handoff", "", "```", d["handoff"] or NOT_DRAFTED, "```", ""]
    (OUT / "documents.md").write_text("\n".join(docs), encoding="utf-8")
    sheet = ["# Documentation: human rating sheet", "",
             "The spec (7.3) asks a person to rate each of the 30 cases' three documents on four dimensions from 1 to 5 "
             "(completeness, accuracy, readability, format; gate: every dimension's mean >= 4). The documents are in "
             "documents.md, the record behind each case in cases.jsonl. The automated proxies are given for "
             "reference (they are not the rating). Fill in the C / A / R / F columns.", "",
             "| Case | Document | Proxy C/A/R/F | C | A | R | F |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for r in report["rows"]:
        for step, label in (("summary", "Discharge summary"), ("instructions", "Patient instructions"),
                            ("handoff", "SBAR handoff")):
            if r[f"{step}_status"] in ("signed", "drafted"):
                sheet.append(f"| {r['id']} | {label} | {_proxy(r, step)} |  |  |  |  |")
    sheet += ["", "Mean (fill in): C ___ A ___ R ___ F ___ per document type. Rater: ______  Date: ______", ""]
    (OUT / "rating_sheet.md").write_text("\n".join(sheet), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    report = run(args.live)
    write(report)
    m = report["metrics"]
    print(f"{m['cases']} cases, raw hallucination rate {m['raw_hallucination_rate']}, untraceable after the validator "
          f"{m['untraceable_after_validator']}, injected blocked {m['injected_blocked']}/5, bilingual "
          f"{m['bilingual_consistency']}, proxies {m['automated_proxies']}: {'passed' if report['passed'] else 'FAILED'}")
    if args.check and not report["passed"]:
        sys.exit(1)


if __name__ == "__main__":
    from _stdio import utf8_stdio

    utf8_stdio()
    main()
