"""WP5 demo: the hospital flow Control Tower in the terminal, in the order of demo/flow_control_tower.md.

    cd apps/api && uv run python scripts/control_tower_demo.py [--commit]

Runs against the database in DATABASE_URL (the API's demo data) inside one
transaction that is rolled back at the end (`--commit` keeps it), with the mock
model unless LLM_PROVIDER says otherwise. Prints the boards at 07:00, the
exceptions, one narrated and approved (Tasks, audit), the ICU surge of 8 patients
played forward, the ED boarders it causes, the OR overrun, the refresh latency
after each simulator step, and the fast-forward to 08:00 tomorrow in steps.
"""

import argparse
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.core.config  # noqa: E402,F401  (loads apps/api/.env)
from app.core.store import ConnectionSource, Store, get_engine, set_ambient_store  # noqa: E402
from app.ehr import scenarios, simjobs, simulator  # noqa: E402
from app.ehr.clock import hospital_now  # noqa: E402
from app.modules.control_tower import exceptions as X  # noqa: E402
from app.modules.control_tower import rules, service  # noqa: E402


def heading(text: str) -> None:
    print(f"\n== {text} ==")


@contextmanager
def demo_store(commit: bool):
    conn = get_engine().connect()
    outer = conn.begin()
    store = Store(ConnectionSource(conn))
    set_ambient_store(store)
    try:
        yield store
        store.save()
        outer.commit() if commit else outer.rollback()
    finally:
        service.reset_state()
        set_ambient_store(None)
        conn.close()


def boards(store: Store) -> None:
    started = time.monotonic()
    snap = service.refresh(store, force=True)
    k = snap.kpis
    print(f"hospital time {snap.now:%a %H:%M}; board built in {snap.build_ms} ms "
          f"(refresh {int((time.monotonic() - started) * 1000)} ms incl. rules)")
    wait = f"{k.ed_predicted_wait.value:.0f} min" if k.ed_predicted_wait else "-"
    print(f"ED      {k.ed_census} patients, {k.ed_waiting} waiting to be seen, {k.ed_boarders} waiting for a bed "
          f"({k.ed_boarders_over_2h} over 2 h), predicted wait next hour {wait}, {k.lwbs_risk} at risk of leaving")
    print(f"Beds    {k.occupancy:.0%} occupied ({k.occupied}/{k.beds}), {k.free} free, {k.cleaning} cleaning, "
          f"net gap {k.net_gap:+d} ({k.expected_admissions} in - {k.expected_discharges} out - {k.free} free)")
    for u in snap.units:
        print(f"        {u.id:5s} {u.occupancy:4.0%} {u.occupied:2d}/{u.beds:2d}  free {u.free:2d}  cleaning {u.cleaning}"
              f"  ALC {u.alc}  out {u.expected_discharges} ({u.discharge_ready} ready)  in {u.expected_admissions}"
              f" (ED {u.ed_boarders})  gap {u.net_gap:+d}")
    util = f"{k.or_utilization:.0%}" if k.or_utilization is not None else "-"
    print(f"OR      {k.or_cases} cases ({k.or_done} done, {k.or_in_progress} in the OR), block utilisation {util}, "
          f"{k.or_cancellation_risk} at risk of cancellation")
    ready = [e for e in snap.ed if e.admit]
    if ready:
        print(f"        e.g. {ready[0].encounter_id}: {ready[0].admit.sentence}")


def stream(store: Store) -> None:
    live = [e for e in X.stream(store) if e.status in X.LIVE and e.cleared_at is None]
    print(f"{len(live)} open exceptions:")
    for e in live:
        print(f"  [{e.severity:4s}] {e.id} {e.title}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--commit", action="store_true", help="keep the changes")
    args = parser.parse_args()
    os.environ.setdefault("LLM_PROVIDER", "mock")
    with demo_store(args.commit) as store:
        heading("1. The hospital at the start of the day")
        boards(store)
        stream(store)

        heading("2. The most urgent exception: AI narrative, approve -> Tasks -> audit")
        top = next(e for e in X.stream(store) if e.status == "open")
        exc = service.narrate(store, top.id)
        n = exc.narration
        print(f"{exc.id} ({exc.severity}) {exc.title}")
        print(f"  narrative ({n['source']}, {'evaluated' if n['evaluated'] else 'NOT EVALUATED'}): {n['narrative']}")
        for a in n["recommended_actions"]:
            print(f"  - [{a['owner_role']}] {a['action']}\n      why: {a['rationale']}; effect: {a['expected_effect']}")
        print(f"  evidence (resolved in FHIR): {', '.join(n['evidence_refs'])}")
        ops = store.staff["U-OPS"]
        chosen = [a["action_id"] for a in n["recommended_actions"]][:2]
        exc = service.approve(store, ops, exc.id, chosen, "Demo approval")
        for a in exc.decision["actions"]:
            print(f"  approved -> Task {a['task_id']} for {a['owner_role']}: {a['label']}")
        audit = [e for e in store.audit.query(event_type="approve") if e.resource_id == exc.review_task_id]
        print(f"  audit: {audit[0].event_type}/{audit[0].action} by {audit[0].user_id} on Task {audit[0].resource_id}")

        heading("3. Inject an ICU surge of 10 critically ill arrivals, run 3.5 hours")
        added = scenarios.inject(store, "icu_surge", operator="U-OPS", count=10)
        print(f"injected {len(added['patients'])} patients (ICU had {added['icu_free_beds_now']} free beds), "
              f"correlation {added['correlation_id'][:12]}")
        for minutes in (60, 90, 60):
            started = time.monotonic()
            simulator.advance_by(store, minutes)
            snap = service.refresh(store)
            print(f"  +{minutes} min -> {snap.now:%H:%M}: board and rules refreshed "
                  f"{int((time.monotonic() - started) * 1000)} ms after the step began "
                  f"(snapshot {snap.build_ms} ms); ICU {next(u for u in snap.units if u.id == 'ICU').occupancy:.0%}, "
                  f"{snap.kpis.ed_boarders} boarding ({snap.kpis.ed_boarders_over_2h} over 2 h)")
        boards(store)
        stream(store)

        heading("4. The OR overrun scenario")
        added = scenarios.inject(store, "or_overrun", operator="U-OPS")
        print(f"case {added['case']} in {added['room']} runs {added['overrun_minutes']} min over, pushing "
              f"{len(added['pushed'])} later cases")
        simulator.advance_by(store, 120)
        service.refresh(store)
        stream(store)

        heading("5. Fast-forward to 08:00 tomorrow, in steps of 30 hospital minutes")
        started = time.monotonic()
        target = simulator.next_morning(hospital_now(store), 8)
        events = simjobs.run_sync(store, target)
        print(f"{events} domain events to {hospital_now(store):%a %H:%M} in {time.monotonic() - started:.1f} s")
        boards(store)
        stream(store)
        found = rules.evaluate(service.refresh(store))
        print(f"rule engine now: {len(found)} findings")
    print("\n(rolled back)" if not args.commit else "\n(committed)")


if __name__ == "__main__":
    main()
