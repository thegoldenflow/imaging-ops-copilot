"""The WP4c eval report (spec 6.5 acceptance): evals/workflows/report.json and report.md.

    TEST_DATABASE_URL=postgresql+psycopg://ioc:ioc@127.0.0.1:5433/ioc_test_wp4c uv run python scripts/workflow_eval.py
    ... --real-server     also runs scripts/workflow_demo.py against TEMPORAL_ADDRESS (DATABASE_URL: a demo database)

No model is judged here: the "eval" of durable workflows is whether each acceptance criterion holds. Each
criterion maps to tests (Temporal's time-skipping test server with a worker in the test process, the replay of
captured histories, the bridge without a server); the script runs them, reads the JUnit result and writes the
report. With --real-server it adds the run against the docker dev server with a real worker process that is
killed and restarted (the sticky-queue hand-over the test server cannot show).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

API = Path(__file__).resolve().parents[1]
REPO = API.parents[1]
OUT = REPO / "evals" / "workflows"
FILES = ["tests/test_workflows.py", "tests/test_workflow_replay.py", "tests/test_workflow_bridge.py"]

CRITERIA = [
    ("medrec_retry", "The journey with an injected failure in the MedRec activity, automatically retried, leaves "
                     "exactly one MedRec document in FHIR",
     ["test_a_failed_medrec_activity_is_retried_and_leaves_exactly_one_medrec_document"]),
    ("worker_restart", "Killing and restarting the worker resumes the journey without re-running completed steps",
     ["test_a_restarted_worker_resumes_the_journey_without_running_completed_steps_again"]),
    ("signoff_timeout", "A sign-off timeout (24 h; 24 s in the test) produces the escalation Task, then the "
                        "operations manager is told",
     ["test_a_signoff_not_given_in_24_seconds_escalates_then_tells_the_operations_manager"]),
    ("low_confidence", "The low-confidence loop: after the human correction the eval set has one more case and the "
                       "regression eval runs",
     ["test_low_confidence_output_is_corrected_added_as_an_eval_case_and_the_regression_runs"]),
    ("replay", "One Temporal replay test per workflow (plus the version-1 journey through patched())",
     ["test_captured_history_replays_against_the_current_code", "test_every_workflow_has_a_replay_test",
      "test_the_version_1_journey_has_no_patch_marker_and_the_current_one_has"]),
    ("journey_end_to_end", "The whole journey from admission to the booked follow-up call",
     ["test_journey_runs_from_admission_to_the_booked_follow_up_call"]),
    ("privileged", "An activity calling a privileged tool is not retried; its failure becomes a Task; retry and "
                   "skip by the operations manager are audited",
     ["test_a_failed_privileged_call_is_not_retried_and_waits_for_a_retry_or_skip"]),
    ("capacity", "Capacity exceptions: approved actions executed with idempotency keys, verified after 1 h; "
                 "rejected and cleared ones record an outcome",
     ["test_capacity_workflow_executes_the_approval_and_checks_the_occupancy_an_hour_later",
      "test_rejected_and_cleared_exceptions_record_their_outcome_too"]),
    ("bridge_offline", "EventBus <-> Temporal bridge (insert-first outbox, order, outage), offline mode, roles and "
                       "audit of retry and skip",
     ["test_without_temporal_the_bridge_is_off_and_the_view_says_offline",
      "test_domain_events_map_to_workflow_starts_and_signals", "test_the_outbox_takes_each_event_once_and_sends_in_order",
      "test_commands_wait_while_temporal_is_unreachable_and_go_out_when_it_is_back",
      "test_retry_and_skip_are_for_the_operations_manager_and_admin_and_audited"]),
]


def run_tests() -> dict[str, dict]:
    if not os.environ.get("TEST_DATABASE_URL"):
        raise SystemExit("Set TEST_DATABASE_URL to a test database of your own (the tests drop and recreate it)")
    with tempfile.TemporaryDirectory() as tmp:
        junit = Path(tmp) / "junit.xml"
        env = {k: v for k, v in os.environ.items() if k != "TEMPORAL_ADDRESS"}  # the tests never use a real server
        subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *FILES, f"--junitxml={junit}"],
                       cwd=API, check=False, env=env)
        root = ET.parse(junit).getroot()
    results = {}
    for case in root.iter("testcase"):
        name = case.get("name", "")
        failed = case.find("failure") is not None or case.find("error") is not None
        skipped = case.find("skipped") is not None
        results[name] = {"file": case.get("classname"), "seconds": round(float(case.get("time", 0)), 1),
                         "status": "failed" if failed else "skipped" if skipped else "passed"}
    return results


def real_server() -> dict:
    if not os.environ.get("TEMPORAL_ADDRESS"):
        return {"ran": False, "why": "TEMPORAL_ADDRESS not set"}
    done = subprocess.run([sys.executable, "scripts/workflow_demo.py"], cwd=API, capture_output=True, text=True,
                          timeout=900)
    lines = [ln for ln in done.stdout.splitlines() if ln.startswith("[")]
    again = next((ln for ln in lines if "run again" in ln), "")
    medrec = next((ln for ln in lines if "MedRec documents" in ln), "")
    resumed = next((ln for ln in lines if "resumed" in ln), "")
    return {"ran": True, "exit_code": done.returncode, "transcript": lines,
            "stderr": [ln for ln in done.stderr.splitlines() if not ln.startswith('{"level"')][-10:],
            "medrec_documents": int(m.group(1)) if (m := re.search(r"encounter: (\d+)", medrec)) else None,
            "rerun_steps": "none" if again.rstrip().endswith("none") else again.split("again: ")[-1] if again else None,
            "resume_seconds": float(m.group(1)) if (m := re.search(r"reached ([\d.]+) s", resumed)) else None}


def main() -> int:
    results = run_tests()
    criteria = []
    for key, text, tests in CRITERIA:
        found = {t: [v for n, v in results.items() if n == t or n.startswith(t + "[")] for t in tests}
        statuses = [v["status"] for runs in found.values() for v in runs]
        status = "passed" if statuses and all(s == "passed" for s in statuses) and all(found.values()) else \
            "skipped" if statuses and all(s == "skipped" for s in statuses) else "failed"
        criteria.append({"key": key, "criterion": text, "status": status, "tests": {
            t: [v["status"] for v in runs] or ["missing"] for t, runs in found.items()}})
    demo = (REPO / "demo" / "workflows.md").exists()
    criteria.append({"key": "demo", "criterion": "Demo script demo/workflows.md (3 minutes, a deliberate failure and "
                                                 "the recovery)", "status": "passed" if demo else "failed",
                     "tests": {"demo/workflows.md": ["present" if demo else "missing"]}})
    server = real_server() if "--real-server" in sys.argv else {"ran": False, "why": "--real-server not given"}
    import temporalio

    report = {"package": "WP4c durable workflows (spec 6.5)", "run_at": datetime.now().isoformat(timespec="seconds"),
              "temporalio": temporalio.__version__, "status": "passed" if all(c["status"] == "passed"
                                                                               for c in criteria) else "failed",
              "tests": {"total": len(results), "passed": sum(v["status"] == "passed" for v in results.values()),
                        "failed": sum(v["status"] == "failed" for v in results.values()),
                        "skipped": sum(v["status"] == "skipped" for v in results.values()),
                        "seconds": round(sum(v["seconds"] for v in results.values()), 1)},
              "criteria": criteria, "real_server": server, "results": results}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    lines = ["# Durable workflows: acceptance report (WP4c, spec 6.5)", "",
             f"Run {report['run_at']} · temporalio {report['temporalio']} · Temporal's time-skipping test server and "
             f"the replay of captured histories · {report['tests']['passed']} of {report['tests']['total']} tests "
             f"passed in {report['tests']['seconds']} s.", "", f"**{report['status']}**", "",
             "| Criterion | Status | Tests |", "| --- | --- | --- |"]
    for c in criteria:
        tests = "<br>".join(f"`{t}` {', '.join(s)}" for t, s in c["tests"].items())
        lines.append(f"| {c['criterion']} | {c['status']} | {tests} |")
    lines += ["", "## Real server (docker dev server, a real worker process killed and restarted)", ""]
    if server.get("ran"):
        if server["exit_code"] and server.get("stderr"):
            lines += ["Error output:", "", "```", *server["stderr"], "```", ""]
        lines += [f"Exit code {server['exit_code']}; MedRec documents after the injected failures: "
                  f"{server['medrec_documents']}; resumed {server['resume_seconds']} s after the restart; completed "
                  f"steps run again: {server['rerun_steps']}.", "", "```", *server["transcript"], "```"]
    else:
        lines.append(f"Not run ({server['why']}): `scripts/workflow_demo.py` with TEMPORAL_ADDRESS, see "
                     f"demo/workflows.md.")
    lines += ["", "No model is judged in this package: the AI steps it orchestrates have their own evals "
              "(evals/control_tower, evals/patient_message_triage).", ""]
    (OUT / "report.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")
    print(f"{report['status']}: {report['tests']['passed']}/{report['tests']['total']} tests; "
          + ", ".join(f"{c['key']} {c['status']}" for c in criteria))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
