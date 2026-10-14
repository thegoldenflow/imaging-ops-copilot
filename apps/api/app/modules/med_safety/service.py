"""Orders and medication safety (spec 7.2): the order review service, medication reconciliation and the queue.

Entry points (plain functions, safe to call again: an order is reviewed once, an
encounter has one reconciliation per kind, and every AI side effect goes through the
Tool Gateway with its idempotency key), for the API, the background loop and the
journey workflow (6.5, WP4c):

- `review_order(store, order_ref)`: rules first (rules.py), the result in the review
  queue; the AI commentary follows in the background (`comment`). The order's status
  never changes: the order belongs to the EHR, which the AI layer cannot write.
- `review_encounter_orders(store, encounter_id)`: every active order of an encounter.
- `reconcile(store, encounter_id, kind)`: admission or discharge reconciliation
  (reconcile.py), the AI's indication judgements and the draft record (`settle`).
- `intake(store)`: the sweep the API's hospital loop runs: new orders of the last 24
  hours (hospital clock), stays admitted in the last 48 hours (their admission
  reconciliation and every active order, older ones too), stays with discharge
  prescriptions. It catches the seeded data and whatever the day simulator adds.
- Decisions: `decide_review` (accept the order or request a change, with a reason;
  only a pharmacist clears a blocking flag), `handle_item` (accept / modify / reject
  each discrepancy, reason required), `sign` (the pharmacist, when every item is
  handled; then the physician's co-signature, through WP4's signing service, which
  refuses a MedRec record signed any other way).

The module reads the EHR through FhirGateway as its agents (`agent:order_review`,
`agent:medication_reconciliation`, the registry's data scope, hospital-wide, every
read audited); what a person sees is read as that person (6.3 rules apply).
"""

from __future__ import annotations

import hashlib
import threading
from datetime import datetime, timedelta

from sqlalchemy import text

from app.agents import approvals, registry
from app.agents.gateway import agent_actor
from app.agents.runtime import runtime
from app.core.audit import mrn_hash
from app.core.context import acting_as_system
from app.core.models import StaffUser
from app.core.store import Store, get_store
from app.ehr import access
from app.ehr.clock import hospital_now
from app.ehr.codes import MRN_SYSTEM, PRACTITIONER_ROLE, TASK_CODE, concept
from app.ehr.gateway import Actor, FhirGateway
from app.ehr.signing import MEDREC_WORKBENCH
from app.ehr.signing import signatures as document_signatures
from app.fhir.dt import fhir_datetime, parse, ref, ref_id
from app.modules.med_safety import facts as F
from app.modules.med_safety import knowledge, medrec_agent, review_agent, rules
from app.modules.med_safety import tools as T
from app.modules.med_safety.document import render
from app.modules.med_safety.models import (MedRecSession, OrderReview, get_review, get_session, review_key, reviews,
                                           session_key, sessions)
from app.modules.med_safety.reconcile import (RecItem, apply_judgements, compare_admission, compare_discharge,
                                              discharge_table, severity_of)

ORDER_REVIEW = "order_review"
MEDREC = "medication_reconciliation"
REVIEW_WINDOW = timedelta(hours=24)  # new orders: authored in the last 24 hours (hospital clock)
ADMISSION_WINDOW = timedelta(hours=48)  # stays admitted in the last 48 hours: reconciliation, all their orders
RECENT_DISCHARGE = timedelta(hours=72)
SERVICE_CATEGORIES = ["lab", "imaging"]
MEDICATION_REVIEWERS = ["pharmacist", "physician"]
SERVICE_REVIEWERS = ["physician"]
LAB_CODES = ["33914-3", "29463-7"]  # eGFR, body weight (the rules' facts)
SEVERITY_TO_PRIORITY = {"blocking": "stat", "major": "urgent", "moderate": "asap"}
_intake = {"key": None}
_lock = threading.Lock()


class DecisionProblem(ValueError):
    """The decision cannot be made in the record's current state (409)."""


class DecisionDenied(PermissionError):
    """This user may not make this decision (403)."""


def module_fhir(agent_id: str = ORDER_REVIEW) -> FhirGateway:
    """FhirGateway for the module itself: the agent's registry data scope, hospital-wide, audited under its actor."""
    return FhirGateway(agent_actor(registry.require_agent(agent_id), None), agent_id)


def reset_state() -> None:
    _intake["key"] = None


def _lock_key(name: str) -> int:
    return int(hashlib.sha256(f"med_safety:{name}".encode()).hexdigest()[:15], 16)


def _try_lock(store: Store, name: str) -> bool:
    """A transaction-scoped advisory lock on a name: two transactions never comment or settle the same thing at once
    (the API's background loop and a request); the second leaves it to the first."""
    return store.try_lock(_lock_key(name))


def _wait_lock(store: Store, name: str) -> None:
    """The same lock, waited for: the intake sweep runs once at a time, and a request that finds the background loop
    sweeping waits for it and then sees its reviews."""
    if not store.detached:
        store.conn().execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _lock_key(name)})


# ---------- facts from the EHR ----------


def _unit_of_encounter(encounter: dict | None) -> str | None:
    """The unit of the current (or last) location: the part of the location id before the first dash."""
    locations = (encounter or {}).get("location") or []
    current = next((loc for loc in reversed(locations) if loc.get("status") == "active"), None)
    chosen = current or (locations[-1] if locations else None)
    where = ref_id(chosen["location"]) if chosen else None
    return where.split("-", 1)[0] if where else None


def _results(fhir: FhirGateway, services: list[dict], patient_ids: list[str], since: datetime) -> dict:
    """ServiceRequest id -> (result ref, result time): lab Observation `obs-<order>`, imaging DiagnosticReport."""
    done = [s for s in services if s.get("status") == "completed"]
    out: dict[str, tuple[str, datetime | None]] = {}
    if not done:
        return out
    for o in fhir.search("Observation", ids=[f"obs-{s['id']}" for s in done]):
        out[o["id"].removeprefix("obs-")] = (f"Observation/{o['id']}", parse(o.get("effectiveDateTime")))
    for r in fhir.search("DiagnosticReport", patient=patient_ids, date_from=since):
        for based in r.get("basedOn") or []:
            out.setdefault(ref_id(based), (f"DiagnosticReport/{r['id']}", parse(r.get("effectiveDateTime"))))
    return out


