"""Suggestions a person accepted: the EHR records them (CDS Hooks style; WP9, spec 8.2 steps 1-3).

An agent or module proposes an EHR action as a suggestion card (the recommend tool `suggestEhrAction`: an
`ai-review` Task that carries the action). Nothing is recorded until a person accepts the card in the review queue
or the call centre's requests (`approvals.decide`: the `approve` audit event, then `task.decided`). The EHR then
records the action, with that person as the operator, the way it records everything else. Here the day simulator
plays the EHR (app/ehr/simulator.py), so the action is applied as one event at the hospital clock's current time
(`simulator.apply_now`) and the EHR sends its HL7 message; in a real deployment this is the EHR's own handling of an
accepted CDS Hooks suggestion. The AI layer never writes an Encounter, Observation or ServiceRequest itself (6.2:
those are not on FhirGateway's write allow-list).

| kind          | accepted by | the EHR records                                                                  |
| ------------- | ----------- | -------------------------------------------------------------------------------- |
| registration  | clerk       | the ED arrival (Encounter class EMER, ADT^A01): the patient's expected visit when |
|               |             | the plan holds one (an ambulance pre-notification), else a new walk-in visit      |
| triage        | nurse       | the triage (ADT^A08): the CTAS level and the vital signs as Observations, plus a  |
|               |             | fall-risk Flag when the card suggested one                                        |
| imaging_order | physician   | the imaging orders with their priority and protocol (ServiceRequest, ORM^O01);    |
|               |             | their results come later in the plan (ORU^R01)                                    |

Delivery is the bus's (a subscriber, consumer `ehr.accepted-suggestions`): a handler may run again for the same
event, and every action is idempotent (the simulator refuses an arrival, triage or order it already recorded). A
card the EHR cannot record (the patient is already in the ED, the visit is gone) gets a note saying why.
"""

from __future__ import annotations

import json
import logging
import random
import uuid
from datetime import timedelta

from app.core.store import Store, get_store
from app.ehr import codes as C
from app.ehr import simulator
from app.ehr.events import DomainEvent, bus
from app.ehr.seed import materialize as M
from app.ehr.seed import plan_order
from app.ehr.seed.materialize import OrderSpec
from app.fhir.dt import fhir_datetime, parse, ref

log = logging.getLogger("app.ehr")
CONSUMER = "ehr.accepted-suggestions"
ACCEPTED_BY = {"registration": "clerk", "triage": "nurse", "imaging_order": "physician"}
COMPLAINTS = tuple(C.COMPLAINTS)


class NotRecorded(ValueError):
    """The EHR cannot record the accepted action (it explains why on the card)."""


def install() -> bool:
    if any(s.consumer == CONSUMER for s in bus.subscriptions()):
        return False
    bus.subscribe("task.decided", handle, consumer=CONSUMER)
    return True


def action_of(task: dict) -> dict | None:
    for entry in task.get("input") or []:
        if (entry.get("type") or {}).get("text") == "ehr_action" and entry.get("valueString"):
            return json.loads(entry["valueString"])
    return None


def handle(event: DomainEvent) -> None:
    if event.attrs.get("decision") != "accept" or event.attrs.get("code") != "ai-review":
        return
    store = get_store()
    task_id = (event.refs.get("task") or "").split("/", 1)[-1]
    task = store.fhir.read("Task", task_id)
    action = action_of(task) if task else None
    if action is None:
        return
    operator = event.actor.removeprefix("user:") if event.actor.startswith("user:") else None
    if event.attrs.get("role") != ACCEPTED_BY.get(action.get("kind")):
        _note(store, task, f"Not recorded: a {action.get('kind')} is accepted by the "
                           f"{ACCEPTED_BY.get(action.get('kind'), 'responsible role')}, not the {event.attrs.get('role')}")
        return
    try:
        written = record(store, action, operator=operator, correlation_id=event.correlation_id)
    except NotRecorded as e:
        _note(store, task, f"Not recorded by the EHR: {e}")
        return
    _note(store, task, f"Recorded by the EHR ({action['kind']}): {', '.join(written) or 'nothing new'}")


def record(store: Store, action: dict, *, operator: str | None, correlation_id: str | None = None) -> list[str]:
    """Apply one accepted action now. Returns the references the EHR wrote."""
    kind = action.get("kind")
    if kind == "registration":
        return register(store, action, operator=operator, correlation_id=correlation_id)
    if kind == "triage":
        return triage(store, action, operator=operator, correlation_id=correlation_id)
    if kind == "imaging_order":
        return order_imaging(store, action, operator=operator, correlation_id=correlation_id)
    raise NotRecorded(f"unknown action {kind!r}")


def _note(store: Store, task: dict, text: str) -> None:
    task["note"] = [*(task.get("note") or []), {"text": text[:500]}]
    store.fhir.update(task)


def _written(result) -> list[str]:
    return [f"{rtype}/{rid}" for rtype, rid in sorted(result.written)]


def _now(store: Store):
    return parse(simulator.clock(store)["now"])


# ---------- registration (step 1) ----------


