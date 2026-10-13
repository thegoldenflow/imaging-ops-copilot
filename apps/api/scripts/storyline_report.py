"""The storyline report (WP9, spec 8.2): walk the twelve steps and record each step's time and result.

    uv run python scripts/storyline_report.py --api http://127.0.0.1:9445 [--report]
        against a running API (its own background loops; Temporal on or off as the API is configured): resets the
        storyline, walks it as the cast, waits for the fast-forwards. With --report, writes
        evals/storyline/report_offline.json and .md (report_temporal.* when the API runs with Temporal).
    uv run python scripts/storyline_report.py --in-process [--report]
        in this process, on the configured database, inside one transaction that is rolled back (the preset is
        planted on the data as it is; the background work is run between the steps: tests/storyline_harness.py).
        With --report, writes evals/storyline/report_in_process.json and .md.

The walk (app/modules/storyline/walk.py) is the same one the backend test runs. Nothing here judges a model: the
report shows that the story runs from start to finish, what each step proposed and wrote, and how long it took.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

API = Path(__file__).resolve().parents[1]
ROOT = API.parents[1]
sys.path.insert(0, str(API))
OUT = ROOT / "evals" / "storyline"


def live(base_url: str, timeout_s: float):
    import httpx

    from app.modules.storyline.walk import Walk, WalkFailed, summary_draft_s

    # Step 9's draft request is synchronous and can take up to summary_draft_s() (5 x 60 s) against a live model.
    draft_s = summary_draft_s()
    client = httpx.Client(base_url=base_url, timeout=max(300.0, draft_s + 60))

    def pump() -> None:
        time.sleep(1.0)

    def advance(walk: Walk, beat: str) -> None:
        deadline = time.monotonic() + 900
        while True:
            status = walk.status()
            target = status["beats"].get(beat)
            if target and status["now"] >= target:
                return
            job = status.get("job") or {}
            if job.get("status") != "running":
                r = client.post("/api/storyline/advance", headers=walk.headers(walk_user))
                if r.status_code >= 400 and "Already there" not in r.text and "Not yet" not in r.text:
                    raise WalkFailed(f"advance {beat}: {r.status_code} {r.text[:300]}")
            if time.monotonic() > deadline:
                raise WalkFailed(f"the clock did not reach {beat}")
            time.sleep(1.5)

    from app.modules.storyline import script as S

    walk_user = S.CAST["admin"]
    # The background journey may be drafting the same summary (409 until it ends), so step 9 waits for one whole
    # draft on top of the usual wait (casebook 3.15).
    walk = Walk(client, pump=pump, advance=advance, log=print, timeout_s=timeout_s,
                summary_timeout_s=timeout_s + draft_s)
    started = time.monotonic()
    print("resetting the storyline (demo reset + preset) ...")
    r = client.post("/api/storyline/reset", headers=walk.headers(walk_user))
    if r.status_code >= 400:
        raise SystemExit(f"reset failed: {r.status_code} {r.text[:300]}")
    reset_s = round(time.monotonic() - started, 1)
    status = walk.status()
    return walk, reset_s, status


def in_process(timeout_s: float):
    os.environ.setdefault("BACKGROUND_WORKERS", "0")
    os.environ["TEMPORAL_ADDRESS"] = ""
    from fastapi.testclient import TestClient

    from app.core.store import ConnectionSource, Store, get_engine, set_ambient_store
    from app.ehr.seed import journey
    from app.llm.gateway import LlmGateway, set_gateway
    from app.llm.providers import MockProvider
    from app.modules.storyline.walk import Walk
    from tests import storyline_harness as H

    conn = get_engine().connect()
    outer = conn.begin()
    store = Store(ConnectionSource(conn))
    set_ambient_store(store)
    set_gateway(LlmGateway(MockProvider(latency_s=0)))
    started = time.monotonic()
    journey.plant(store, "U-ADMIN")
    reset_s = round(time.monotonic() - started, 1)
    from app.main import app

    client = TestClient(app)
    client.__enter__()
    walk = Walk(client, pump=H.pump, advance=H.advance, log=print, timeout_s=timeout_s)

    def close() -> None:
        client.__exit__(None, None, None)
        set_gateway(None)
        set_ambient_store(None)
        outer.rollback()
        conn.close()

    return walk, reset_s, walk.status(), close


def write(report: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    name = f"report_{report['kind']}"
    (OUT / f"{name}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = [f"# Storyline report ({report['mode']})", "",
             f"Run {report['run_at']} on {report['platform']}, git {report['git']}; LLM provider {report['llm']}; "
             f"Temporal {'on' if report['temporal'] else 'off'}.", "",
             f"Result: **{'passed' if report['passed'] else 'FAILED'}**: {report['steps_ok']} of 12 steps; walk "
             f"{report['walk_seconds']} s (reset {report['reset_seconds']} s); the demo script budgets "
             f"{report['budget_seconds']} s of talking and clicking.", "",
             "| Step | Who | Seconds | Result | Records |", "| --- | --- | --- | --- | --- |"]
    for s in report["steps"]:
        lines.append(f"| {s['n']}. {s['title']} | {s['user']} | {s['seconds']} | {'ok' if s['ok'] else 'FAILED'}: "
                     f"{s['result'].replace('|', '/')} | {', '.join(s['refs'][:4])} |")
    lines += ["", "Seconds are the walk's (API calls, waits for the background work and the clock's fast-forwards), "
              "not the presenter's; the budget per step is in app/modules/storyline/script.py and "
              "demo/hospital_journey.md.", ""]
    if report.get("error"):
        lines += ["Error:", "", "```", report["error"], "```", ""]
    (OUT / f"{name}.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {OUT / name}.md")


def main() -> None:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--api", help="base URL of a running API, e.g. http://127.0.0.1:9445")
    mode.add_argument("--in-process", action="store_true")
    parser.add_argument("--report", action="store_true", help="write evals/storyline/report_<mode>.json and .md")
    parser.add_argument("--timeout", type=float, default=180,
                        help="seconds to wait for one step's background work (--api: the discharge summary step "
                             "waits this plus the longest a summary draft can take)")
    args = parser.parse_args()
    from app.core.config import settings
    from app.modules.storyline import script as S

    close = None
    if args.api:
        walk, reset_s, status = live(args.api, args.timeout)
    else:
        walk, reset_s, status, close = in_process(args.timeout)
    started = time.monotonic()
    error = None
    try:
        walk.run()
    except Exception as e:  # the report says where it stopped
        error = f"{type(e).__name__}: {e}"
        print("FAILED:", error)
    finally:
        final = walk.status()
        if close:
            close()
    git = subprocess.run(["git", "describe", "--always", "--dirty"], capture_output=True, text=True, cwd=ROOT).stdout.strip()
    temporal = bool(status.get("temporal", {}).get("configured")) and bool(
        (status.get("temporal") or {}).get("status", {}).get("online"))
    report = {"kind": "in_process" if not args.api else "temporal" if temporal else "offline",
              "mode": "running API " + args.api if args.api else "in process (rolled back)",
              "run_at": datetime.now().isoformat(timespec="seconds"), "platform": platform.platform(), "git": git,
              "llm": settings.llm_provider if args.api is None else "the API's",
              "temporal": temporal,
              "reset_seconds": reset_s, "walk_seconds": round(time.monotonic() - started, 1),
              "budget_seconds": S.TOTAL_SECONDS, "steps_ok": sum(1 for r in walk.runs if r.ok),
              "passed": error is None and len(walk.runs) == 12 and all(r.ok for r in walk.runs),
              "journey": final.get("journey"), "hospital_time_end": final.get("now"),
              "steps": [{"n": r.n, "key": r.key, "title": r.title, "user": r.user, "seconds": r.seconds, "ok": r.ok,
                         "result": r.result, "refs": r.refs} for r in walk.runs],
              "error": error}
    if args.report:
        write(report)
    print(json.dumps({k: report[k] for k in ("passed", "steps_ok", "walk_seconds")}))
    sys.exit(0 if report["passed"] else 1)


if __name__ == "__main__":
    from _stdio import utf8_stdio

    utf8_stdio()
    main()
