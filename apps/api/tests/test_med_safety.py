"""WP6 (spec 7.2): orders and medication safety end to end on the seeded hospital.

The generator plants the 8.2 case (warfarin plus a new naproxen, a home antihypertensive missed at admission), a
blocking allergy, renal dosing, dose bounds, a duplicate test and a Medicine A admission reconciliation, and writes
discharge prescriptions for the stays leaving within 24 hours (app/ehr/seed/medsafety.py). Covered here: the
intake and the rules on real FHIR data, the AI commentary (schema, evidence, queue Task, consent, prod mode), the
decisions, admission and discharge reconciliation through signing and co-signing, the discharge medication table
interface for 7.3, the workbench API and its RBAC.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager

import pytest

from app.agents import registry
from app.agents import trace as traces
from app.core.models import Role
from app.core.store import EngineSource, Store, get_engine
from app.ehr import consent, simulator
from app.ehr.clock import hospital_now
from app.ehr.gateway import Actor, FhirGateway, LocalBackend
from app.fhir.dt import parse, ref_id
from app.modules.med_safety import discharge, review_agent, service as S
from app.modules.med_safety.models import reviews, session_key, sessions
from app.modules.med_safety.reconcile import RecItem

NAPROXEN = "MedicationRequest/medrx-ms-82-naproxen"
PIPERACILLIN = "MedicationRequest/medrx-ms-allergy-piperacillin"
KETOROLAC = "MedicationRequest/medrx-ms-renal-ketorolac"
TRAMADOL = "MedicationRequest/medrx-ms-dose-tramadol"
REPEAT_TEST = "ServiceRequest/ord-ms-duplicate"
QUETIAPINE = "medrx-ms-medrec-quetiapine"


@pytest.fixture(autouse=True)
def fresh_intake():
    """Every test starts from the rolled-back seed: forget which state the intake sweep last saw."""
    S.reset_state()
    yield
    S.reset_state()


def user(store, role: Role):
    return next(u for u in store.staff.values() if u.role == role and u.demo_login)


def encounter_of(store, rid: str, rtype: str = "MedicationRequest") -> str:
    return ref_id(store.fhir.read(rtype, rid)["encounter"])


def settled_session(store, encounter_id: str, kind: str):
    session = S.reconcile(store, encounter_id, kind)
    return S.settle(store, session.id)


def handle_all(store, pharmacist, session, *, modify: str | None = None):
    for item in [RecItem.model_validate(i) for i in session.items]:
        if item.discrepancy and item.action is None:
            if item.id == modify:
                session = S.handle_item(store, pharmacist, session.id, item.id, "modify", "Resume at home dose",
                                        change="Resume the home medication at the home dose")
            else:
                session = S.handle_item(store, pharmacist, session.id, item.id, "accept", "Intended by the team")
    return session


# ---------- demo data ----------


def test_generator_plants_the_cases_and_discharge_prescriptions(fresh_state):
    store = fresh_state
    naproxen = store.fhir.read("MedicationRequest", "medrx-ms-82-naproxen")
    stay = ref_id(naproxen["encounter"])
    orders = {m["medicationCodeableConcept"]["coding"][0]["code"]
              for m in store.fhir.search("MedicationRequest", encounter=stay)}
    assert "11289" in orders  # warfarin continued
    pid = ref_id(naproxen["subject"])
    home = {m["medicationCodeableConcept"]["coding"][0]["code"]
            for m in store.fhir.search("MedicationStatement", patient=pid, status="active")}
    missed = [rx for rx in ("17767", "29046", "5487") if rx in home and rx not in orders]
    assert "11289" in home and missed  # a home antihypertensive not ordered on admission
    assert store.fhir.read("Observation", "obs-ms-82-inr")["valueQuantity"]["value"] == 3.8
    for rid in ("medrx-ms-allergy-piperacillin", "medrx-ms-renal-ketorolac", "medrx-ms-dose-tramadol", QUETIAPINE):
        assert store.fhir.read("MedicationRequest", rid)["status"] == "active"
    assert store.fhir.read("ServiceRequest", "ord-ms-duplicate")["status"] == "active"
    plans = store.fhir.search("MedicationRequest", category="plan")
    assert plans and all(p["intent"] == "plan" and p["status"] == "active" and p["id"].startswith("medrx-dc-")
                         and p["category"][0]["coding"][0]["code"] == "discharge" for p in plans)
    stays = {ref_id(p["encounter"]) for p in plans}
    units = {S._unit_of_encounter(store.fhir.read("Encounter", s)) for s in stays}
    assert "MEDA" in units  # the demo physician co-signs one


def test_the_planted_ai_cases_fit_their_story_whatever_the_day_of_seeding():
    # The hospital is generated as of 07:00 on the day of seeding (Saturday seeds Friday, Sunday seeds Monday), and
    # which stays get the planted cases depends on that day. The 8.2 case and the admission reconciliation need the
    # patient's ai_processing consent, which the newest Medicine A admission lacks on Monday (deny) and Thursday
    # (missing); the 8.2 naproxen is for fracture pain, and on Thursday the newest Orthopedics admission is a spinal
    # fusion. The renal case's ketorolac must meet the renal rule alone and be the patient's only NSAID: the last
    # Medicine B stay, picked before, already took ibuprofen or celecoxib on Monday, Tuesday and Thursday, and
    # warfarin on Wednesday (a major interaction, listed before the renal finding). One seeding per hospital day
    # stands for the others: a Sunday (Monday's day) and a Thursday.
    from datetime import datetime

    from app.core.config import settings
    from app.core.store import Store, use_store
    from app.ehr.seed import generate
    from app.ehr.seed.medsafety import FRACTURES
    from app.modules.med_safety import facts, rules

    for wall in (datetime(2026, 10, 11, 12), datetime(2026, 10, 15, 12)):
        s = Store()
        with use_store(s):
            info = generate(s, settings.seed, wall)
            fhir = FhirGateway(Actor.system("test"), "consent_management", LocalBackend(s))
            for name in ("case_82", "medrec"):
                case = info["medsafety"][name]
                assert consent.consent_status(fhir, case["patient"])["ai_processing"] == "permit", (wall, name)
            stay = s.fhir.read("Encounter", info["medsafety"]["case_82"]["stay"])
            assert stay["reasonCode"][0]["coding"][0]["code"] in FRACTURES, wall
            assert ref_id(s.fhir.read("MedicationRequest", "medrx-ms-82-naproxen")["encounter"]) == stay["id"]
            assert ref_id(s.fhir.read("MedicationRequest", QUETIAPINE)["encounter"]) == info["medsafety"]["medrec"]["stay"]
            pid = info["medsafety"]["renal"]["patient"]
            known = S.collect(FhirGateway(Actor.system("test"), "order_review", LocalBackend(s)), [pid],
                              hospital_now(s))[pid]
            ketorolac = facts.med_from_request(s.fhir.read("MedicationRequest", "medrx-ms-renal-ketorolac"))
            assert [f.rule for f in rules.evaluate(ketorolac, known)] == ["renal_dosing"], wall
            assert [m.name for m in known.meds if m.drug_class == "nsaid" and m.ref != ketorolac.ref] == [], wall


def test_discharge_keeps_the_discharge_prescriptions_active(fresh_state):
    store = fresh_state
    plans = store.fhir.search("MedicationRequest", category="plan")
    stays = {ref_id(p["encounter"]) for p in plans}
    upcoming = sorted((e for e in store.modules["hospital_plan"]["events"] if e["kind"] == "discharge"
                       and e["stay"] in stays), key=lambda e: e["at"])
    first = upcoming[0]
    minutes = (parse(first["at"]) - hospital_now(store)).total_seconds() / 60 + 1
    simulator.advance_by(store, minutes)
    stay = first["stay"]
    assert store.fhir.read("Encounter", stay)["status"] == "finished"
    meds = store.fhir.search("MedicationRequest", encounter=stay)
    assert all(m["status"] == "completed" for m in meds if m["intent"] == "order")
    assert all(m["status"] == "active" for m in meds if m["intent"] == "plan")


# ---------- intake and rules ----------


def test_intake_reviews_new_orders_with_the_rules(fresh_state):
    store = fresh_state
    counts = S.intake(store, force=True)
    assert counts["reviews"] > 50 and counts["admission"] > 5 and counts["discharge"] > 5
    table = reviews(store)
    naproxen = table.get(NAPROXEN)
    assert naproxen.severity == "major" and not naproxen.blocking and naproxen.status == "pending"
    assert any(f["entry"] == "interactions:warfarin-nsaid" for f in naproxen.findings)
    allergy = table.get(PIPERACILLIN)
    assert allergy.blocking and allergy.findings[0]["rule"] == "allergy"
    renal = table.get(KETOROLAC)  # the kidneys are the case's only problem, on the planted eGFR
    assert [(f["rule"], f["evidence_refs"]) for f in renal.findings] == [
        ("renal_dosing", ["Observation/obs-ms-renal-egfr"])]
    assert any(f["rule"] == "dose_bounds" and "a day is above 300" in f["detail"] for f in table.get(TRAMADOL).findings)
    repeat = table.get(REPEAT_TEST)
    assert repeat.kind == "service" and repeat.reviewer_roles == ["physician"]
    assert repeat.findings[0]["rule"] == "duplicate_test" and repeat.commentary_status == "pending"
    # the review never changes the order
    assert store.fhir.read("MedicationRequest", "medrx-ms-82-naproxen")["status"] == "active"
    assert S.intake(store, force=True) == {"reviews": 0, "admission": 0, "discharge": 0}
    assert S.intake(store) == {}  # nothing changed: the sweep is skipped


def test_entry_points_are_idempotent(fresh_state):
    store = fresh_state
    first = S.review_order(store, NAPROXEN)
    assert S.review_order(store, NAPROXEN).id == first.id
    encounter = encounter_of(store, "medrx-ms-82-naproxen")
    reviewed = S.review_encounter_orders(store, encounter)
    assert first.id in {r.id for r in reviewed} and len({r.id for r in reviewed}) == len(reviewed)
    session = S.reconcile(store, encounter, "admission")
    assert S.reconcile(store, encounter, "admission").id == session.id
    settled = S.settle(store, session.id)
    assert S.settle(store, session.id).document_id == settled.document_id  # one draft record
    docs = [d for d in store.fhir.search("DocumentReference", encounter=encounter, code="medrec")]
    assert len(docs) == 1
    with pytest.raises(ValueError):
        S.review_order(store, "Observation/x")


# ---------- the AI commentary ----------


def test_ai_commentary_is_schema_valid_grounded_and_queued(fresh_state):
    store = fresh_state
    review = S.review_order(store, NAPROXEN)
    review = S.comment(store, review.id)
    assert review.commentary_status == "done" and not review.blocking  # the AI never blocks
    commentary = review.commentary
    review_agent.Commentary.model_validate({k: commentary[k] for k in ("concerns", "alternatives", "guideline_refs")})
    assert commentary["concerns"] and commentary["unresolved_refs"] == []
    refs = [r for c in commentary["concerns"] for r in c["evidence_refs"]]
    assert refs and S.module_fhir().resolve_refs(refs) == set(refs)
    assert commentary["guideline_refs"] and all(S.knowledge.resolve(g) for g in commentary["guideline_refs"])
    assert all(0 <= c["confidence"] <= 1 for c in commentary["concerns"])
    task = store.fhir.read("Task", review.review_task_id)
    assert (task["intent"], task["status"], task["focus"]["reference"]) == ("proposal", "requested", NAPROXEN)
    assert {c["coding"][0]["code"] for c in task["performerType"]} == {"pharmacist", "doctor"}
    run = traces.run_of(task)
    trace = traces.get(run)
    assert trace.agent_id == "order_review" and trace.prompt_version == "order_review@1"
    assert NAPROXEN in trace.input_refs and trace.provenance_id
    assert store.fhir.read("Provenance", trace.provenance_id)["target"][0]["reference"] == f"Task/{task['id']}"


def test_without_consent_or_in_prod_mode_the_rules_stand_alone(fresh_state):
    store = fresh_state
    S.intake(store, force=True)
    fhir = FhirGateway(Actor.system("test"), "consent_management", LocalBackend(store))
    flagged = [r for r in reviews(store).values() if r.kind == "medication" and r.findings]
    without = next((r for r in flagged if consent.consent_status(fhir, r.patient_id)["ai_processing"] != "permit"), None)
    if without is None:  # make one: the patient withdrew the AI-processing consent
        without = flagged[0]
        for c in store.fhir.search("Consent", patient=without.patient_id):
            if c["category"][0]["coding"][0]["code"] == "ai_processing":
                store.fhir.update({**c, "provision": {**c["provision"], "type": "deny"}})
    review = S.comment(store, without.id)
    assert review.commentary_status == "refused" and "consent" in review.commentary["reason"]
    assert review.findings and review.status == "pending"  # the rule findings stand
    other = next(r for r in flagged if r.id != without.id)
    with registry.mode("prod"):
        refused = S.comment(store, other.id)
    assert refused.commentary_status == "refused" and refused.findings


# ---------- decisions ----------


def test_review_decisions_need_the_right_person_and_a_reason(fresh_state):
    store = fresh_state
    S.intake(store, force=True)
    pharmacist, physician = user(store, Role.PHARMACIST), user(store, Role.PHYSICIAN)
    allergy = reviews(store).get(PIPERACILLIN)
    with pytest.raises(S.DecisionDenied, match="pharmacist"):
        S.decide_review(store, physician, allergy.id, "accept_order", "Tolerated before")  # Medicine A, but blocking
    with pytest.raises(S.DecisionProblem, match="reason"):
        S.decide_review(store, pharmacist, allergy.id, "request_change", "no")
    decided = S.decide_review(store, pharmacist, allergy.id, "request_change",
                              "Anaphylaxis to penicillin: switch to a non-beta-lactam")
    assert decided.status == "resolved" and decided.decision["change_task_id"]
    task = store.fhir.read("Task", decided.decision["change_task_id"])
    assert task["code"]["coding"][0]["code"] == "order-change-request"
    assert task["performerType"][0]["coding"][0]["code"] == "doctor"
    assert store.fhir.read("MedicationRequest", "medrx-ms-allergy-piperacillin")["status"] == "active"
    event = [e for e in store.audit.events() if e.event_type == "approve"][-1]
    assert event.user_id == pharmacist.id and event.module == "order_review"
    with pytest.raises(S.DecisionProblem, match="already"):
        S.decide_review(store, pharmacist, allergy.id, "accept_order", "Second thoughts")
    # with AI commentary: the decision completes the ai-review Task
    naproxen = S.comment(store, reviews(store).get(NAPROXEN).id)
    decided = S.decide_review(store, pharmacist, naproxen.id, "request_change", "Stop naproxen; acetaminophen instead")
    task = store.fhir.read("Task", naproxen.review_task_id)
    assert task["status"] == "completed" and {o["valueString"] for o in task["output"]} >= {"approve", pharmacist.id}
    assert traces.get(traces.run_of(task)).human_action["decision"] == "accept"
    with pytest.raises(S.DecisionDenied):
        S.decide_review(store, pharmacist, reviews(store).get(REPEAT_TEST).id, "accept_order", "Fine as ordered")


# ---------- reconciliation ----------


@contextmanager
def opened_elsewhere(encounter_id: str, kind: str = "admission"):
    """The reconciliation opened in a second, real transaction (another request, or the background intake) that
    commits a second later. Its committed row is deleted again afterwards."""
    other = Store(EngineSource(get_engine()))
    opened = S.reconcile(other, encounter_id, kind)  # holds the reconciliation's lock; the session is pending
    timer = threading.Timer(1.0, other.commit)
    timer.start()
    try:
        yield opened
    finally:
        timer.join()
        other.close()
        cleanup = Store(EngineSource(get_engine()))
        del sessions(cleanup)[session_key(encounter_id, kind)]
        cleanup.commit()


def test_a_reconciliation_opened_by_another_transaction_is_returned_not_replaced(fresh_state):
    """Two requests open the same reconciliation at once: the second waits for the first and returns its session
    instead of writing its own over it under the same key."""
    store = fresh_state
    encounter = encounter_of(store, QUETIAPINE)
    with opened_elsewhere(encounter) as opened:
        started = time.monotonic()
        assert S.reconcile(store, encounter, "admission").id == opened.id
        assert time.monotonic() - started >= 0.8


def test_the_intake_leaves_a_reconciliation_a_request_is_opening(fresh_state):
    store = fresh_state
    now = hospital_now(store)
    admitted = S.module_fhir(S.MEDREC).search("Encounter", cls="IMP", status="in-progress",
                                               date_from=now - S.ADMISSION_WINDOW)
    encounter = next(e["id"] for e in admitted if session_key(e["id"], "admission") not in sessions(store))
    with opened_elsewhere(encounter) as opened:
        assert S.intake(store, force=True)["admission"] > 0
        assert sessions(store)[session_key(encounter, "admission")].id == opened.id


def test_admission_reconciliation_signed_and_cosigned(fresh_state):
    store = fresh_state
    pharmacist, physician = user(store, Role.PHARMACIST), user(store, Role.PHYSICIAN)
    encounter = encounter_of(store, QUETIAPINE)
    session = settled_session(store, encounter, "admission")
    items = {i["id"]: RecItem.model_validate(i) for i in session.items}
    assert session.ai_status == "done" and session.document_id
    assert any(i.type == "dose_change" for i in items.values())
    assert items["dup-statin"].type == "duplicate_therapy"
    quetiapine = items["new-51272"]
    assert quetiapine.type == "new_without_indication" and quetiapine.source == "ai" and quetiapine.confidence
    with pytest.raises(S.DecisionProblem, match="Handle every discrepancy"):
        S.sign(store, pharmacist, session.id)
    with pytest.raises(S.DecisionDenied):
        S.handle_item(store, physician, session.id, "dup-statin", "accept", "Intended")
    with pytest.raises(S.DecisionProblem, match="change"):
        S.handle_item(store, pharmacist, session.id, "dup-statin", "modify", "Two statins")
    session = handle_all(store, pharmacist, session, modify="dup-statin")
    assert S.unhandled(session) == []
    with pytest.raises(S.DecisionProblem, match="after the pharmacist"):
        S.sign(store, physician, session.id)
    signed = S.sign(store, pharmacist, session.id)
    doc = store.fhir.read("DocumentReference", signed.document_id)
    assert signed.status == "pharmacist_signed" and doc["docStatus"] == "preliminary"
    assert signed.change_tasks  # the modify item went to the prescriber
    sign_events = [e for e in store.audit.events() if e.event_type == "sign" and e.resource_id == signed.document_id]
    assert sign_events and sign_events[-1].user_id == pharmacist.id
    final = S.sign(store, physician, session.id)
    doc = store.fhir.read("DocumentReference", final.document_id)
    assert final.status == "final" and doc["docStatus"] == "final"
    assert doc["authenticator"]["reference"] == f"Practitioner/{pharmacist.practitioner_id}"
    prov = store.fhir.read("Provenance", traces.provenance_id(traces.run_of(doc)))
    verifiers = {a["who"]["reference"] for a in prov["agent"]
                 if any(c.get("code") == "verifier" for c in (a.get("type") or {}).get("coding") or [])}
    assert verifiers == {f"Practitioner/{pharmacist.practitioner_id}", f"Practitioner/{physician.practitioner_id}"}
    with pytest.raises(S.DecisionProblem):
        S.handle_item(store, pharmacist, session.id, "dup-statin", "accept", "Too late")


def test_the_82_case_shows_the_interaction_and_the_omission(fresh_state):
    store = fresh_state
    pharmacist = user(store, Role.PHARMACIST)
    encounter = encounter_of(store, "medrx-ms-82-naproxen")
    S.review_encounter_orders(store, encounter)
    session = settled_session(store, encounter, "admission")
    items = [RecItem.model_validate(i) for i in session.items]
    omissions = [i for i in items if i.type == "omission" and i.home.rxcui in ("17767", "29046", "5487")]
    assert omissions
    view = S.session_view(store, pharmacist, session.id)
    flag = view["order_flags"][NAPROXEN]
    assert flag["severity"] == "major" and any("warfarin" in t for t in flag["titles"])
    naproxen = next(i for i in items if i.order and i.order.ref == NAPROXEN)
    assert naproxen.type == "new" and naproxen.source == "ai"  # fracture pain: indicated


def test_discharge_medication_table_interface(fresh_state):
    """The interface 7.3 builds on: a typed read through FhirGateway, final only by default; and the endpoint."""
    store = fresh_state
    pharmacist, physician = user(store, Role.PHARMACIST), user(store, Role.PHYSICIAN)
    plans = store.fhir.search("MedicationRequest", category="plan")
    stays = sorted({ref_id(p["encounter"]) for p in plans})
    stay = next(s for s in stays if S._unit_of_encounter(store.fhir.read("Encounter", s)) == "MEDA")
    session = settled_session(store, stay, "discharge")
    reader = FhirGateway(Actor.of(physician), "discharge_summary", LocalBackend(store))
    assert discharge.discharge_medication_table(reader, stay) is None
    items = [RecItem.model_validate(i) for i in session.items]
    S.accept_hospital_use(store, pharmacist, session.id, "Hospital use only; stops at discharge")
    home_gap = next((i.id for i in items if i.type == "omission" and i.home is not None), None)
    session = handle_all(store, pharmacist, S._medrec_session(store, session.id), modify=home_gap)
    S.sign(store, pharmacist, session.id)
    pending = discharge.discharge_medication_table(reader, stay, final_only=False)
    assert pending.status == "awaiting_cosign"
    S.sign(store, physician, session.id)
    table = discharge.discharge_medication_table(reader, stay)
    assert table.status == "final" and table.encounter_id == stay and table.document.startswith("DocumentReference/")
    assert [s.role for s in table.signatures] == ["pharmacist", "physician"]
    lines = table.all()
    discharge_rx = {p["medicationCodeableConcept"]["coding"][0]["code"] for p in plans
                    if ref_id(p["encounter"]) == stay}
    assert {m.rxcui for m in [*table.new, *table.changed]} | {m.rxcui for m in table.continued} >= discharge_rx
    for line in lines:
        assert line.category in ("new", "changed", "stopped", "continued") and line.source_refs
        assert all(store.fhir.read(*r.split("/")) for r in line.source_refs)
    if home_gap:
        resumed = [m for m in table.continued if m.change_note]
        assert resumed and resumed[0].rxcui == home_gap.removeprefix("om-")
    for m in table.changed:
        assert m.previous_text and m.dose_text and m.previous_text != m.dose_text


def test_discharge_medications_endpoint(client, login, fresh_state):
    store = fresh_state
    pharmacist, physician = user(store, Role.PHARMACIST), user(store, Role.PHYSICIAN)
    plans = store.fhir.search("MedicationRequest", category="plan")
    stay = next(s for s in sorted({ref_id(p["encounter"]) for p in plans})
                if S._unit_of_encounter(store.fhir.read("Encounter", s)) == "MEDA")
    url = f"/api/med-safety/encounters/{stay}/discharge-medications"
    assert client.get(url, headers=login(physician.id)).status_code == 404
    session = settled_session(store, stay, "discharge")
    session = handle_all(store, pharmacist, session)
    S.sign(store, pharmacist, session.id)
    assert client.get(url + "?final_only=false", headers=login(physician.id)).json()["status"] == "awaiting_cosign"
    S.sign(store, physician, session.id)
    body = client.get(url, headers=login(physician.id)).json()
    assert body["schema"] == "discharge-medication-table@1" and body["status"] == "final"
    assert set(body) >= {"new", "changed", "stopped", "continued", "document", "signatures"}
    assert client.get(url, headers=login(pharmacist.id)).status_code == 200
    assert client.get(url, headers=login(user(store, Role.CLERK).id)).status_code == 403


# ---------- the workbench API ----------


def test_workbench_queue_and_rbac(client, login, fresh_state):
    store = fresh_state
    pharmacist, physician = user(store, Role.PHARMACIST), user(store, Role.PHYSICIAN)
    for other in (user(store, Role.NURSE), user(store, Role.CLERK), store.staff["U-OPS"], store.staff["U-ADMIN"]):
        assert client.get("/api/med-safety/queue", headers=login(other.id)).status_code == 403
    body = client.get("/api/med-safety/queue", headers=login(pharmacist.id)).json()
    items = body["items"]
    assert items[0]["blocking"] and items[0]["kind"] == "order_review"
    ranks = [(not i["blocking"], -S.rules.SEVERITY_RANK[i["severity"]]) for i in items if i["status"] == "pending"]
    assert ranks == sorted(ranks)  # blocking first, then severity
    assert all(i["order_kind"] == "medication" for i in items if i["kind"] == "order_review")
    assert any(i.get("order_ref") == NAPROXEN for i in items) and all(i["mrn"] for i in items)
    mine = client.get("/api/med-safety/queue?show=all", headers=login(physician.id)).json()["items"]
    assert mine and all(i["unit_id"] in physician.unit_ids for i in mine)
    assert any(i.get("order_ref") == REPEAT_TEST for i in mine)
    repeat = reviews(store).get(REPEAT_TEST)
    assert client.get(f"/api/med-safety/reviews/{repeat.id}", headers=login(pharmacist.id)).status_code == 403
    assert client.get(f"/api/med-safety/reviews/{repeat.id}", headers=login(physician.id)).status_code == 200
    naproxen = reviews(store).get(NAPROXEN)  # Orthopedics: outside the Medicine A physician's units
    response = client.get(f"/api/med-safety/reviews/{naproxen.id}", headers=login(physician.id))
    assert response.status_code == 403 and response.json()["code"] == "break_glass_required"


def test_workbench_flow_over_http(client, login, fresh_state):
    store = fresh_state
    pharmacist, physician = user(store, Role.PHARMACIST), user(store, Role.PHYSICIAN)
    ph, md = login(pharmacist.id), login(physician.id)
    client.get("/api/med-safety/queue", headers=ph)
    naproxen = reviews(store).get(NAPROXEN)
    view = client.post(f"/api/med-safety/reviews/{naproxen.id}/commentary", headers=ph).json()
    assert view["commentary"]["concerns"] and view["can_decide"]
    refs = [r for c in view["commentary"]["concerns"] for r in c["evidence_refs"]] + view["commentary"]["guideline_refs"]
    shown = client.get("/api/med-safety/evidence", params=[("ref", r) for r in refs], headers=ph).json()["evidence"]
    assert {e["kind"] for e in shown} <= {"fhir", "guideline"} and len(shown) == len(set(refs))
    decided = client.post(f"/api/med-safety/reviews/{naproxen.id}/decision", headers=ph,
                          json={"decision": "accept_order", "reason": "x"})
    assert decided.status_code == 409
    decided = client.post(f"/api/med-safety/reviews/{naproxen.id}/decision", headers=ph,
                          json={"decision": "request_change", "reason": "Stop naproxen: warfarin and INR 3.8"})
    assert decided.status_code == 200 and decided.json()["status"] == "resolved"
    encounter = encounter_of(store, QUETIAPINE)
    session = S.reconcile(store, encounter, "admission")
    view = client.get(f"/api/med-safety/medrec/{session.id}", headers=ph).json()
    assert view["ai_status"] == "done" and not view["can_sign"] and view["can_handle"]
    for item in view["items"]:
        if item["discrepancy"]:
            response = client.post(f"/api/med-safety/medrec/{session.id}/items/{item['id']}", headers=ph,
                                   json={"action": "accept", "reason": "Reviewed with the team"})
            assert response.status_code == 200
    view = client.get(f"/api/med-safety/medrec/{session.id}", headers=ph).json()
    assert view["can_sign"] and view["unhandled"] == []
    assert client.post(f"/api/med-safety/medrec/{session.id}/sign", headers=md).status_code == 409
    signed = client.post(f"/api/med-safety/medrec/{session.id}/sign", headers=ph).json()
    assert signed["status"] == "pharmacist_signed" and signed["document"]["doc_status"] == "preliminary"
    final = client.post(f"/api/med-safety/medrec/{session.id}/sign", headers=md).json()
    assert final["status"] == "final" and final["document"]["doc_status"] == "final"
    assert sessions(store).get(session_key(encounter, "admission")).status == "final"
    assert client.post(f"/api/med-safety/medrec/{session.id}/sign", headers=login(user(store, Role.NURSE).id)) \
        .status_code == 403


def test_a_medrec_record_is_signed_only_in_the_workbench(client, login, fresh_state):
    """The generic path (the chart's Sign endpoint, the signing service without the workbench's flow, an approved
    signDocumentFinal request) refuses a MedRec record with a denied `sign` audit event, even with every item handled;
    the chart links to the workbench instead of offering Sign; the workbench signs and co-signs it."""
    from types import SimpleNamespace

    from app.agents.handlers import sign_document_final
    from app.ehr.codes import MRN_SYSTEM
    from app.ehr.gateway import FhirAccessDenied
    from app.ehr.signing import signatures

    store = fresh_state
    pharmacist, physician = user(store, Role.PHARMACIST), user(store, Role.PHYSICIAN)
    ph, md = login(pharmacist.id), login(physician.id)
    encounter = encounter_of(store, QUETIAPINE)
    session = handle_all(store, pharmacist, settled_session(store, encounter, "admission"))
    assert S.unhandled(session) == []
    doc_id = session.document_id
    patient = store.fhir.read("Patient", session.patient_id)
    mrn = next(i["value"] for i in patient["identifier"] if i["system"] == MRN_SYSTEM)

    def chart_row(headers) -> dict:
        documents = client.get(f"/api/hospital/patients/{mrn}", headers=headers).json()["documents"]
        return next(d for d in documents if d["id"] == doc_id)

    def refused(by, before: int) -> None:
        denied = [e for e in store.audit.events()[before:] if e.event_type == "sign" and e.resource_id == doc_id]
        assert [(e.outcome, e.user_id, e.module) for e in denied] == [("denied", by.id, S.MEDREC)]
        assert "signed in Medication safety" in denied[0].reason

    for headers in (ph, md):  # both signers get a link to the workbench, no Sign button
        row = chart_row(headers)
        assert (row["signing_flow"], row["can_sign"], row["can_sign_in_flow"], row["encounter_id"]) == (
            "medrec_workbench", False, True, encounter)
    for who, headers in ((pharmacist, ph), (physician, md)):
        before = len(store.audit.events())
        response = client.post(f"/api/hospital/documents/{doc_id}/sign", headers=headers)
        assert response.status_code == 403 and "signed in Medication safety" in response.json()["detail"]
        refused(who, before)
    before = len(store.audit.events())
    with pytest.raises(FhirAccessDenied, match="Medication safety"):
        FhirGateway(Actor.of(pharmacist), "signing", LocalBackend(store)).sign_document(doc_id)
    refused(pharmacist, before)
    before = len(store.audit.events())
    with pytest.raises(FhirAccessDenied, match="Medication safety"):  # the agent's tool, run as its approver
        sign_document_final(SimpleNamespace(fhir=FhirGateway(Actor.of(pharmacist), S.MEDREC, LocalBackend(store))),
                            doc_id)
    refused(pharmacist, before)
    doc = store.fhir.read("DocumentReference", doc_id)
    assert doc["docStatus"] == "preliminary" and signatures(doc) == []
    assert sessions(store).get(session_key(encounter, "admission")).status == "open"

    signed = client.post(f"/api/med-safety/medrec/{session.id}/sign", headers=ph).json()
    assert signed["status"] == "pharmacist_signed" and signed["document"]["doc_status"] == "preliminary"
    assert client.post(f"/api/hospital/documents/{doc_id}/sign", headers=md).status_code == 403  # nor the co-signature
    final = client.post(f"/api/med-safety/medrec/{session.id}/sign", headers=md).json()
    assert final["status"] == "final" and final["document"]["doc_status"] == "final"
    doc = store.fhir.read("DocumentReference", doc_id)
    assert [s["role"] for s in signatures(doc)] == ["pharmacist", "physician"]
    items = [RecItem.model_validate(i) for i in discharge._data(doc)["items"]]
    assert any(i.discrepancy for i in items) and all(i.action for i in items if i.discrepancy)  # every decision
    assert (chart_row(ph)["doc_status"], chart_row(ph)["can_sign_in_flow"]) == ("final", False)


# ---------- the journey's steps (6.5: WP4c's activities call them; Temporal in tests/test_workflows.py) ----------


def _draft_calls(store, encounter_id: str) -> list[str]:
    return sorted(c.status for t in traces.table(store).values()
                  if t.agent_id == S.MEDREC and t.encounter_id == encounter_id
                  for c in t.tool_calls if c.tool_id == "draftMedRec")


def test_journey_order_review_step_signs_off_with_the_pharmacists_last_decision(fresh_state):
    from datetime import timedelta

    from app.agents.runtime import runtime
    from app.fhir.dt import fhir_datetime
    from app.modules.med_safety import journey as J

    store = fresh_state
    pharmacist = user(store, Role.PHARMACIST)
    encounter = encounter_of(store, "medrx-ms-allergy-piperacillin")
    as_of = fhir_datetime(hospital_now(store))
    found = J.review_orders(store, encounter, as_of)
    assert PIPERACILLIN in {r.order_ref for r in found}
    assert J.review_orders(store, encounter, as_of) == found  # reviews are kept: a retry gets the same list
    authored = next(r.authored_at for r in found if r.order_ref == PIPERACILLIN)
    before = J.review_orders(store, encounter, fhir_datetime(authored - timedelta(seconds=1)))
    assert PIPERACILLIN not in {r.order_ref for r in before}  # the journey's cut-off
    text, priority = J.signoff_text(found, as_of)
    assert priority == "stat" and "blocking" in text and "Medication safety" in text
    S.comment(store, reviews(store)[PIPERACILLIN].id)  # the AI commentary arrives later: the text stays the same
    assert J.signoff_text(J.review_orders(store, encounter, as_of), as_of) == (text, priority)

    steps = []
    for _ in range(2):  # Temporal's retry after the work committed: the gateway returns the first Task
        with runtime.start(S.ORDER_REVIEW, user=None, encounter_id=encounter) as run:
            steps.append(J.order_review_signoff(run, found, as_of))
            run.finish(outcome="completed")
    assert steps[0].wait_for == steps[1].wait_for and steps[0].signer == "pharmacist"
    task_id = steps[0].wait_for[0].split("/")[1]
    task = store.fhir.read("Task", task_id)
    assert task["code"]["coding"][0]["code"] == "workflow-signoff" and task["priority"] == "stat"
    assert task["performerType"][0]["coding"][0]["code"] == "pharmacist" and task["description"] == text

    pending = sorted((r for r in reviews(store).find_by("encounter_id", encounter)
                      if r.status == "pending" and r.kind == "medication"), key=lambda r: r.blocking)
    assert pending[-1].order_ref == PIPERACILLIN
    for n, review in enumerate(pending, 1):
        S.decide_review(store, pharmacist, review.id, "request_change" if review.blocking else "accept_order",
                        "Reviewed with the prescriber")
        assert store.fhir.read("Task", task_id)["status"] == ("completed" if n == len(pending) else "requested")
    approved = [e for e in store.audit.events() if e.event_type == "approve" and e.resource_id == task_id]
    assert approved and approved[-1].user_id == pharmacist.id


def test_journey_medrec_step_retried_replays_the_draft_and_skips_a_record_already_signed(fresh_state):
    from app.fhir.dt import fhir_datetime
    from app.modules.med_safety import journey as J

    store = fresh_state
    pharmacist, physician = user(store, Role.PHARMACIST), user(store, Role.PHYSICIAN)
    encounter = encounter_of(store, QUETIAPINE)
    as_of = fhir_datetime(hospital_now(store))
    first = J.medrec_admission(store, encounter, as_of)
    ref = f"DocumentReference/medrec-{encounter}-admission"
    assert first.wait_for == [ref] and first.signer == "pharmacist" and not first.refused
    again = J.medrec_admission(store, encounter, as_of)  # a retry: no second model call, the same draft replayed
    assert again.wait_for == [ref]
    assert _draft_calls(store, encounter) == ["ok", "replayed"]
    llm = [t for t in traces.table(store).values() if t.agent_id == S.MEDREC and t.encounter_id == encounter
           and t.llm_calls]
    assert len(llm) == 1
    assert len([d for d in store.fhir.search("DocumentReference", encounter=encounter)
                if d["type"]["coding"][0]["code"] == "medrec"]) == 1

    session = handle_all(store, pharmacist, S.reconcile(store, encounter, "admission"))
    S.sign(store, pharmacist, session.id)
    assert J.medrec_admission(store, encounter, as_of).wait_for == [ref]  # the co-signature still to come
    S.sign(store, physician, session.id)
    done = J.medrec_admission(store, encounter, as_of)  # signed in the workbench before the journey got here
    assert done.wait_for == [] and "already signed final" in done.detail
    assert _draft_calls(store, encounter) == ["ok", "replayed"]  # a signed record is not drafted again


def test_journey_medrec_step_reconciles_the_orders_written_by_the_sign_off(fresh_state):
    from datetime import timedelta

    from app.fhir.dt import fhir_datetime
    from app.modules.med_safety import journey as J

    store = fresh_state
    encounter = encounter_of(store, QUETIAPINE)
    quetiapine = store.fhir.read("MedicationRequest", QUETIAPINE)
    cutoff = fhir_datetime(parse(quetiapine["authoredOn"]) - timedelta(seconds=1))
    J.medrec_admission(store, encounter, cutoff)
    items = {i["id"] for i in S.reconcile(store, encounter, "admission").items}
    assert "new-51272" not in items  # quetiapine was ordered after the order review's sign-off


def test_a_journey_signoff_written_while_the_last_decision_was_saved_is_confirmed_soon_after(fresh_state):
    """WP9 (Temporal on): the journey's order review can write its sign-off Task while the pharmacist's last decision
    is being saved, so neither sees the other; the API's background recheck confirms it a moment later, in the name of
    the pharmacist who decided, as the decision itself would have."""
    from app.agents.runtime import runtime
    from app.modules.med_safety import journey as MJ

    store = fresh_state
    S.intake(store, force=True)
    pharmacist = user(store, Role.PHARMACIST)
    encounter = reviews(store).get(NAPROXEN).encounter_id
    # The activity reviews every order of the stay, also those older than the intake's 24 hours (on a Thursday the
    # 8.2 stay was admitted the day before), and the pharmacist decides them all: only then is the decision the last.
    found = MJ.review_orders(store, encounter, None)  # as the activity read them, before the decision was saved
    for r in [r for r in reviews(store).find_by("encounter_id", encounter)
              if r.status == "pending" and r.kind == "medication"]:
        S.decide_review(store, pharmacist, r.id, "request_change", "Stop it: interaction with warfarin at home")
    with runtime.start("order_review", user=None, encounter_id=encounter) as run:
        result = MJ.order_review_signoff(run, found, None)
        run.finish(outcome="completed")
    task_id = result.wait_for[0].split("/")[1]
    assert store.fhir.read("Task", task_id)["status"] == "requested"
    MJ._LAST["at"] = 0.0
    assert MJ.recheck_signoffs(store) == [task_id]
    assert store.fhir.read("Task", task_id)["status"] == "completed"
    assert MJ.decided_before(MJ.review_orders(store, encounter, None)) is not None  # a later journey asks no one