def register(store: Store, action: dict, *, operator: str | None, correlation_id: str | None = None) -> list[str]:
    pid = action.get("patient_id")
    if not pid or store.fhir.read("Patient", pid) is None:
        raise NotRecorded(f"no Patient/{pid}")
    active = store.fhir.search("Encounter", patient=pid, status=list(simulator.ACTIVE))
    if any(e["class"]["code"] in ("EMER", "IMP") for e in active):
        raise NotRecorded("the patient is already registered in the ED or admitted")
    plan = store.modules["hospital_plan"]
    now = _now(store)
    visit_id = next((vid for vid, v in sorted(plan["visits"].items())
                     if v["patient"] == pid and v.get("expected")), None)
    if visit_id is not None:  # the ambulance pre-notified the ED: the planned visit starts now
        entry = plan["visits"][visit_id]
        delta = now - parse(entry["arrival"])
        for key in ("arrival", "triage", "seen", "decision", "end"):
            entry[key] = (parse(entry[key]) + delta).isoformat()
        entry["expected"] = False
    else:  # a walk-in: the EHR plans the visit's course as it plans any arrival (the scenarios' builders)
        visit_id = _walk_in(store, plan, pid, action, now, operator)
    entry = plan["visits"][visit_id]
    entry["registered_by"] = operator
    if action.get("mode") in ("ambulance", "walk-in", "police", "transfer"):
        entry["mode"] = action["mode"]
    store.modules["hospital_plan"] = plan
    result = simulator.apply_now(store, {"kind": "ed_arrival", "visit": visit_id}, operator=operator,
                                 correlation_id=correlation_id)
    if not result.written:
        raise NotRecorded(f"Encounter/{visit_id} already exists")
    return _written(result)


def _walk_in(store: Store, plan: dict, pid: str, action: dict, now, operator: str | None) -> str:
    from app.ehr.scenarios import _Injector
    from app.ehr.simulator import patient_info

    complaint = action.get("complaint") if action.get("complaint") in COMPLAINTS else "injury"
    ctx = {"name": "registration", "correlation_id": uuid.uuid4().hex, "operator": operator}
    rng = random.Random(f"hospital:{plan.get('seed')}:registration:{pid}:{now.isoformat()}")
    injector = _Injector(store, plan, now - timedelta(minutes=1), rng, ctx)
    p = patient_info(store.fhir, pid, parse(plan["generated_for"]).date())
    visit = injector._ed_visit(p, now, complaint, 3, admit_dx=None, treatment_min=rng.randint(60, 150))
    injector._add_visit(visit, p)
    plan["events"].sort(key=plan_order)
    return visit.id


# ---------- triage (step 2) ----------


def triage(store: Store, action: dict, *, operator: str | None, correlation_id: str | None = None) -> list[str]:
    plan = store.modules["hospital_plan"]
    visit_id = action.get("visit")
    entry = plan["visits"].get(visit_id or "")
    if entry is None:
        raise NotRecorded(f"no planned visit {visit_id}")
    ctas = int(action.get("ctas") or entry["ctas"])
    if not 1 <= ctas <= 5:
        raise NotRecorded(f"CTAS {ctas} is not a level")
    entry["ctas"] = ctas
    if isinstance(action.get("vitals"), dict):
        entry["vitals"] = {k: float(v) for k, v in action["vitals"].items() if k in C.VITALS}
    store.modules["hospital_plan"] = plan
    result = simulator.apply_now(store, {"kind": "ed_triage", "visit": visit_id}, operator=operator,
                                 correlation_id=correlation_id)
    if not result.written:
        raise NotRecorded("the visit is not waiting for triage")
    written = _written(result)
    for code in action.get("flags") or []:
        if code == "fall-risk":
            flag_id = f"flag-{visit_id}-fall-risk"
            if store.fhir.read("Flag", flag_id) is None:
                store.fhir.create({
                    "resourceType": "Flag", "id": flag_id, "status": "active",
                    "category": [C.concept("http://terminology.hl7.org/CodeSystem/flag-category", "clinical")],
                    "code": C.concept(C.FLAG_CODE, "fall-risk", "Fall risk"),
                    "subject": ref("Patient", entry["patient"]), "encounter": ref("Encounter", visit_id),
                    "period": {"start": fhir_datetime(_now(store))}})
                written.append(f"Flag/{flag_id}")
    return written


# ---------- imaging orders (step 3) ----------


def order_imaging(store: Store, action: dict, *, operator: str | None,
                  correlation_id: str | None = None) -> list[str]:
    plan = store.modules["hospital_plan"]
    visit_id = action.get("visit")
    entry = plan["visits"].get(visit_id or "")
    if entry is None or store.fhir.read("Encounter", visit_id) is None:
        raise NotRecorded(f"no visit {visit_id} in the EHR")
    from app.ehr.reference import IMAGING_ORDERS

    now = _now(store)
    requester = (store.staff.get(operator).practitioner_id if operator and store.staff.get(operator) else None)
    ids = []
    for item in action.get("orders") or []:
        if item.get("code") not in IMAGING_ORDERS:
            raise NotRecorded(f"unknown imaging order {item.get('code')!r}")
        oid = f"ord-{visit_id}-{item['code'].lower().replace('_', '-')}"
        if oid in plan["orders"]:
            continue
        order = OrderSpec(oid, entry["patient"], visit_id, "imaging", item["code"], now,
                          now + timedelta(minutes=int(item.get("minutes") or 45)),
                          priority=item.get("priority") or "routine", detail=item.get("protocol"),
                          requester=requester, conclusion=item.get("conclusion"))
        plan["orders"][oid] = M.plan_dict(order)
        plan["events"].append({"at": order.completed.isoformat(), "kind": "result", "order": oid})
        ids.append(oid)
    if not ids:
        raise NotRecorded("the orders are already placed")
    plan["events"].sort(key=plan_order)
    store.modules["hospital_plan"] = plan
    written = []
    for oid in ids:
        written += _written(simulator.apply_now(store, {"kind": "order", "order": oid}, operator=operator,
                                                correlation_id=correlation_id))
    return written
