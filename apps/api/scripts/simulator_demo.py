"""WP3 demo: the day simulator, the HL7 adapter and the domain event bus.

    cd apps/api && uv run python scripts/simulator_demo.py [--hours 1] [--scenario icu_surge] [--full-day]
    cd apps/api && uv run python scripts/simulator_demo.py --report   # writes evals/simulator/day_run.json

Runs against the database in DATABASE_URL (the API's demo data, migrated first)
inside one transaction that is rolled back at the end, so the demo hospital stays
at its current clock; `--commit` keeps the changes. A subscriber prints what it
receives. See demo/event_bus_simulator.md for the talk track.
"""

import argparse
import json
import sys
import time
from collections import Counter
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.db.migrate import upgrade  # noqa: E402
from app.core.store import ConnectionSource, Store, get_engine, set_ambient_store  # noqa: E402
from app.ehr import scenarios, simulator  # noqa: E402
from app.ehr.events import DomainEvent, bus  # noqa: E402
from app.ehr.gateway import Actor, FhirGateway  # noqa: E402
from app.ehr.hl7 import CLASS_CODE, Hl7Message  # noqa: E402
from app.fhir.dt import ref_id  # noqa: E402
from app.fhir.types import validate  # noqa: E402

REPORT = Path(__file__).resolve().parents[3] / "evals" / "simulator" / "day_run.json"


def heading(text: str) -> None:
    print(f"\n== {text} ==")


@contextmanager
def demo_store(commit: bool):
    """One outer transaction for the whole demo (rolled back unless --commit), shared by every step."""
    conn = get_engine().connect()
    outer = conn.begin()
    store = Store(ConnectionSource(conn))
    set_ambient_store(store)
    try:
        yield store
        store.save()
        if commit:
            outer.commit()
        else:
            outer.rollback()
    finally:
        set_ambient_store(None)
        conn.close()


def line(e: DomainEvent) -> str:
    where = ref_id(e.refs.get("location")) or ref_id(e.refs.get("order")) or ref_id(e.refs.get("appointment")) or ""
    extra = ", ".join(f"{k}={v}" for k, v in e.attrs.items())
    return f"{e.occurred_at:%a %H:%M}  {e.source:<8} -> {e.type:<22} {e.encounter or '':<12} {where:<16} {extra}"


def er7_of(e: DomainEvent) -> str:
    """The admission message the event came from, rebuilt from its fields (only ids and codes)."""
    m = Hl7Message(e.source, e.message_id, e.occurred_at, patient=e.patient,
                   patient_class=CLASS_CODE.get(str(e.attrs.get("encounter_class"))), location=ref_id(e.refs.get("location")),
                   visit=e.encounter)
    return m.er7().replace("\r", "\n")


def census(store: Store) -> str:
    ops = next(u for u in store.staff.values() if u.role == "operations_manager")
    fhir = FhirGateway(Actor.of(ops), module="simulator_demo")
    parts = []
    for unit in ("ED", "MEDA", "MEDB", "SURG", "ORTH", "ICU"):
        board = fhir.get_bed_board(unit)
        counts = board.counts()
        if unit == "ED":
            busy = sum(1 for b in board.beds if b.encounter_id)
            parts.append(f"ED {busy}/{len(board.beds)} bays + {len(board.waiting)} waiting")
        else:
            parts.append(f"{unit} {counts.get('O', 0)}/{len(board.beds)} (dirty {counts.get('K', 0)})")
    boarding = sum(1 for r in store.fhir.search("ServiceRequest", category="bed-request", status="active"))
    return " | ".join(parts) + f" | ED boarders {boarding}"


