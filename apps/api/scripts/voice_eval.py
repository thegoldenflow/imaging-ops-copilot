"""Voice-services evals (spec 7.4): call centre, follow-up calls, bilingual.

    cd apps/api && uv run python scripts/voice_eval.py            # mock model
    cd apps/api && uv run python scripts/voice_eval.py --live     # the configured model (LLM_PROVIDER)
    add --check to exit 1 when a gate is missed

The cases (evals/call_center, evals/followup, evals/voice_bilingual: cases.jsonl, written by
scripts/gen_voice_eval.py) run against the seeded hospital in the database of DATABASE_URL, inside one transaction
that is rolled back (each case in its own savepoint), through the real path: the call-centre dialog and the
follow-up call engine, the agents through the runtime and the Tool Gateway, the rules, the write-back. Writes
report.json and report.md in each folder. The mock models were written by the author of the scripts, so mock
results are optimistic: they show the pipeline, not a model's quality.
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.core.config  # noqa: E402,F401  (loads apps/api/.env)
from app.modules.voice_services import evals  # noqa: E402

NOTE = ("Synthetic scripts written alongside the mock models: with the mock the numbers show that the pipeline works "
        "(dialog, identity check, rules, Tool Gateway, write-back), not a model's quality. Run with --live on the "
        "configured model before trusting them.")


def run(live: bool) -> dict:
    import app.main  # noqa: F401  (every module and mock fixture registered)
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
    out = {}
    try:
        for name, fn in (("call_center", evals.run_call_center), ("followup", evals.run_followups),
                         ("voice_bilingual", evals.run_bilingual)):
            started = time.monotonic()
            out[name] = evals.stamp(fn(store, evals.load(name)), mode, started)
    finally:
        set_ambient_store(None)
        outer.rollback()
        conn.close()
    return out


def call_center_md(r: dict) -> list[str]:
    rows = [f"| {x['id']} | {x['language']} | {x['category']} | {x['label']} | {x['predicted']} | "
            f"{'yes' if x['correct'] else 'NO'} | {x['outcome']} | {', '.join(x['actions']) or '-'} |"
            for x in r["results"]]
    return ["# Call-centre eval (spec 7.4)", "",
            f"Run {r['run_at']} ({r['mode']} model): **{'passed' if r['passed'] else 'FAILED'}**. {r['cases']} scripts; "
            f"intent accuracy **{r['intent_accuracy']}** (gate {r['intent_gate']}); identity-failure scripts put "
            f"through to a person **{r['identity_failures_transferred']}** of {r['identity_failure_cases']} "
            f"(gate {r['identity_gate']}). Server time per caller turn: p50 {r['latency_ms']['p50']} ms, p95 "
            f"{r['latency_ms']['p95']} ms (text only; speech runs in the browser).", "", NOTE, "",
            "## By intent", "", "| Intent | Scripts | Correct |", "| --- | --- | --- |",
            *[f"| {k} | {v['cases']} | {v['correct']} |" for k, v in sorted(r["by_intent"].items())], "",
            "## By language", "", "| Language | Scripts | Correct |", "| --- | --- | --- |",
            *[f"| {k} | {v['cases']} | {v['correct']} |" for k, v in sorted(r["by_language"].items())], "",
            "## Scripts", "", "| Script | Lang | Kind | Label | Recognised | Correct | Outcome | Actions |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |", *rows]


def followup_md(r: dict) -> list[str]:
    rows = [f"| {x['id']} | {x['patient']} | {'yes' if x['red_flag'] else 'no'} | {x['note']} | "
            f"{'yes' if x['escalated'] else 'no'} | {x['escalated_by'] or '-'} | {x['priority'] or '-'} | "
            f"{', '.join(x['rule_codes']) or '-'} | {x['classifier'].get('status')} "
            f"{x['classifier'].get('confidence') if x['classifier'].get('confidence') is not None else ''} |"
            for x in r["results"]]
    o = r["over_escalation"]
    return ["# Follow-up call eval (spec 7.4)", "",
            f"Run {r['run_at']} ({r['mode']} model): **{'passed' if r['passed'] else 'FAILED'}**. {r['cases']} call "
            f"scripts, {r['red_flag_cases']} with a red flag. Red-flag recall **{r['recall']}** (gate "
            f"{r['recall_gate']}). With the model down, the rules alone escalated "
            f"**{r['rules_only']['escalated_by_rules']} of {r['rules_only']['cases']}** red-flag scripts (7.4: all, "
            f"without the model); rule codes as labelled: {r['rule_codes_as_labelled']}. Rule hits on scripts without a "
            f"red flag: {', '.join(r['false_rule_hits']) or 'none'}.", "",
            f"Over-escalation (recorded, not gated: a nurse calling back without need is the accepted cost): "
            f"{o['escalated']} of {o['cases']} scripts without a red flag ({o['rate']}), by "
            f"{', '.join(f'{k} {v}' for k, v in o['by'].items()) or '-'}.", "",
            f"Write-back: {r['write_back']['communications']} call records, {r['write_back']['with_observations']} "
            f"calls with patient-reported Observations, {r['write_back']['tasks']} nurse Tasks.", "", NOTE, "",
            "## Scripts", "", "| Script | Patient | Red flag | What | Escalated | By | Priority | Rule codes | "
                              "Classifier |", "| --- | --- | --- | --- | --- | --- | --- | --- | --- |", *rows]


def bilingual_md(r: dict) -> list[str]:
    rows = [f"| {x['id']} | {x['language']} | {'yes' if x['outcome_ok'] else 'NO'} | "
            f"{'yes' if x['language_ok'] else 'NO'} | {'yes' if x['write_back'] else 'NO'} | {x['turns']} |"
            for x in r["results"]]
    return ["# Bilingual voice eval (spec 7.4)", "",
            f"Run {r['run_at']} ({r['mode']} model): **{'passed' if r['passed'] else 'FAILED'}**. {r['cases']} scripts "
            f"({r['by_language']['en']} English, {r['by_language']['zh']} Chinese) through the whole flow: expected "
            f"outcome {r['outcome_ok']}, every agent sentence in the caller's language {r['language_ok']}, write-back "
            f"{r['write_back_ok']}; the same outcome in both languages for {r['pairs_consistent']} scenarios.", "",
            NOTE, "", "| Script | Lang | Outcome | Language | Write-back | Agent turns |",
            "| --- | --- | --- | --- | --- | --- |", *rows]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    reports = run(args.live)
    for name, lines in (("call_center", call_center_md), ("followup", followup_md),
                        ("voice_bilingual", bilingual_md)):
        evals.write(reports[name], evals.EVALS / name, lines(reports[name]))
        print(f"{name}: {'passed' if reports[name]['passed'] else 'FAILED'}")
    if args.check and not all(r["passed"] for r in reports.values()):
        sys.exit(1)


if __name__ == "__main__":
    from _stdio import utf8_stdio

    utf8_stdio()
    main()
