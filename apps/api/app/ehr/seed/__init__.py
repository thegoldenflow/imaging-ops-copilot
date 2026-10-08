"""The synthetic hospital: a deterministic, Synthea-style FHIR data set.

`generate(store, seed, wall_now)` writes the Location tree, practitioners, 1,000
patients with their histories and a simulated 60-day hospital timeline into
`store.fhir`, as of 07:00 on the hospital day, and stores the following 36 hours
as the day simulator's plan. Everything is fictional (see docs/data-model.md).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.core.store import Store
from app.ehr import codes as C
from app.ehr.seed import materialize as M
from app.ehr.seed import people
from app.ehr.seed.builder import Builder
from app.ehr.seed.simulate import WINDOW_DAYS, simulate

DAY_START_HOUR = 7
# Events at the same minute: a bed is cleaned and patients leave before others arrive in it.
KIND_ORDER = {"bed_clean": 0, "discharge": 1, "ed_depart": 2, "surgery_end": 3, "day_surgery_leave": 4, "transfer": 5}


def plan_order(event: dict) -> tuple[str, int]:
    """Sort key of plan events (`at` is an ISO timestamp, so it sorts as text)."""
    return event["at"], KIND_ORDER.get(event["kind"], 9)


def hospital_day_start(wall_now: datetime) -> datetime:
    """07:00 on the hospital day: today, or the nearest weekday at a weekend (so the OR has a list)."""
    day = wall_now.replace(hour=DAY_START_HOUR, minute=0, second=0, microsecond=0)
    if day.weekday() == 5:
        day -= timedelta(days=1)
    elif day.weekday() == 6:
        day += timedelta(days=1)
    return day


def generate(store: Store, seed: int, wall_now: datetime) -> dict:
    now = hospital_day_start(wall_now)
    b = Builder(store, seed, now)
    people.build_locations(b)
    staff = people.build_practitioners(b)
    patients = people.build_patients(b)
    people.build_baseline(b, patients, now - timedelta(days=WINDOW_DAYS))
    doctors: dict[str, list[str]] = {}
    for p in staff:
        if p.role == "doctor" and p.unit and not p.id.startswith("prac-surg"):
            doctors.setdefault(p.unit, []).append(p.id)
    tl = simulate(b.rng, patients, doctors, now)
    by_id = {p.id: p for p in patients}
    visits = {v.id: v for v in tl.ed_visits}
    surgeries = {s.id: s for s in tl.surgeries}

    events: list[dict] = []
    plan_visits: dict[str, dict] = {}
    plan_stays: dict[str, dict] = {}
    plan_surgeries: dict[str, dict] = {}
    plan_orders: dict[str, dict] = {}

    def event(at: datetime, kind: str, **refs) -> None:
        if now < at <= tl.horizon:
            events.append({"at": at.isoformat(), "kind": kind, **refs})

    # ED visits
    for v in tl.ed_visits:
        stay = tl.stay(v.stay) if v.stay else None
        if v.arrival <= now:
            M.write_ed_visit(b, v, by_id[v.patient], stay, now)
            if v.end > now:
                for order in M.ed_orders(b, v, by_id[v.patient]):
                    if order.authored <= now:
                        M.write_order(b, order, now)
                    if order.completed > now:
                        plan_orders[order.id] = M.plan_dict(order)
                        event(order.authored, "order", order=order.id)
                        event(order.completed, "result", order=order.id)
        elif v.arrival <= tl.horizon:
            for order in M.ed_orders(b, v, by_id[v.patient]):
                plan_orders[order.id] = M.plan_dict(order)
                event(order.authored, "order", order=order.id)
                event(order.completed, "result", order=order.id)
        if v.end > now and v.arrival <= tl.horizon:
            plan_visits[v.id] = M.plan_dict(v)
            event(v.arrival, "ed_arrival", visit=v.id)
            event(v.triage, "ed_triage", visit=v.id)
            if not v.lwbs:
                event(v.seen, "ed_seen", visit=v.id)
            event(v.decision, "ed_decision", visit=v.id)
            if not v.admit:
                event(v.end, "ed_depart", visit=v.id)

    # Inpatient stays
    for s in tl.stays:
        p = by_id[s.patient]
        surgery = surgeries.get(s.surgery) if s.surgery else None
        ed = visits.get(s.ed_visit) if s.ed_visit else None
        if s.admit <= now:
            future_orders = M.write_stay(b, s, p, surgery, ed, now)
        elif s.admit <= tl.horizon:
            future_orders = M.stay_orders(b, s, p)
        else:
            continue
        if s.discharge > now:
            plan_stays[s.id] = M.plan_dict(s)
            event(s.admit, "admit", stay=s.id)
            for bed, since, _until in s.beds[1:]:
                event(since, "transfer", stay=s.id, bed=bed)
            if s.alc_from and s.alc_from < s.discharge:
                event(s.alc_from, "alc", stay=s.id)
            event(s.discharge, "discharge", stay=s.id)
            for order in future_orders:
                plan_orders[order.id] = M.plan_dict(order)
                event(order.authored, "order", order=order.id)
                event(order.completed, "result", order=order.id)

    # Surgeries
    for sg in tl.surgeries:
        M.write_surgery(b, sg, by_id[sg.patient], now, tl.horizon, tl.stay(sg.stay) if sg.stay else None)
        if sg.booked_start > tl.horizon:
            continue
        if sg.status == "cancelled":
            cancel_at = sg.booked_start - timedelta(hours=1)
            if sg.booked_at <= now < cancel_at:  # on today's list now, cancelled an hour before the start
                plan_surgeries[sg.id] = M.plan_dict(sg)
                event(cancel_at, "surgery_cancel", surgery=sg.id)
            continue
        if sg.end > now:
            plan_surgeries[sg.id] = M.plan_dict(sg)
            event(sg.booked_at, "surgery_booked", surgery=sg.id)  # emergency cases booked from the ED later on
            if sg.day_surgery:
                event(sg.booked_start - timedelta(hours=1, minutes=30), "day_surgery_arrival", surgery=sg.id)
                event(sg.end + timedelta(hours=4), "day_surgery_leave", surgery=sg.id)
            event(sg.start, "surgery_start", surgery=sg.id)
            event(sg.end, "surgery_end", surgery=sg.id)

    # Bed status at 07:00: occupied, waiting for housekeeping, or free
    occupied = {bed for s in tl.stays for bed, since, until in s.beds if since <= now < until}
    for bed, slots in tl.bed_free_at.items():
        status = "U"
        if bed in occupied:
            status = "O"
        elif any(start <= now < end for start, end in slots):
            status = "K"
        for _start, end in slots:  # each booking ends with its housekeeping window
            event(end, "bed_clean", bed=bed)
        if status != "U":
            loc = store.fhir.read("Location", bed)
            loc["operationalStatus"] = C.coding(C.BED_STATUS, status, {"O": "Occupied", "K": "Contaminated"}[status])
            store.fhir.update(loc)

    events.sort(key=plan_order)
    store.modules["hospital_plan"] = {"generated_for": now.isoformat(), "horizon": tl.horizon.isoformat(), "seed": seed,
                                      "events": events, "visits": plan_visits, "stays": plan_stays,
                                      "surgeries": plan_surgeries, "orders": plan_orders}
    store.modules["hospital_clock"] = {"now": now.isoformat(), "rate": 0, "running": False}
    return {"now": now, "resources": b.written, "patients": len(patients), "events": len(events),
            "inpatients": len(occupied), "ed_now": sum(1 for v in tl.ed_visits if v.arrival <= now < v.end)}

