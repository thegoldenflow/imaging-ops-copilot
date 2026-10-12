"""Run the free-text de-identification eval (spec 6.3) and write evals/deid/report.json and report.md.

    uv run python scripts/deid_eval.py            # second pass on the mock provider (no key needed)
    uv run python scripts/deid_eval.py --live     # second pass on the configured provider (LLM_PROVIDER)
    uv run python scripts/deid_eval.py --check    # exit 1 below recall 0.98 / precision 0.90

Runs without a database: the LLM gateway logs into a detached in-memory store.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.store import Store, use_store  # noqa: E402
from app.llm.deid_eval import evaluate, write_report  # noqa: E402
from app.llm.gateway import LlmGateway, set_gateway  # noqa: E402
from app.llm.providers import MockProvider  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Free-text de-identification eval (6.3)")
    parser.add_argument("--live", action="store_true", help="second pass on the configured provider, not the mock")
    parser.add_argument("--no-second-pass", action="store_true", help="rule layer only")
    parser.add_argument("--check", action="store_true", help="exit 1 when below the thresholds")
    parser.add_argument("--no-write", action="store_true", help="print only, do not write the report")
    args = parser.parse_args()
    set_gateway(LlmGateway() if args.live else LlmGateway(MockProvider(latency_s=0)))
    with use_store(Store()):
        report = evaluate(second_pass=not args.no_second_pass)
    for label, key in (("rule layer", "rule_layer"), ("pipeline", "pipeline")):
        s = report[key]
        print(f"{label:>10}: recall {s['recall']:.4f}  precision {s['precision']:.4f}  "
              f"({s['caught']}/{s['gold_spans']} caught, {s['correct_redactions']}/{s['redacted_spans']} correct)")
    print(f"{'PASSED' if report['passed'] else 'FAILED'} (recall >= {report['thresholds']['recall']}, "
          f"precision >= {report['thresholds']['precision']}); second pass: {report['second_pass']}")
    if not args.no_write:
        for path in write_report(report):
            print("wrote", path)
    return 1 if args.check and not report["passed"] else 0


if __name__ == "__main__":
    sys.exit(main())