def check_order(received: list[DomainEvent]) -> list[str]:
    problems = []
    times = [e.occurred_at for e in received]
    if times != sorted(times):
        problems.append("events out of timeline order")
    lifecycle: dict[str, list[str]] = {}
    for e in received:
        if e.encounter and e.type.startswith("patient."):
            lifecycle.setdefault(e.encounter, []).append(e.type)
    for enc, types in lifecycle.items():
        if "patient.admitted" in types and types[0] != "patient.admitted":
            problems.append(f"{enc}: {types[0]} before patient.admitted")
        if "patient.discharged" in types and types[-1] != "patient.discharged":
            problems.append(f"{enc}: an event after patient.discharged")
    return problems


def advance_with_retry(fn, *args):
    for _ in range(20):
        try:
            return fn(*args)
        except simulator.SimulatorBusy:  # the running API's background tick holds the lock for a moment
            time.sleep(0.25)
    raise SystemExit("The simulator stays busy; pause it in the API first")


def full_day(store: Store, scenario: str | None) -> dict:
    """One run for the report: optionally a scenario, then fast-forward to 08:00 tomorrow."""
    received: list[DomainEvent] = []
    bus.subscribe("*", received.append, consumer="report.recorder")
    try:
        start = simulator.status(store)["now"]
        injected = scenarios.inject(store, scenario, operator="U-OPS") if scenario else None
        t0 = time.perf_counter()
        result = advance_with_retry(simulator.fast_forward, store, 8)
        seconds = time.perf_counter() - t0
        bus.drain()
    finally:
        bus.unsubscribe("report.recorder")
    invalid = []
    for rtype, rid in sorted(result.written):
        try:
            validate(store.fhir.read(rtype, rid))
        except Exception as e:  # noqa: BLE001 - report every invalid resource
            invalid.append(f"{rtype}/{rid}: {e}"[:200])
    out = {
        "scenario": scenario, "start": start.isoformat(), "end": result.end.isoformat(), "seconds": round(seconds, 1),
        "plan_events_applied": dict(sorted(result.applied.items())),
        "domain_events": dict(sorted(result.events.items())),
        "hl7_messages": dict(sorted(Counter(e.source for e in received if e.source != "platform").items())),
        "event_types": len(result.events), "delivered_to_subscriber": len(received),
        "deferred_for_want_of_a_bed": result.deferred, "ward_vital_sign_panels": result.vitals_panels,
        "resources_written": len(result.written), "resources_invalid": invalid,
        "order_problems": check_order(received), "consistency_problems": simulator.consistency_problems(store),
    }
    if injected:
        out["injected"] = {k: v for k, v in injected.items() if k in ("correlation_id", "patients", "icu_free_beds_now")}
        out["events_with_scenario_correlation_id"] = sum(1 for e in received if e.correlation_id == injected["correlation_id"])
    return json.loads(json.dumps(out, default=str))


def report() -> int:
    upgrade()
    runs = []
    for scenario in (None, "icu_surge"):
        with demo_store(commit=False) as store:
            runs.append(full_day(store, scenario))
            day = store.modules["hospital_plan"]["generated_for"]
    passed = all(r["event_types"] >= 6 and not r["order_problems"] and not r["consistency_problems"]
                 and not r["resources_invalid"] and r["delivered_to_subscriber"] == sum(r["domain_events"].values())
                 for r in runs)
    data = {"what": "WP3 day simulator: a full planned day (07:00 to 08:00 the next day) with and without the ICU "
                    "surge scenario; no model involved",
            "generated_at": datetime.now().isoformat(timespec="seconds"), "hospital_day": day,
            "criteria": {"event_types": ">= 6", "order": "subscriber sees events in timeline order; per encounter "
                         "admitted first, discharged last", "consistency": "one patient per bed; bed status matches "
                         "occupancy; finished encounters closed; every clinical resource has its encounter",
                         "resources": "every resource the simulator wrote validates against app/fhir/types"},
            "passed": passed, "runs": runs}
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    for r in runs:
        print(f"{r['scenario'] or 'planned day'}: {sum(r['domain_events'].values())} events of {r['event_types']} types "
              f"in {r['seconds']} s; deferred {r['deferred_for_want_of_a_bed']}; problems "
              f"{len(r['order_problems']) + len(r['consistency_problems']) + len(r['resources_invalid'])}")
    print(f"{'PASSED' if passed else 'FAILED'}; report written to {REPORT}")
    return 0 if passed else 1