def collect(fhir: FhirGateway, patient_ids: list[str], now: datetime) -> dict[str, F.PatientFacts]:
    """The rules' facts for these patients, in a few hospital-wide searches (one audit record each)."""
    pids = sorted(set(patient_ids))
    if not pids:
        return {}
    since = now - max(REVIEW_WINDOW, ADMISSION_WINDOW) - timedelta(hours=72)  # the intake's oldest orders, 72 h before
    with fhir.batch("medication safety: facts"):
        patients = {p["id"]: p for p in fhir.search("Patient", ids=pids)}
        allergies = [a for a in fhir.search("AllergyIntolerance", patient=pids) if F.allergy_active(a)]
        measurements = fhir.search("Observation", patient=pids, code=LAB_CODES)
        orders = fhir.search("MedicationRequest", patient=pids, status="active", category="order")
        home = fhir.search("MedicationStatement", patient=pids, status="active")
        services = fhir.search("ServiceRequest", patient=pids, category=SERVICE_CATEGORIES, date_from=since)
        results = _results(fhir, services, pids, since)
    by_patient: dict[str, dict[str, list]] = {p: {"a": [], "o": [], "m": [], "s": []} for p in pids}
    for a in allergies:
        by_patient[access.patient_of(a)]["a"].append(F.allergy_from(a))
    for o in measurements:
        by_patient[access.patient_of(o)]["o"].append(o)
    for m in orders:
        by_patient[access.patient_of(m)]["m"].append(F.med_from_request(m))
    for m in home:
        by_patient[access.patient_of(m)]["m"].append(F.med_from_statement(m))
    for s in services:
        by_patient[access.patient_of(s)]["s"].append(F.service_from(s, results.get(s["id"])))
    out = {}
    for pid in pids:
        p = patients.get(pid, {})
        obs = by_patient[pid]["o"]
        out[pid] = F.PatientFacts(
            patient_ref=f"Patient/{pid}", age=F.age_on(p.get("birthDate"), now.date()), sex=p.get("gender"),
            weight=F.latest([F.measurement(o) for o in obs if F._code(o.get("code"))[0] == "29463-7"], now),
            egfr=F.latest([F.measurement(o) for o in obs if F._code(o.get("code"))[0] == "33914-3"], now),
            allergies=tuple(by_patient[pid]["a"]), meds=tuple(by_patient[pid]["m"]),
            services=tuple(by_patient[pid]["s"]))
    return out


# ---------- order review ----------


def _summary(resource: dict) -> dict:
    if resource["resourceType"] == "MedicationRequest":
        med = F.med_from_request(resource)
        return {"name": med.name, "rxcui": med.rxcui, "drug_class": med.drug_class, "text": med.text,
                "dose": med.dose, "unit": med.unit, "per_day": med.per_day, "route": med.route, "prn": med.prn,
                "intent": resource.get("intent"), "status": resource.get("status"),
                "authored": resource.get("authoredOn"), "requester": ref_id(resource.get("requester")),
                "note": ((resource.get("note") or [{}])[0]).get("text")}
    svc = F.service_from(resource)
    return {"display": svc.display, "code": svc.code, "category": svc.category, "priority": resource.get("priority"),
            "status": resource.get("status"), "authored": resource.get("authoredOn"),
            "requester": ref_id(resource.get("requester"))}


def _status(review: OrderReview) -> str:
    if review.decision:
        return "resolved"
    concerns = (review.commentary or {}).get("concerns") or []
    return "pending" if review.findings or concerns else "clear"


def _severity(review: OrderReview) -> str:
    ai = [c["severity"] for c in (review.commentary or {}).get("concerns") or []]  # the AI never blocks
    return rules.most_severe([f["severity"] for f in review.findings] + ai)


def _new_review(store: Store, resource: dict, facts: F.PatientFacts, encounter: dict | None,
                now: datetime) -> OrderReview:
    medication = resource["resourceType"] == "MedicationRequest"
    order = F.med_from_request(resource) if medication else F.service_from(resource)
    findings = [f.as_dict() for f in rules.evaluate(order, facts)]
    review = OrderReview(
        id=store.next_id("ORV"), order_ref=f"{resource['resourceType']}/{resource['id']}",
        kind="medication" if medication else "service", patient_id=access.patient_of(resource),
        encounter_id=access.encounter_of(resource), unit_id=_unit_of_encounter(encounter), order=_summary(resource),
        authored_at=parse(resource.get("authoredOn")), reviewed_at=now,
        reviewer_roles=MEDICATION_REVIEWERS if medication else SERVICE_REVIEWERS, findings=findings,
        blocking=any(f["blocking"] for f in findings),
        commentary_status="pending" if medication or findings else "on_demand")
    return review.model_copy(update={"severity": _severity(review), "status": _status(review)})


def _review_orders(store: Store, fhir: FhirGateway, resources: list[dict], now: datetime) -> list[OrderReview]:
    table = reviews(store)
    fresh = [r for r in resources if review_key(f"{r['resourceType']}/{r['id']}") not in table]
    if not fresh:
        return []
    facts = collect(fhir, [access.patient_of(r) for r in fresh], now)
    encounters = {e["id"]: e for e in fhir.search("Encounter", ids=sorted({access.encounter_of(r) for r in fresh}
                                                                         - {None}))}
    out = []
    for resource in fresh:
        review = _new_review(store, resource, facts[access.patient_of(resource)],
                             encounters.get(access.encounter_of(resource)), now)
        table[review_key(review.order_ref)] = review
        out.append(review)
    return out


def review_order(store: Store, order_ref: str) -> OrderReview:
    """Review one MedicationRequest or ServiceRequest (rules now, AI commentary in the background). Idempotent."""
    existing = reviews(store).get(review_key(order_ref))
    if existing is not None:
        return existing
    rtype, _, rid = order_ref.partition("/")
    if rtype not in ("MedicationRequest", "ServiceRequest"):
        raise ValueError(f"{order_ref}: the order review takes a MedicationRequest or a ServiceRequest")
    fhir = module_fhir(ORDER_REVIEW)
    found = fhir.read(rtype, rid)
    if found is None:
        raise LookupError(f"{order_ref} not found")
    return _review_orders(store, fhir, [found.to_fhir()], hospital_now(store))[0]


def review_encounter_orders(store: Store, encounter_id: str) -> list[OrderReview]:
    """Every active medication order of the encounter (and active lab or imaging orders), reviewed once."""
    fhir = module_fhir(ORDER_REVIEW)
    resources = [m.to_fhir() for m in fhir.get_medications(encounter_id)
                 if m.status == "active" and m.intent == "order"]
    resources += [s.to_fhir() for s in fhir.get_orders(encounter_id) if s.status == "active"
                  and F.service_from(s.to_fhir()).category in SERVICE_CATEGORIES]
    _review_orders(store, fhir, resources, hospital_now(store))
    table = reviews(store)
    return [table[review_key(f"{r['resourceType']}/{r['id']}")] for r in resources]


# ---------- the AI commentary ----------


