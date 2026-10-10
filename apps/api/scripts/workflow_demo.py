"""Durable workflows end to end against the real Temporal dev server (spec 6.5; demo/workflows.md).

    docker compose --profile workflows up -d temporal
    TEMPORAL_ADDRESS=127.0.0.1:7233 LLM_PROVIDER=mock uv run python scripts/workflow_demo.py

What it shows, with a real worker process (`python -m app.workflows.worker`) that it starts, kills and restarts:

1. a patient journey started for a Medicine A inpatient (through the bridge's outbox, as an admission would);
2. a deliberate failure: the next two attempts of the MedRec activity fail after the draft was written; Temporal
   retries (1 s, 4 s) and the Tool Gateway's idempotency key returns the first draft: FHIR holds one MedRec;
3. the worker process is killed (TerminateProcess / SIGKILL) while the journey waits for the MedRec signatures;
   the pharmacist and the physician sign meanwhile; the signals wait in Temporal;
4. a new worker process replays the history and goes on from the sign-off without running a completed step
   again (Temporal hands the dead worker's sticky task to it after about 10 s);
5. the timeline as the workflow view shows it.

It writes to the database in DATABASE_URL (use a demo database, not the one the tests use) and leaves the
journey waiting on the ward. Run the API with the same settings to see it in the workflow view.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agents import approvals  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.store import get_store, unit_of_work  # noqa: E402
from app.ehr.consent import consent_status  # noqa: E402
from app.ehr.gateway import Actor, FhirGateway  # noqa: E402
from app.workflows import bridge, client, faults, progress  # noqa: E402
from app.workflows import model as M  # noqa: E402

PHARMACIST, PHYSICIAN = "U-PHAR-01", "U-DOC-09"
T0 = time.monotonic()


def say(text: str) -> None:
    print(f"[{time.monotonic() - T0:6.1f} s] {text}", flush=True)


def pump() -> None:
    """What the API's background loops do: deliver domain events to the bridge, send its commands to Temporal."""
    from app.ehr.events import bus

    bus.drain()
    with unit_of_work() as store:
        bridge.dispatch_step(store)


def worker() -> subprocess.Popen:
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    return subprocess.Popen([sys.executable, "-m", "app.workflows.worker"], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def step(workflow_id: str, key: str) -> dict | None:
    with unit_of_work():
        run = progress.get(workflow_id)
        return next((s for s in run.steps if s["key"] == key), None) if run else None


def wait(workflow_id: str, key: str, status: str, timeout: float = 90) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pump()
        s = step(workflow_id, key)
        if s and s["status"] == status:
            return s
        time.sleep(0.5)
    raise SystemExit(f"timed out: {workflow_id} {key} never became {status} (last: {step(workflow_id, key)})")


def patient() -> tuple[str, str]:
    with unit_of_work() as store:
        fhir = FhirGateway(Actor.system("workflow-demo"), "workflow_engine")
        for enc in sorted(store.fhir.search("Encounter", cls="IMP", status="in-progress", unit="MEDA"),
                          key=lambda e: e["id"]):
            pid = enc["subject"]["reference"].split("/")[1]
            run = progress.get(M.journey_id(enc["id"]))
            if run is None and all(consent_status(fhir, pid)[c] == "permit" for c in ("ai_processing",
                                                                                      "followup_call")):
                return enc["id"], pid
    raise SystemExit("no Medicine A inpatient with both consents and no journey yet (reset the demo data)")


def scheduled(workflow_id: str) -> list[str]:
    async def fetch(c):
        history = await c.get_workflow_handle(workflow_id).fetch_history()
        return [e.activity_task_scheduled_event_attributes.activity_type.name for e in history.events
                if e.HasField("activity_task_scheduled_event_attributes")]
    return client.link().call(fetch)


def main() -> None:
    link = client.link()
    if link is None:
        raise SystemExit("Set TEMPORAL_ADDRESS (e.g. 127.0.0.1:7233) and start the dev server first")
    if not link.status()["online"]:
        raise SystemExit(f"Temporal at {settings.temporal_address} is not reachable")
    if link.workers() > 0:
        raise SystemExit("Another worker polls the task queue; stop it (the demo starts and kills its own)")
    bridge.install()
    encounter, pid = patient()
    wf = M.journey_id(encounter)
    say(f"journey {wf} for inpatient {encounter} (Medicine A), Temporal {settings.temporal_address} "
        f"namespace {settings.temporal_namespace}")

    with unit_of_work():
        faults.inject("medRecAdmission", 2)
        bridge.start_journey(encounter, pid, "user:workflow-demo", f"demo-{int(time.time())}")
    say("deliberate failure set: the next 2 attempts of medRecAdmission fail after their work committed")

    proc = worker()
    say(f"worker process {proc.pid} started")
    s = wait(wf, "orderReview", "waiting")
    with unit_of_work():
        approvals.decide(get_store().staff.get(PHARMACIST), s["waiting_for"]["refs"][0].split("/")[1], "approve",
                         "orders fine (demo)")
    say(f"pharmacist confirmed the order review ({s['waiting_for']['refs'][0]})")

    s = wait(wf, "medRecAdmission", "waiting")
    doc = s["waiting_for"]["refs"][0]
    with unit_of_work() as store:
        medrecs = [d for d in store.fhir.search("DocumentReference", encounter=encounter)
                   if any(c.get("code") == "medrec" for c in (d.get("type") or {}).get("coding") or [])]
    say(f"MedRec step done on attempt {s['attempts']} of {M.RETRY_MAX_ATTEMPTS}; MedRec documents in FHIR for the "
        f"encounter: {len(medrecs)} ({doc})")

    proc.kill()
    proc.wait()
    say(f"worker process {proc.pid} killed while the journey waits for the MedRec co-signature")
    with unit_of_work() as store:
        for uid in (PHARMACIST, PHYSICIAN):
            FhirGateway(Actor.of(store.staff.get(uid)), "signing").sign_document(doc.split("/")[1])
    pump()
    say("pharmacist and physician signed; the signals are stored in Temporal, no worker runs: timeline still "
        f"'{step(wf, 'medRecAdmission')['status']}'")
    before = scheduled(wf)

    proc = worker()
    say(f"worker process {proc.pid} started (a new process with nothing in memory)")
    restarted = time.monotonic()
    wait(wf, "wardMonitoring", "waiting", timeout=120)
    say(f"journey resumed after the sign-off: ward monitoring reached {time.monotonic() - restarted:.1f} s after the "
        f"restart")
    after = scheduled(wf)
    steps_before = [n for n in before if n.startswith("journey.")]
    again = sorted({n for n in steps_before if after.count(n) > before.count(n)})
    new = [n for n in after[len(before):] if n.startswith("journey.")]
    say(f"step activities before the kill: {', '.join(steps_before)}; after the restart only {', '.join(new) or 'none'}"
        f" ran; completed steps run again: {', '.join(again) or 'none'}")
    proc.kill()
    proc.wait()

    with unit_of_work():
        run = progress.get(wf)
        print("\nTimeline (as the workflow view shows it):")
        for s in progress.view(run)["steps"]:
            print(f"  {s['label']:<40} {s['status']:<9} {('attempts ' + str(s['attempts'])) if s['attempts'] > 1 else '':<11}"
                  f" {s['detail'] or ''}"[:160])
    say("done; the journey waits on the ward (open the workflow view, or skip it there as the bed manager)")


if __name__ == "__main__":
    main()