def demo(args) -> int:
    upgrade()
    with demo_store(commit=args.commit) as store:
        received: list[DomainEvent] = []
        bus.subscribe("*", received.append, consumer="demo.printer")
        try:
            state = simulator.status(store)
            heading(f"Hospital clock {state['now']:%a %Y-%m-%d %H:%M} (plan until {state['horizon']:%a %H:%M}), "
                    f"{state['remaining']} planned events left")
            for e in state["upcoming"][:5]:
                print(f"  next: {e['at']:%H:%M}  {e['kind']:<12} {e['ref']}")
            print("census:", census(store))

            heading(f"Advance {args.hours:g} hour(s): the simulator writes FHIR, the HL7 adapter publishes events")
            result = advance_with_retry(simulator.advance_by, store, args.hours * 60)
            bus.drain()
            print(f"applied {sum(result.applied.values())} plan events -> {sum(result.events.values())} domain events "
                  f"{dict(Counter(e.type for e in received).most_common())}")
            for e in received[:14]:
                print(" ", line(e))
            first = next((e for e in received if e.source == "ADT^A01"), None)
            if first:
                heading("What the interface engine received for the first arrival (ids and codes only)")
                print(er7_of(first))
            print("census:", census(store))

            if args.scenario:
                heading(f"Inject scenario {args.scenario}: {scenarios.SCENARIOS[args.scenario]}")
                added = scenarios.inject(store, args.scenario, operator="U-OPS")
                if "patients" in added:
                    pts = added["patients"]
                    print(f"{len(pts)} patients {pts[0]['visit']} .. {pts[-1]['visit']}, arriving "
                          f"{pts[0]['arrival']:%H:%M}-{pts[-1]['arrival']:%H:%M}; correlation id {added['correlation_id']}"
                          + (f"; ICU beds free now: {added['icu_free_beds_now']}" if "icu_free_beds_now" in added else ""))
                else:
                    print({k: v for k, v in added.items() if k not in ("description", "scenario")})
                received.clear()
                result = advance_with_retry(simulator.advance_by, store, 150)
                bus.drain()
                mine = [e for e in received if e.correlation_id == added["correlation_id"]]
                print(f"2.5 hours later: {len(mine)} events carry the scenario's correlation id, actor {mine[0].actor if mine else '-'}; "
                      f"{result.deferred} admissions or transfers waited for a bed")
                for e in mine[:12]:
                    print(" ", line(e))
                print("census:", census(store))

            if args.full_day:
                heading("Fast-forward to 08:00 tomorrow")
                received.clear()
                t0 = time.perf_counter()
                result = advance_with_retry(simulator.fast_forward, store, 8)
                bus.drain()
                print(f"{time.perf_counter() - t0:.1f} s: {sum(result.events.values())} events "
                      f"{dict(sorted(result.events.items()))}")
                print("order problems:", check_order(received) or "none")
                print("census:", census(store))
            print("consistency problems:", simulator.consistency_problems(store) or "none")
        finally:
            bus.unsubscribe("demo.printer")
    print("\n(rolled back: the demo hospital is unchanged)" if not args.commit else "\n(committed)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--hours", type=float, default=1.0)
    parser.add_argument("--scenario", choices=sorted(scenarios.SCENARIOS))
    parser.add_argument("--full-day", action="store_true")
    parser.add_argument("--report", action="store_true", help="write evals/simulator/day_run.json")
    parser.add_argument("--commit", action="store_true", help="keep the changes (default: roll back)")
    args = parser.parse_args()
    return report() if args.report else demo(args)


if __name__ == "__main__":
    sys.exit(main())
