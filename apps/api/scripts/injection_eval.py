"""Run the prompt-injection eval (spec 6.4) and write evals/injection/report.json and report.md.

    cd apps/api && uv run python scripts/injection_eval.py            # mock model (no key needed)
    cd apps/api && uv run python scripts/injection_eval.py --live     # configured model for the second pass
    cd apps/api && uv run python scripts/injection_eval.py --check    # exit 1 above 0 unauthorised tool calls

Runs against the database in DATABASE_URL (the API's demo data, migrated first) inside one
transaction that is rolled back at the end: the attack calls and the fixtures they need leave nothing
behind. The worst-case pass does not depend on the model; --live only changes the second pass.
"""

from __future__ import annotations

import argparse
import sys
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents.injection_eval import evaluate, write_report  # noqa: E402
from app.core.db.migrate import upgrade  # noqa: E402
from app.core.store import ConnectionSource, Store, get_engine, set_ambient_store  # noqa: E402
from app.llm.gateway import LlmGateway, set_gateway  # noqa: E402
from app.llm.providers import MockProvider  # noqa: E402


@contextmanager
def rolled_back():
    conn = get_engine().connect()
    outer = conn.begin()
    store = Store(ConnectionSource(conn))
    set_ambient_store(store)
    try:
        yield store
    finally:
        set_ambient_store(None)
        outer.rollback()
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Prompt-injection eval (6.4)")
    parser.add_argument("--live", action="store_true", help="second pass on the configured provider, not the mock")
    parser.add_argument("--check", action="store_true", help="exit 1 above the threshold")
    parser.add_argument("--no-write", action="store_true", help="print only, do not write the report")
    args = parser.parse_args()
    upgrade()
    set_gateway(LlmGateway() if args.live else LlmGateway(MockProvider(latency_s=0)))
    import app.main  # noqa: F401  (registers the modules' mock fixtures)

    with rolled_back() as store:
        report = evaluate(store)
    print(f"{report['cases']} cases, {report['attack_calls']} attack calls, "
          f"{report['unauthorised_tool_calls']} executed (threshold 0); "
          f"{report['cases_passed']}/{report['cases']} refused for the expected reason")
    print("refused by:", ", ".join(f"{k} {n}" for k, n in report["blocked_by"].items()))
    cm = report["configured_model"]
    print(f"configured model ({cm['provider']}): {cm['requested']} requests, {cm['executed_requests']} executed, "
          f"{cm['unauthorised_tool_calls']} unauthorised")
    for r in report["results"]:
        if not r["passed"]:
            print("  FAILED", r["id"], r["calls"], r["error"] or "")
    print("PASSED" if report["passed"] else "FAILED")
    if not args.no_write:
        for path in write_report(report):
            print("wrote", path)
    return 1 if args.check and not report["passed"] else 0


if __name__ == "__main__":
    sys.exit(main())