def comment(store: Store, review_id: str) -> OrderReview:
    """The order_review agent's commentary for one review, as the agent itself; evidence resolved before display."""
    review = get_review(store, review_id)
    if review is None:
        raise LookupError(f"No order review {review_id}")
    if review.commentary_status not in ("pending", "on_demand") or review.encounter_id is None:
        return review
    if not _try_lock(store, review.id):  # being commented elsewhere
        return review
    payload = review.model_dump(mode="json")
    try:
        with acting_as_system(), runtime.start(review_agent.AGENT_ID, user=None, encounter_id=review.encounter_id,
                                               context_id=review.id) as run:
            result = review_agent.comment(run, payload)
    except (registry.AgentNotDeployable, registry.UnregisteredAgent) as e:  # prod mode before the eval passed
        review = review.model_copy(update={"commentary_status": "refused",
                                           "commentary": {"reason": str(e), "concerns": []}})
        reviews(store)[review_key(review.order_ref)] = review
        return review
    commentary: dict = {"reason": result.reason, "run_id": result.run_id, "evaluated": result.evaluated,
                        "model_status": result.model_status, "attempts": result.attempts, "problems": result.problems,
                        "dropped_refs": result.dropped_refs, "guidelines": result.guidelines, "concerns": [],
                        "alternatives": [], "guideline_refs": []}
    if result.status == "done" and result.output is not None:
        out = result.output
        refs = [r for c in out["concerns"] for r in c["evidence_refs"]]
        resolved = module_fhir(ORDER_REVIEW).resolve_refs(refs) if refs else set()
        unresolved = sorted({r for r in refs if r not in resolved})
        for c in out["concerns"]:
            c["evidence_refs"] = [r for r in c["evidence_refs"] if r in resolved]
        commentary.update(out, unresolved_refs=unresolved,
                          guideline_refs=[g for g in out["guideline_refs"] if knowledge.resolve(g)])
    review = review.model_copy(update={"commentary_status": result.status, "commentary": commentary,
                                       "review_task_id": result.review_task_id or review.review_task_id})
    review = review.model_copy(update={"severity": _severity(review), "status": _status(review)})
    reviews(store)[review_key(review.order_ref)] = review
    return review


def pending_commentary(store: Store) -> list[OrderReview]:
    """Reviews waiting for their commentary: blocking and most severe first, then the oldest order."""
    found = reviews(store).find_by("commentary_status", "pending")
    return sorted(found, key=lambda r: (not r.blocking, -rules.SEVERITY_RANK[r.severity],
                                        r.authored_at or datetime.min))


# ---------- medication reconciliation ----------


def _conditions_for(conditions: list[dict], encounter_id: str) -> list[F.ConditionFact]:
    """The diagnosis list: active problems and this encounter's diagnoses."""
    return [F.condition_from(c) for c in conditions if F.condition_from(c).active
            or access.encounter_of(c) == encounter_id]


def _session_inputs(fhir: FhirGateway, encounters: list[dict], orders_as_of: datetime | None = None) -> dict[str, dict]:
    """Per encounter: home medications, orders (those written by `orders_as_of`, when given), discharge prescriptions
    and the diagnosis list (bulk reads)."""
    if not encounters:
        return {}
    enc_ids = [e["id"] for e in encounters]
    pids = sorted({access.patient_of(e) for e in encounters})
    with fhir.batch("medication reconciliation: inputs"):
        home = fhir.search("MedicationStatement", patient=pids, status="active")
        requests = fhir.search("MedicationRequest", encounter=enc_ids)
        conditions = fhir.search("Condition", patient=pids)
    out = {}
    for e in encounters:
        pid = access.patient_of(e)
        mine = [r for r in requests if access.encounter_of(r) == e["id"] and not (
            orders_as_of and r.get("intent") == "order" and (parse(r.get("authoredOn")) or orders_as_of) > orders_as_of)]
        out[e["id"]] = {
            "patient_id": pid, "unit_id": _unit_of_encounter(e),
            "home": [F.med_from_statement(s) for s in home if access.patient_of(s) == pid],
            "orders": [F.med_from_request(r) for r in mine if r.get("intent") == "order"],
            "plans": [F.med_from_request(r) for r in mine if r.get("intent") == "plan"],
            "conditions": _conditions_for([c for c in conditions if access.patient_of(c) == pid], e["id"])}
    return out


def _existing_draft(fhir: FhirGateway, encounter_id: str, kind: str) -> str | None:
    """A draft record already on the encounter (e.g. WP4's demo draft) becomes this reconciliation's document."""
    for doc in fhir.search("DocumentReference", encounter=encounter_id, code="medrec", status="preliminary"):
        kinds = {c.get("code") for cc in doc.get("category") or [] for c in cc.get("coding") or []
                 if c.get("system") == T.MEDREC_KIND}
        if not kinds or kind in kinds:
            return doc["id"]
    return None


def _new_session(store: Store, fhir: FhirGateway, encounter_id: str, kind: str, inputs: dict,
                 now: datetime) -> MedRecSession:
    if kind == "admission":
        items = compare_admission(inputs["home"], inputs["orders"], inputs["conditions"])
    else:
        items = compare_discharge(inputs["home"], inputs["orders"], inputs["plans"], inputs["conditions"])
    return MedRecSession(id=store.next_id("MR"), encounter_id=encounter_id, patient_id=inputs["patient_id"],
                         kind=kind, unit_id=inputs["unit_id"], created_at=now, updated_at=now,
                         severity=severity_of(items), items=[i.model_dump(mode="json") for i in items],
                         document_id=_existing_draft(fhir, encounter_id, kind))


def reconcile(store: Store, encounter_id: str, kind: str = "admission", *, settle_now: bool = False,
              orders_as_of: datetime | None = None) -> MedRecSession:
    """The encounter's admission or discharge reconciliation (one per kind; a repeated call returns it). The journey
    passes `orders_as_of`, the order review's sign-off: the admission orders are those written by then."""
    if kind not in ("admission", "discharge"):
        raise ValueError("kind is admission or discharge")
    table = sessions(store)
    existing = table.get(session_key(encounter_id, kind))
    if existing is None:  # another transaction may be opening it right now: wait for it, then look again
        existing = _fresh_session(store, encounter_id, kind)
    if existing is None:
        fhir = module_fhir(MEDREC)
        found = fhir.read("Encounter", encounter_id)
        if found is None:
            raise LookupError(f"Encounter/{encounter_id} not found")
        inputs = _session_inputs(fhir, [found.to_fhir()], orders_as_of)[encounter_id]
        if kind == "discharge" and not inputs["plans"]:
            raise DecisionProblem(f"Encounter/{encounter_id} has no discharge prescriptions yet")
        existing = _new_session(store, fhir, encounter_id, kind, inputs, hospital_now(store))
        table[session_key(encounter_id, kind)] = existing
    return settle(store, existing.id) if settle_now else existing


