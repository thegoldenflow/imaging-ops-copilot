"""Control Tower evals (spec 7.1): the rule engine and the narrator.

    cd apps/api && uv run python scripts/control_tower_eval.py            # both, mock model
    cd apps/api && uv run python scripts/control_tower_eval.py --rules    # the rule engine only (no database)
    cd apps/api && uv run python scripts/control_tower_eval.py --live     # the narrator on the configured model
    add --check to exit 1 when a gate is missed

Rule engine: the 20 scripted scenarios in evals/control_tower/rules/cases.jsonl;
every expected exception with its severity must match. Narrator: 30 exceptions
collected from the hospital in DATABASE_URL while the day simulator plays the
scripted scenarios (ICU surge, ED surge, OR overrun), inside one transaction that
is rolled back; each narrated through the agent runtime and scored for schema
validity, evidence that resolves in FHIR and guard acceptance. Writes
evals/control_tower/report.json, report.md, narrator/cases.jsonl (the exceptions)
and narrator/rating_sheet.md (for the human accuracy rating the spec asks for).
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.core.config  # noqa: E402,F401  (loads apps/api/.env)
from app.modules.control_tower import evals  # noqa: E402

TARGET = 30
# Two walks through the planned day, each in its own rolled-back transaction: (scenario and its size or None,
# hospital minutes to advance afterwards). An ICU surge of 9 fills ICU and leaves about 4 patients boarding.
WALKS = [
    [(None, None, 0), ("icu_surge", 9, 60), (None, None, 90), ("ed_surge", None, 60), (None, None, 60),
     ("or_overrun", None, 30), (None, None, 120), (None, None, 180), (None, None, 240), (None, None, 240)],
    [("ed_surge", None, 30), ("ed_surge", None, 60), ("icu_surge", 10, 120), (None, None, 90), ("or_overrun", None, 60),
     (None, None, 120), (None, None, 240), (None, None, 240), (None, None, 300)],
]


def collect_and_narrate(live: bool) -> tuple[list[dict], list[dict], str]:
    """(scores, cases, mode) from a rolled-back walk through the scripted day."""
    from app.core.store import ConnectionSource, Store, get_engine, set_ambient_store
    from app.ehr import scenarios, simulator
    from app.llm.gateway import LlmGateway, get_gateway, set_gateway
    from app.llm.providers import MockProvider
    from app.modules.control_tower import exceptions as X
    from app.modules.control_tower import service
    from app.modules.control_tower.snapshot import tower_fhir

    if not live:
        set_gateway(LlmGateway(MockProvider(latency_s=0)))
    mode = get_gateway().mode
    cases, scores = [], []
    for walk_no, walk in enumerate(WALKS, start=1):
        conn = get_engine().connect()
        outer = conn.begin()
        store = Store(ConnectionSource(conn))
        set_ambient_store(store)
        seen: set[str] = set()
        try:
            service.reset_state()
            for scenario, size, minutes in walk:
                if scenario:
                    scenarios.inject(store, scenario, operator="U-OPS", count=size)
                if minutes:
                    simulator.advance_by(store, minutes)
                service.refresh(store, force=True)
                for exc in X.stream(store, limit=500):
                    if exc.id in seen or len(scores) >= TARGET or exc.cleared_at is not None:
                        continue
                    seen.add(exc.id)
                    narrated = service.narrate(store, exc.id)
                    payload = narrated.model_dump(mode="json")
                    cases.append({"walk": walk_no, **{k: payload[k] for k in (
                        "id", "key", "rule", "severity", "title", "summary", "unit_id", "facts", "menu",
                        "evidence_refs", "detected_at")}})
                    resolvable = tower_fhir().resolve_refs(narrated.narration["evidence_refs"])
                    scores.append({"walk": walk_no, **evals.score_narration(payload, narrated.narration, resolvable)})
                if len(scores) >= TARGET:
                    break
        finally:
            service.reset_state()
            set_ambient_store(None)
            outer.rollback()
            conn.close()
        if len(scores) >= TARGET:
            break
    return scores, cases, mode


def rating_sheet(scores: list[dict]) -> str:
    lines = ["# Narrator: human accuracy rating", "",
             "The spec (7.1) asks for a person to rate each narrative's accuracy from 1 to 5 against the exception's "
             "facts (gate: mean >= 4). Fill in the Score column; the facts are in narrator/cases.jsonl.", "",
             "| Exception | Rule | Severity | Narrative | Score (1-5) |", "| --- | --- | --- | --- | --- |"]
    for s in scores:
        lines.append(f"| {s['id']} | {s['rule']} | {s['severity']} | {(s['narrative'] or '').replace('|', '/')} | |")
    return "\n".join(lines) + "\n"


def markdown(report: dict) -> str:
    r, n = report["rules"], report.get("narrator")
    lines = ["# Control Tower evals", "", f"Run {report['run_at']}. Synthetic data only.", "",
             "## Rule engine", "",
             f"{r['passed']} of {r['cases']} scripted scenarios match their expected exceptions and severities "
             f"({'passed' if r['all_passed'] else '**failed**'}).", "",
             "| Case | Scenario | Expected | Got | Passed |", "| --- | --- | --- | --- | --- |"]
    for c in r["results"]:
        fmt = lambda xs: ", ".join(f"{k} ({s})" for k, s in xs) or "none"  # noqa: E731
        lines.append(f"| {c['id']} | {c['title']} | {fmt(c['expected'])} | {fmt(c['got'])} | "
                     f"{'yes' if c['passed'] else '**no**'} |")
    if n:
        lines += ["", "## Narrator", "",
                  f"Model: {n['mode']}. {n['cases']} exceptions from the seeded hospital while the simulator played "
                  f"the ICU surge, the ED surge and the OR overrun ({', '.join(f'{k} {v}' for k, v in n['by_rule'].items())}).",
                  "", "| Metric | Result | Gate |", "| --- | --- | --- |",
                  f"| Schema validity | {n['schema_validity']:.0%} | 100% |",
                  f"| Evidence references resolvable in FHIR (grounding rate) | {n['grounding_rate']:.0%} | 100% |",
                  f"| Accepted by the guards (no fallback to the engine's template) | {n['guard_pass_rate']:.0%} | - |",
                  f"| Human rating of accuracy | {n['human_rating']} | mean >= 4/5 |", "",
                  f"Gate (schema and grounding on at least 30 exceptions): {'passed' if n['passed'] else '**failed**'}."]
        if n["mode"] == "mock":
            lines += ["", "The mock model writes its narrative from the facts in the prompt, so these results show that "
                          "the pipeline (context, schema, guards, evidence check, review Task) holds; run `--live` for "
                          "a real model's numbers."]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rules", action="store_true", help="the rule engine only")
    parser.add_argument("--live", action="store_true", help="narrate with the configured model instead of the mock")
    parser.add_argument("--check", action="store_true", help="exit 1 when a gate is missed")
    args = parser.parse_args()
    if not args.live:
        os.environ["LLM_PROVIDER"] = "mock"
    started = time.monotonic()
    report: dict = {"run_at": datetime.now().isoformat(timespec="seconds"), "rules": evals.rule_eval()}
    print(f"rules: {report['rules']['passed']}/{report['rules']['cases']} scenarios match")
    if not args.rules:
        scores, cases, mode = collect_and_narrate(args.live)
        report["narrator"] = evals.narrator_summary(scores, mode)
        n = report["narrator"]
        print(f"narrator ({mode}): {n['cases']} exceptions, schema {n['schema_validity']:.0%}, grounding "
              f"{n['grounding_rate']:.0%}, guards {n['guard_pass_rate']:.0%}")
        evals.NARRATOR_CASES.parent.mkdir(parents=True, exist_ok=True)
        evals.NARRATOR_CASES.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases),
                                        encoding="utf-8")
        (evals.EVALS_DIR / "narrator" / "rating_sheet.md").write_text(rating_sheet(scores), encoding="utf-8")
    passed = report["rules"]["all_passed"] and report.get("narrator", {"passed": True})["passed"]
    report["passed"] = passed
    if not args.rules:
        (evals.EVALS_DIR / "report.json").write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
        (evals.EVALS_DIR / "report.md").write_text(markdown(report), encoding="utf-8")
    print(f"{'passed' if passed else 'FAILED'} in {time.monotonic() - started:.1f} s")
    if args.check and not passed:
        sys.exit(1)


if __name__ == "__main__":
    main()
