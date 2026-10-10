"""The patient-message triage regression eval (spec 6.5 triggerRegression, 6.6): every case through the agent's
current prompt.

    uv run python scripts/triage_regression.py            (mock provider unless LLM_PROVIDER says otherwise)
    uv run python scripts/triage_regression.py --check    (exit 1 below the gate)

Writes evals/patient_message_triage/report.json and report.md. The LowConfidenceReviewWorkflow runs the same
eval after it appends a human-reviewed case. Runs in a rolled-back transaction (the model calls are logged there).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents import evalsets  # noqa: E402
from app.core.store import get_store, unit_of_work  # noqa: E402


def main() -> int:
    with unit_of_work():
        report = evalsets.regression("patient_message_triage")
        get_store().rollback()
    print(f"{report['passed']} of {report['cases']} cases pass (accuracy {report['accuracy']}, gate {report['gate']}, "
          f"mode {report['mode']}): {report['status']}")
    for r in report["results"]:
        if not r["passed"]:
            print(f"  FAIL {r['id']} ({r['source']}): expected {r['expected']}, got {r['got']}")
    return 1 if "--check" in sys.argv and report["status"] != "passed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