def _fresh_session(store: Store, encounter_id: str, kind: str) -> MedRecSession | None:
    """The reconciliation as committed now, read under its lock (held to the end of the transaction). The background
    intake and a request (or two requests) can open the same one at once; both write it under the same key, so the
    second must find the first's session instead of replacing it and the id the first caller got."""
    key = session_key(encounter_id, kind)
    _wait_lock(store, f"medrec:{key}")
    if not store.detached:  # a detached store's table is the data itself: nothing to re-read
        sessions(store).forget(key)
    return sessions(store).get(key)


def _items(session: MedRecSession) -> list[RecItem]:
    return [RecItem.model_validate(i) for i in session.items]


def _save_session(store: Store, session: MedRecSession, items: list[RecItem] | None = None, **update) -> MedRecSession:
    if items is not None:
        update["items"] = [i.model_dump(mode="json") for i in items]
        update["severity"] = severity_of(items)
    session = session.model_copy(update={**update, "updated_at": hospital_now(store)})
    sessions(store)[session_key(session.encounter_id, session.kind)] = session
    return session


def settle(store: Store, session_id: str) -> MedRecSession:
    """The AI step of a reconciliation: the indication judgements and the draft record (agent
    medication_reconciliation, as itself). Without the AI (no consent, prod mode before its eval passed, the model
    down) new medications without a named indication stay discrepancies and the module writes the draft itself."""
    session = get_session(store, session_id)
    if session is None:
        raise LookupError(f"No reconciliation {session_id}")
    if session.ai_status != "pending" or not _try_lock(store, session.id):
        return session
    items = _items(session)
    result = None
    reason = None
    try:
        with acting_as_system(), runtime.start(medrec_agent.AGENT_ID, user=None, encounter_id=session.encounter_id,
                                               context_id=session.id) as run:
            result = medrec_agent.judge(run, session.model_dump(mode="json"), items)
    except (registry.AgentNotDeployable, registry.UnregisteredAgent) as e:
        reason = str(e)
    if result is not None and result.status == "done":
        return _save_session(store, session, result.items, ai_status="done", ai_detail=result.detail(),
                             document_id=result.document_id or session.document_id)
    status = "refused" if result is None or result.status == "refused" else "unavailable"
    reason = reason or (result.reason if result else None) or "the AI is unavailable"
    items = apply_judgements(items, {}, medrec_agent.DEFAULT_THRESHOLD, unavailable=reason.split(":")[0])
    session = _save_session(store, session, items, ai_status=status,
                            ai_detail={**(result.detail() if result else {}), "reason": reason})
    return _write_document(store, session, items, module_fhir(MEDREC))


def _write_document(store: Store, session: MedRecSession, items: list[RecItem], fhir: FhirGateway, *,
                    final: bool = False) -> MedRecSession:
    """Create or update the reconciliation's draft record (as `fhir`'s actor: the module, or the pharmacist)."""
    text, data = render(session.model_dump(mode="json"), items, final=final)
    when = fhir_datetime(hospital_now(store))
    refs = list(dict.fromkeys(r for i in items for r in i.evidence_refs))[:200]
    if session.document_id:
        found = fhir.read("DocumentReference", session.document_id)
        doc = found.to_fhir() if found is not None else None
    else:
        doc = None
    fresh = T.medrec_document(session.patient_id, session.encounter_id, session.kind, text, data, when=when,
                              source_refs=refs)
    if doc is not None:
        doc.update({k: v for k, v in fresh.items() if k not in ("id", "extension", "author")})
        stored = fhir.update(doc).to_fhir()
    else:
        stored = fhir.create(fresh).to_fhir()
    return _save_session(store, session, document_id=stored["id"])


def pending_settlement(store: Store) -> list[MedRecSession]:
    return sorted(sessions(store).find_by("ai_status", "pending"), key=lambda s: s.created_at)


# ---------- intake (the background sweep) ----------


def intake(store: Store, *, force: bool = False) -> dict[str, int]:
    """Review new orders and open reconciliations for new admissions and discharge prescriptions. Cheap when nothing
    changed (keyed on the store's change counter and the hospital clock)."""
    now = hospital_now(store)
    key = f"{store.version}:{now.isoformat()}"
    with _lock:
        if not force and _intake["key"] == key:
            return {}
    _wait_lock(store, "intake")  # another transaction sweeping: wait, then find its reviews and add only what is new
    fhir = module_fhir(ORDER_REVIEW)
    with fhir.batch("medication safety intake"):
        meds = fhir.search("MedicationRequest", status="active", category="order", date_from=now - REVIEW_WINDOW)
        tests = fhir.search("ServiceRequest", status="active", category=SERVICE_CATEGORIES,
                            date_from=now - REVIEW_WINDOW)
        # and every active order of the stays the admission reconciliation covers, older ones too (the journey's
        # orderReview reviews the same; a stay started before the data was seeded has no journey)
        stays = [e["id"] for e in fhir.search("Encounter", cls="IMP", status="in-progress",
                                               date_from=now - ADMISSION_WINDOW)]
        if stays:
            meds += fhir.search("MedicationRequest", encounter=stays, status="active", category="order")
            tests += fhir.search("ServiceRequest", encounter=stays, status="active", category=SERVICE_CATEGORIES)
    orders = list({(r["resourceType"], r["id"]): r for r in meds + tests}.values())
    created = _review_orders(store, fhir, orders, now)
    counts = {"reviews": len(created), "admission": 0, "discharge": 0}
    medrec = module_fhir(MEDREC)
    table = sessions(store)
    with medrec.batch("medication reconciliation intake"):
        admitted = medrec.search("Encounter", cls="IMP", status="in-progress", date_from=now - ADMISSION_WINDOW)
        plans = medrec.search("MedicationRequest", status="active", category="plan")
        leaving_ids = sorted({access.encounter_of(p) for p in plans} - {None})
        leaving = medrec.search("Encounter", ids=leaving_ids) if leaving_ids else []
    todo = [(e, "admission") for e in admitted if session_key(e["id"], "admission") not in table]
    todo += [(e, "discharge") for e in leaving if session_key(e["id"], "discharge") not in table and (
        e.get("status") == "in-progress" or (parse((e.get("period") or {}).get("end")) or now) >= now - RECENT_DISCHARGE)]
    # a request may be opening one of these right now: take its lock, look again, and leave those to it
    todo = [(e, kind) for e, kind in todo if _fresh_session(store, e["id"], kind) is None]
    inputs =_session_inputs(medrec, list({e["id"]: e for e, _ in todo}.values()))
    for encounter, kind in todo:
        if kind == "discharge" and not inputs[encounter["id"]]["plans"]:
            continue
        session = _new_session(store, medrec, encounter["id"], kind, inputs[encounter["id"]], now)
        table[session_key(encounter["id"], kind)] = session
        counts[kind] += 1
    with _lock:
        _intake["key"] = key
    return counts


def intake_step(store: Store) -> None:
    """Background step of the API's hospital loop (after the event drain)."""
    from app.modules.med_safety import journey

    intake(store)
    journey.recheck_signoffs(store)  # a journey sign-off written while the last decision was being saved


def ai_step(store: Store, limit: int = 2) -> None:
    """Background step: the most urgent commentary and reconciliation judgements still waiting."""
    for review in pending_commentary(store)[:limit]:
        comment(store, review.id)
    for session in pending_settlement(store)[:limit]:
        settle(store, session.id)


# ---------- people's decisions ----------


def _in_scope(user: StaffUser, patient_id: str) -> bool:
    return FhirGateway(Actor.of(user), ORDER_REVIEW).is_in_scope(patient_id)


def _patient_mrn(fhir: FhirGateway, patient_id: str) -> str | None:
    found = fhir.read("Patient", patient_id)
    return next((i.value for i in (found.identifier or []) if i.system == MRN_SYSTEM), None) if found else None


def _audit(store: Store, user: StaffUser, *, action: str, event_type: str, resource_type: str, resource_id: str,
           reason: str, patient_id: str | None, encounter_id: str | None, module: str, source_ip: str | None) -> None:
    mrn = _patient_mrn(module_fhir(ORDER_REVIEW), patient_id) if patient_id else None
    store.audit.record(user_id=user.id, user_name=user.name, role=str(user.role), action=action, event_type=event_type,
                       resource_type=resource_type, resource_id=resource_id, outcome="allowed", source_ip=source_ip,
                       reason=reason[:500], patient_mrn_hash=mrn_hash(mrn) if mrn else None,
                       encounter_id=encounter_id, module=module)


def can_decide(user: StaffUser, review: OrderReview) -> str | None:
    """Why this user may not decide this review (None: they may)."""
    role = str(user.role)
    if role not in review.reviewer_roles:
        return ("Laboratory and imaging orders are reviewed by physicians" if review.kind == "service"
                else f"Order reviews are decided by {' or '.join(review.reviewer_roles)}")
    if role == "physician":
        if review.blocking:
            return "A blocking flag (allergy) must be handled by a pharmacist"
        if not _in_scope(user, review.patient_id):
            return "This patient is outside your units"
    return None


def _change_task(store: Store, agent_id: str, encounter_id: str, description: str, priority: str,
                 context_id: str) -> str | None:
    """A prescriber Task asking for a change (the order is the EHR's; the AI layer never edits it): written by the
    agent acting as itself after the person's recorded decision, or by the module when the agent is refused."""
    args = {"encounter_id": encounter_id, "code": "order-change-request", "description": description[:1000],
            "performer_role": "physician", "priority": priority}
    try:
        with acting_as_system(), runtime.start(agent_id, user=None, encounter_id=encounter_id,
                                               context_id=context_id) as run:
            result = run.tool("createTask", args)
            run.finish(outcome="completed" if result.ok else "needs_human")
        return (result.output or {}).get("task_id")
    except (registry.AgentNotDeployable, registry.UnregisteredAgent):
        fhir = module_fhir(agent_id)
        encounter = fhir.read("Encounter", encounter_id).to_fhir()
        now = fhir_datetime(hospital_now(store))
        task = {"resourceType": "Task", "status": "requested", "intent": "order", "priority": priority,
                "code": concept(TASK_CODE, "order-change-request"), "description": description[:1000],
                "for": encounter["subject"], "encounter": ref("Encounter", encounter_id), "authoredOn": now,
                "lastModified": now, "performerType": [concept(PRACTITIONER_ROLE, access.ROLE_CODE["physician"])]}
        return fhir.create(task).id


def decide_review(store: Store, user: StaffUser, review_id: str, decision: str, reason: str, *,
                  source_ip: str | None = None) -> OrderReview:
    """accept_order (the order stands; the flags are acknowledged with a reason) or request_change (a Task asks the
    prescriber to change the order). Recorded on the review's ai-review Task (an `approve` audit event) or, without
    AI commentary, as an `approve` audit event on the review."""
    review = get_review(store, review_id)
    if review is None:
        raise LookupError(f"No order review {review_id}")
    if decision not in ("accept_order", "request_change"):
        raise DecisionProblem("Decide accept_order or request_change")
    if why := can_decide(user, review):
        raise DecisionDenied(why)
    if review.decision:
        raise DecisionProblem(f"{review_id} is already decided")
    reason = " ".join((reason or "").split())
    if len(reason) < 5:
        raise DecisionProblem("Give a reason (at least 5 characters)")
    concerns = (review.commentary or {}).get("concerns") or []
    note = f"{decision}: {reason}"
    if review.review_task_id and approvals.is_open(review.review_task_id):
        took_ai = (decision == "request_change") if concerns else (decision == "accept_order")
        approvals.decide(user, review.review_task_id, "approve" if took_ai else "reject", note, source_ip=source_ip)
    else:
        _audit(store, user, action="accept" if decision == "accept_order" else "request_change", event_type="approve",
               resource_type="OrderReview", resource_id=review.id, reason=note, patient_id=review.patient_id,
               encounter_id=review.encounter_id, module=ORDER_REVIEW, source_ip=source_ip)
    task_id = None
    if decision == "request_change" and review.encounter_id:
        what = review.order.get("name") or review.order.get("display") or review.order_ref
        task_id = _change_task(store, ORDER_REVIEW, review.encounter_id,
                               f"Order review {review.id}: please change {what} ({review.order_ref}). "
                               f"{str(user.role).capitalize()} {user.name}: {reason}",
                               SEVERITY_TO_PRIORITY.get(review.severity, "routine"), review.id)
    now = hospital_now(store)
    commentary_status = "skipped" if review.commentary_status in ("pending", "on_demand") else review.commentary_status
    review = review.model_copy(update={
        "decision": {"decision": decision, "reason": reason, "by": user.id, "name": user.name, "role": str(user.role),
                     "at": now.isoformat(), "change_task_id": task_id},
        "commentary_status": commentary_status, "status": "resolved"})
    reviews(store)[review_key(review.order_ref)] = review
    from app.modules.med_safety import journey

    journey.confirm_signoff(store, user, review, source_ip=source_ip)
    return review


def _medrec_session(store: Store, session_id: str) -> MedRecSession:
    session = get_session(store, session_id)
    if session is None:
        raise LookupError(f"No reconciliation {session_id}")
    return session


def handle_item(store: Store, user: StaffUser, session_id: str, item_id: str, action: str, reason: str,
                change: str | None = None, *, source_ip: str | None = None) -> MedRecSession:
    """The pharmacist's handling of one discrepancy: accept (intended), modify (the list must change: say how) or
    reject (not a real discrepancy); a reason every time."""
    session = _medrec_session(store, session_id)
    if str(user.role) != "pharmacist":
        raise DecisionDenied("Discrepancies are handled by the pharmacist")
    if session.status != "open":
        raise DecisionProblem(f"{session_id} is already signed")
    if session.ai_status == "pending":
        session = settle(store, session_id)
    if action not in ("accept", "modify", "reject"):
        raise DecisionProblem("The action is accept, modify or reject")
    reason = " ".join((reason or "").split())
    change = " ".join((change or "").split()) or None
    if len(reason) < 5:
        raise DecisionProblem("Give a reason (at least 5 characters)")
    if action == "modify" and (change is None or len(change) < 5):
        raise DecisionProblem("Say what should change (at least 5 characters)")
    items = _items(session)
    item = next((i for i in items if i.id == item_id), None)
    if item is None:
        raise LookupError(f"No item {item_id} in {session_id}")
    if not item.discrepancy:
        raise DecisionProblem(f"{item_id} is not a discrepancy")
    now = hospital_now(store)
    item.action, item.reason, item.change = action, reason, change if action == "modify" else None
    item.by, item.by_name, item.at = user.id, user.name, now.isoformat()
    _audit(store, user, action=f"medrec_{action}", event_type="write", resource_type="MedRec", resource_id=session.id,
           reason=f"{item_id} ({item.type}): {reason}" + (f"; change: {change}" if action == "modify" else ""),
           patient_id=session.patient_id, encounter_id=session.encounter_id, module=MEDREC, source_ip=source_ip)
    return _save_session(store, session, items)


def accept_hospital_use(store: Store, user: StaffUser, session_id: str, reason: str, *,
                        source_ip: str | None = None) -> MedRecSession:
    """Accept every unhandled hospital-use omission of a discharge reconciliation with one reason (each item is
    recorded and audited as its own decision)."""
    session = _medrec_session(store, session_id)
    for item in _items(session):
        if item.hospital_use and item.discrepancy and item.action is None:
            session = handle_item(store, user, session_id, item.id, "accept", reason, source_ip=source_ip)
    return session


def unhandled(session: MedRecSession) -> list[str]:
    return [i.id for i in _items(session) if not i.handled or i.indication == "pending"]


def sign(store: Store, user: StaffUser, session_id: str, request=None) -> MedRecSession:
    """The pharmacist signs a reconciliation whose every item is handled (the record is updated with the decisions,
    then signed through the signing service: a `sign` audit event; it stays preliminary until the co-signature);
    then the physician co-signs (final; the authenticator is the pharmacist). Modify decisions become Tasks for the
    prescriber. This is the only way a MedRec record becomes final: the signing service refuses it without
    `flow=MEDREC_WORKBENCH`."""
    session = _medrec_session(store, session_id)
    role = str(user.role)
    fhir = FhirGateway(Actor.of(user, request), MEDREC)
    if role == "pharmacist":
        if session.status != "open":
            raise DecisionProblem(f"{session_id} is already signed by the pharmacist")
        if session.ai_status == "pending":
            session = settle(store, session_id)
        if open_items := unhandled(session):
            raise DecisionProblem(f"Handle every discrepancy first ({len(open_items)} left: {', '.join(open_items)})")
        items = _items(session)
        now = hospital_now(store)
        signatures = [*session.signatures, {"role": role, "by": user.id, "name": user.name, "at": now.isoformat()}]
        session = _save_session(store, session, signatures=signatures)
        session = _write_document(store, session, items, fhir, final=True)
        fhir.sign_document(session.document_id, flow=MEDREC_WORKBENCH)
        tasks = []
        for item in items:
            if item.action == "modify":
                med = item.order or item.home or item.inpatient
                tasks.append(_change_task(
                    store, MEDREC, session.encounter_id,
                    f"Medication reconciliation {session.id} ({session.kind}): {med.name if med else item.id}: "
                    f"{item.change} (pharmacist {user.name}: {item.reason})",
                    "urgent" if item.severity == "major" else "routine", f"{session.id}:{item.id}"))
        table = discharge_table(items) if session.kind == "discharge" else None
        return _save_session(store, session, status="pharmacist_signed", change_tasks=[t for t in tasks if t],
                             discharge_table=table)
    if role == "physician":
        if session.status != "pharmacist_signed":
            raise DecisionProblem("The physician co-signs after the pharmacist has signed")
        if not _in_scope(user, session.patient_id):
            raise DecisionDenied("This patient is outside your units")
        fhir.sign_document(session.document_id, flow=MEDREC_WORKBENCH)
        now = hospital_now(store)
        signatures = [*session.signatures, {"role": role, "by": user.id, "name": user.name, "at": now.isoformat()}]
        return _save_session(store, session, status="final", signatures=signatures)
    raise DecisionDenied("Reconciliations are signed by the pharmacist and co-signed by a physician")


# ---------- views ----------


def _wait_minutes(since: datetime | None, now: datetime) -> int | None:
    return max(0, int((now - since).total_seconds() // 60)) if since else None


def _names(fhir: FhirGateway, patient_ids: list[str]) -> dict[str, dict]:
    if not patient_ids:
        return {}
    out = {}
    for p in fhir.search("Patient", ids=sorted(set(patient_ids))):
        name = next((n.get("text") for n in p.get("name") or [] if n.get("use") in (None, "official")), None)
        mrn = next((i.get("value") for i in p.get("identifier") or [] if i.get("system") == MRN_SYSTEM), None)
        out[p["id"]] = {"mrn": mrn, "name": name}
    return out


def _review_item(r: OrderReview, now: datetime) -> dict:
    top = r.findings[0] if r.findings else None
    concerns = (r.commentary or {}).get("concerns") or []
    sources = [f"rule:{f['rule_name']}" for f in r.findings] + (["ai"] if concerns else [])
    what = r.order.get("name") or r.order.get("display") or r.order_ref
    more = f" (+{len(r.findings) - 1} more)" if len(r.findings) > 1 else ""
    title = f"{top['title']}{more}" if top else (
        f"{what}: {concerns[0]['type'].replace('_', ' ')} (AI)" if concerns else f"{what}: no flags")
    return {"kind": "order_review", "id": r.id, "severity": r.severity, "blocking": r.blocking, "status": r.status,
            "title": title, "detail": r.order.get("text") or r.order.get("category"), "patient_id": r.patient_id,
            "encounter_id": r.encounter_id, "unit_id": r.unit_id, "order_ref": r.order_ref, "order_kind": r.kind,
            "since": r.authored_at, "wait_minutes": _wait_minutes(r.authored_at, now), "sources": sources,
            "commentary_status": r.commentary_status,
            "evaluated": (r.commentary or {}).get("evaluated", True)}


def _session_item(s: MedRecSession, now: datetime) -> dict:
    items = _items(s)
    left = [i for i in items if i.discrepancy and i.action is None]
    pending = [i for i in items if i.indication == "pending"]
    state = {"open": f"{len(left)} of {sum(i.discrepancy for i in items)} discrepancies to handle"
             + (" (AI judging new medications)" if pending else ""),
             "pharmacist_signed": "signed by the pharmacist; physician co-signature due",
             "final": "signed and co-signed"}[s.status]
    sources = sorted({"ai" if i.source == "ai" else f"rule:{i.type}" for i in items if i.discrepancy})
    return {"kind": "medrec", "id": s.id, "severity": s.severity if s.status == "open" else "none", "blocking": False,
            "status": "resolved" if s.status == "final" else ("pending" if s.status == "open" else "cosign"),
            "title": f"{s.kind.capitalize()} reconciliation: {state}", "detail": None, "patient_id": s.patient_id,
            "encounter_id": s.encounter_id, "unit_id": s.unit_id, "since": s.created_at,
            "wait_minutes": _wait_minutes(s.created_at, now), "sources": sources, "medrec_status": s.status,
            "commentary_status": s.ai_status, "evaluated": (s.ai_detail or {}).get("evaluated", True)}


QUEUE_ORDER = {"pending": 0, "cosign": 1, "clear": 2, "resolved": 3}


def queue(store: Store, user: StaffUser, *, show: str = "open") -> dict:
    """The workbench queue for the user's role: blocking first, then severity, then the longest wait.
    show: open (needs a person), all, done."""
    intake(store)
    now = hospital_now(store)
    role = str(user.role)
    if role not in ("pharmacist", "physician"):
        raise DecisionDenied("The medication safety workbench is for pharmacists and physicians")
    units = set(user.unit_ids) if role == "physician" else None
    rows = []
    for r in reviews(store).values():
        if role not in r.reviewer_roles or (units is not None and r.unit_id not in units):
            continue
        rows.append(_review_item(r, now))
    for s in sessions(store).values():
        if units is not None and s.unit_id not in units:
            continue
        rows.append(_session_item(s, now))
    if show == "open":  # what needs this person: flags and open reconciliations; for a physician also co-signatures
        wanted = {"pending", "cosign"} if role == "physician" else {"pending"}
        rows = [r for r in rows if r["status"] in wanted]
    elif show == "done":
        rows = [r for r in rows if r["status"] in ("resolved", "cosign")]
    rows.sort(key=lambda r: (QUEUE_ORDER.get(r["status"], 9), not r["blocking"], -rules.SEVERITY_RANK[r["severity"]],
                             -(r["wait_minutes"] or 0)))
    names = _names(module_fhir(ORDER_REVIEW), [r["patient_id"] for r in rows])
    for r in rows:
        r.update(names.get(r["patient_id"], {}))
    counts = {"blocking": sum(r["blocking"] for r in rows), "pending": sum(r["status"] == "pending" for r in rows),
              "cosign": sum(r["status"] == "cosign" for r in rows)}
    return {"now": now, "role": role, "items": rows, "counts": counts}


def _resource_label(resource: dict) -> str:
    rtype = resource.get("resourceType")
    if rtype in ("MedicationRequest", "MedicationStatement"):
        med = F.med_from_request(resource) if rtype == "MedicationRequest" else F.med_from_statement(resource)
        where = {"order": "order", "discharge": "discharge prescription", "home": "home medication"}[med.source]
        return f"{med.name} {med.text} ({where}, {resource.get('status')})"
    if rtype == "Observation":
        code, display = F._code(resource.get("code"))
        quantity = resource.get("valueQuantity") or {}
        return f"{display or code}: {quantity.get('value')} {quantity.get('unit') or ''} " \
               f"({(parse(resource.get('effectiveDateTime')) or datetime.min):%Y-%m-%d %H:%M})".strip()
    if rtype == "AllergyIntolerance":
        a = F.allergy_from(resource)
        return f"Allergy: {a.display} ({a.drug_class or 'no class'}; criticality {a.criticality}; {a.reaction})"
    if rtype == "Condition":
        c = F.condition_from(resource)
        return f"{c.display} ({c.category or 'condition'}, {'active' if c.active else 'resolved'})"
    if rtype == "ServiceRequest":
        s = F.service_from(resource)
        return f"{s.display} ordered {(s.authored or datetime.min):%Y-%m-%d %H:%M} ({s.status})"
    if rtype == "DiagnosticReport":
        return f"{F._code(resource.get('code'))[1]}: {resource.get('conclusion') or ''}".strip()
    if rtype == "Patient":
        return f"Patient: {resource.get('gender')}, born {resource.get('birthDate')}"
    return f"{rtype}/{resource.get('id')}"


def evidence(user: StaffUser, refs: list[str], request=None) -> list[dict]:
    """'View evidence': each reference read as the user (6.3 applies) or a guideline section."""
    fhir = FhirGateway(Actor.of(user, request), ORDER_REVIEW)
    out = []
    for value in dict.fromkeys(refs):
        rtype, _, rid = value.partition("/")
        if "#" in value and (section := knowledge.resolve(value)):
            out.append({"ref": value, "kind": "guideline", "label": section.title, "text": section.text,
                        "source": section.source, "document": section.document})
            continue
        try:
            found = fhir.read(rtype, rid) if rtype in access.ALL_TYPES and rid else None
        except PermissionError as e:  # FhirAccessDenied / BreakGlassRequired (already audited)
            out.append({"ref": value, "kind": "denied", "label": str(e)})
            continue
        out.append({"ref": value, "kind": "fhir" if found else "missing",
                    "label": _resource_label(found.to_fhir()) if found else "not found"})
    return out


def patient_header(user: StaffUser, patient_id: str, encounter_id: str | None, request=None) -> dict:
    """The banner over the order and the discrepancy table, read as the user."""
    fhir = FhirGateway(Actor.of(user, request), ORDER_REVIEW)
    now = hospital_now(get_store())
    patient = fhir.read("Patient", patient_id).to_fhir()
    out: dict = {"patient_id": patient_id,
                 "mrn": next((i.get("value") for i in patient.get("identifier") or [] if i.get("system") == MRN_SYSTEM),
                             None),
                 "name": next((n.get("text") for n in patient.get("name") or [] if n.get("use") in (None, "official")),
                              None),
                 "gender": patient.get("gender"), "age": F.age_on(patient.get("birthDate"), now.date())}
    if encounter_id:
        enc = fhir.read("Encounter", encounter_id).to_fhir()
        out.update(encounter_id=encounter_id, unit_id=_unit_of_encounter(enc),
                   bed=ref_id(next((loc["location"] for loc in reversed(enc.get("location") or [])
                                    if loc.get("status") == "active"), None)),
                   reason=((enc.get("reasonCode") or [{}])[0]).get("text") or F._code((enc.get("reasonCode")
                                                                                       or [{}])[0])[1],
                   admitted=(enc.get("period") or {}).get("start"), encounter_status=enc.get("status"))
    obs = [o.to_fhir() for o in fhir.get_patient_resources("Observation", patient_id, codes=LAB_CODES + ["6301-6"])]
    for key, code in (("egfr", "33914-3"), ("weight", "29463-7"), ("inr", "6301-6")):
        m = F.latest([F.measurement(o) for o in obs if F._code(o.get("code"))[0] == code], now)
        out[key] = {"value": m.value, "unit": m.unit, "at": m.at, "ref": m.ref} if m else None
    out["allergies"] = [{"ref": f"AllergyIntolerance/{a['id']}", **{k: v for k, v in vars(F.allergy_from(a)).items()
                                                                  if k != "ref"}}
                        for a in (x.to_fhir() for x in fhir.get_patient_resources("AllergyIntolerance", patient_id))
                        if F.allergy_active(a)]
    conditions = [c.to_fhir() for c in fhir.get_patient_resources("Condition", patient_id)]
    shown: dict[str, dict] = {}  # one line per diagnosis (the ED visit and the stay may both record it)
    for c in conditions:
        fact = F.condition_from(c)
        if (fact.active or (encounter_id and access.encounter_of(c) == encounter_id)) and fact.display not in shown:
            shown[fact.display] = {"ref": f"Condition/{c['id']}", "display": fact.display, "category": fact.category}
    out["conditions"] = list(shown.values())
    return out


def review_view(store: Store, user: StaffUser, review_id: str, request=None) -> dict:
    review = get_review(store, review_id)
    if review is None:
        raise LookupError(f"No order review {review_id}")
    if str(user.role) not in review.reviewer_roles:
        raise DecisionDenied(can_decide(user, review) or "Not your review")
    fhir = FhirGateway(Actor.of(user, request), ORDER_REVIEW)
    rtype, _, rid = review.order_ref.partition("/")
    order = fhir.read(rtype, rid)  # BreakGlassRequired / FhirAccessDenied for someone else's patient
    commentary = review.commentary or {}
    return {
        **review.model_dump(mode="json", exclude={"commentary", "decision"}), "decision": review.decision,
        "order_resource": order.to_fhir() if order else None,
        "commentary": {k: commentary.get(k) for k in ("concerns", "alternatives", "guideline_refs", "guidelines",
                                                      "unresolved_refs", "dropped_refs", "problems", "reason",
                                                      "model_status", "run_id", "evaluated", "attempts")}
        if commentary else None,
        "can_decide": can_decide(user, review) is None and not review.decision,
        "why_not": None if review.decision else can_decide(user, review),
        "patient": patient_header(user, review.patient_id, review.encounter_id, request),
        "medrec": [{"id": s.id, "kind": s.kind, "status": s.status} for s in
                   (sessions(store).get(session_key(review.encounter_id, k)) for k in ("admission", "discharge"))
                   if s is not None] if review.encounter_id else []}


def session_view(store: Store, user: StaffUser, session_id: str, request=None) -> dict:
    session = _medrec_session(store, session_id)
    role = str(user.role)
    if role not in ("pharmacist", "physician"):
        raise DecisionDenied("Reconciliations are for pharmacists and physicians")
    header = patient_header(user, session.patient_id, session.encounter_id, request)  # 6.3 scope applies
    if session.ai_status == "pending":
        session = settle(store, session_id)
    items = _items(session)
    table = reviews(store)
    order_flags = {}
    for item in items:
        for line in (item.order, item.inpatient):
            if line and line.ref.startswith("MedicationRequest/") and (r := table.get(review_key(line.ref))):
                order_flags[line.ref] = {"review_id": r.id, "severity": r.severity, "blocking": r.blocking,
                                         "status": r.status, "titles": [f["title"] for f in r.findings][:3]}
    doc = None
    if session.document_id:
        found = FhirGateway(Actor.of(user, request), MEDREC).read("DocumentReference", session.document_id)
        if found is not None:
            d = found.to_fhir()
            doc = {"id": d["id"], "doc_status": d.get("docStatus"), "signatures": document_signatures(d),
                   "authenticator": (d.get("authenticator") or {}).get("display")}
    left = unhandled(session)
    can_sign = (role == "pharmacist" and session.status == "open" and not left) or \
               (role == "physician" and session.status == "pharmacist_signed")
    return {**session.model_dump(mode="json", exclude={"items"}), "items": [
        {**i.model_dump(mode="json"), "discrepancy": i.discrepancy, "handled": i.handled} for i in items],
            "order_flags": order_flags, "unhandled": left, "can_sign": can_sign,
            "can_handle": role == "pharmacist" and session.status == "open", "document": doc, "patient": header,
            "discharge_preview": discharge_table(items) if session.kind == "discharge" else None}


def encounter_view(store: Store, user: StaffUser, encounter_id: str, request=None) -> dict:
    """An encounter's medication orders with their review status and its reconciliations (for the workbench)."""
    fhir = FhirGateway(Actor.of(user, request), ORDER_REVIEW)
    encounter = fhir.read("Encounter", encounter_id)
    if encounter is None:
        raise LookupError(f"No encounter {encounter_id}")
    table = reviews(store)
    orders = []
    for m in fhir.get_medications(encounter_id):
        data = m.to_fhir()
        r = table.get(review_key(f"MedicationRequest/{data['id']}"))
        orders.append({"ref": f"MedicationRequest/{data['id']}", **_summary(data),
                       "review": {"id": r.id, "severity": r.severity, "status": r.status, "blocking": r.blocking}
                       if r else None})
    return {"encounter_id": encounter_id, "orders": orders,
            "medrec": [{"id": s.id, "kind": s.kind, "status": s.status, "document_id": s.document_id} for s in
                       (sessions(store).get(session_key(encounter_id, k)) for k in ("admission", "discharge"))
                       if s is not None],
            "patient": patient_header(user, access.patient_of(encounter.to_fhir()), encounter_id, request)}


def rules_view() -> dict:
    t = rules.load_tables()
    return {"renal_threshold": t.renal_threshold,
            "renal": [d.model_dump() for d in t.renal.values()],
            "interactions": [p.model_dump(by_alias=True) for p in t.pairs],
            "dose_bounds": [d.model_dump() for d in t.dose.values()],
            "duplicate_test": t.medrec.duplicate_test.model_dump(),
            "note": "Demo simplifications, not clinical guidance; production uses a commercial drug knowledge base."}

