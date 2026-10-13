"""WP7 (spec 7.3): the documentation agent end to end on the seeded hospital.

The generator plants on Medicine A a Chinese-speaking warfarin patient with a catheter ablation, a stay whose note
tempts the model into an unprescribed medicine, and a recently discharged stay with a pending result, and writes
discharge plans (CarePlan) for the stays leaving with prescriptions (app/ehr/seed/documentation.py). Covered here:
the refusal without a signed discharge medication table, the draft (chunks, citations, validator, trace and
Provenance), the physician's edit and the sign block, the bilingual patient instructions from the signed summary
only, the SBAR handoff, consent (template only), the journey steps without Temporal, the API and its RBAC.
"""

from __future__ import annotations

import pytest

from app.agents import trace as traces
from app.core.models import Role
from app.ehr import consent

from app.ehr.gateway import Actor, FhirAccessDenied, FhirGateway, LocalBackend
from app.fhir.dt import ref_id
from app.modules.documentation import document as D
from app.modules.documentation import journey, service
from app.modules.documentation import phrases as P
from app.modules.documentation.render import item_keys
from app.modules.med_safety import service as S
from app.modules.med_safety.reconcile import RecItem


@pytest.fixture(autouse=True)
def fresh_intake():
    S.reset_state()
    service.reset_state()
    yield
    S.reset_state()
    service.reset_state()


def user(store, role: Role):
    return next(u for u in store.staff.values() if u.role == role and u.demo_login)


def stay_of(store, rtype: str, rid: str) -> str:
    return ref_id(store.fhir.read(rtype, rid)["encounter"])


def sign_discharge_medrec(store, encounter_id: str):
    """WP6's discharge reconciliation: the pharmacist handles every discrepancy and signs, a physician co-signs."""
    pharmacist, physician = user(store, Role.PHARMACIST), user(store, Role.PHYSICIAN)
    session = S.reconcile(store, encounter_id, "discharge")
    session = S.settle(store, session.id)
    for item in [RecItem.model_validate(i) for i in session.items]:
        if (item.discrepancy and item.action is None) or item.indication == "pending":
            session = S.handle_item(store, pharmacist, session.id, item.id, "accept", "Intended by the team")
    S.sign(store, pharmacist, session.id)
    return S.sign(store, physician, session.id)


def red(view: dict) -> list[dict]:
    return [i for i in view["validation"]["issues"] if i["severity"] == "red"]


# ---------- demo data ----------


def test_generator_plants_the_documentation_cases(fresh_state):
    store = fresh_state
    warfarin = stay_of(store, "Procedure", "proc-doc-ablation")
    halluc = stay_of(store, "Condition", "cond-doc-htn-note")
    for stay in (warfarin, halluc):
        enc = store.fhir.read("Encounter", stay)
        assert enc["status"] == "in-progress"
        assert store.fhir.read("CarePlan", f"careplan-dc-{stay}") is not None
        assert any(m.get("intent") == "plan" for m in store.fhir.search("MedicationRequest", encounter=stay))
    patient = store.fhir.read("Patient", store.fhir.read("Encounter", warfarin)["subject"]["reference"].split("/")[1])
    assert patient["communication"][0]["language"]["coding"][0]["code"] in ("zh-CN", "zh-TW")
    plans = [m for m in store.fhir.search("MedicationRequest", encounter=warfarin) if m.get("intent") == "plan"]
    assert any(m["medicationCodeableConcept"]["coding"][0]["code"] == "11289" for m in plans)
    activities = store.fhir.read("CarePlan", f"careplan-dc-{warfarin}")["activity"]
    assert activities[0]["detail"]["code"]["coding"][0]["code"] == "anticoag_clinic"
    note = store.fhir.read("Condition", "cond-doc-htn-note")["note"][0]["text"]
    assert "not started" in note
    pending = [s for s in store.fhir.search("ServiceRequest", status="active") if s["id"].startswith("ord-doc-pending-")]
    assert len(pending) == 1
    discharged = store.fhir.read("Encounter", ref_id(pending[0]["encounter"]))
    assert discharged["status"] == "finished"


# ---------- discharge summary ----------


def test_summary_refuses_without_the_signed_discharge_medication_table(fresh_state):
    store = fresh_state
    stay = stay_of(store, "Procedure", "proc-doc-ablation")
    out = service.draft_summary(store, user(store, Role.PHYSICIAN), stay)
    assert out["status"] == "insufficient"
    assert any("discharge medication table" in m for m in out["missing"])
    assert store.fhir.read("DocumentReference", D.document_id("discharge_summary", stay)) is None


def test_summary_draft_traces_every_item_and_the_physician_signs(fresh_state):
    store = fresh_state
    stay = stay_of(store, "Procedure", "proc-doc-ablation")
    sign_discharge_medrec(store, stay)
    physician = user(store, Role.PHYSICIAN)
    view = service.draft_summary(store, physician, stay)
    assert view["kind"] == "discharge_summary" and view["doc_status"] == "preliminary" and view["mode"] == "ai"
    content = view["content"]
    assert content["discharge_diagnoses"] and content["medications"]["changed"] or content["medications"]["continued"]
    assert any(d["code"] == "49436004" for d in content["discharge_diagnoses"])
    assert any(p["code"] == "18286008" for p in content["procedures"])
    assert content["hospital_course"] and all(s["source_refs"] for s in content["hospital_course"])
    assert all(s["date"][:2] == "20" for s in content["hospital_course"])  # date tokens restored
    sources = {s["ref"] for s in view["sources"]}
    for section in ("discharge_diagnoses", "procedures", "follow_up", "red_flags", "hospital_course"):
        for item in content[section]:
            assert item["source_refs"] and set(item["source_refs"]) <= sources, (section, item)
    assert not red(view) and view["can_sign"]
    assert view["model_calls"] >= len(view["inputs"]["chunks"]) + 1  # a segment call per chunk, then the synthesis
    trace = traces.get(view["run_id"])
    assert trace.agent_id == "discharge_summary" and f"DocumentReference/{view['id']}" in trace.output_refs
    assert len(trace.llm_calls) == view["model_calls"]
    signed = service.sign(store, physician, view["id"])
    assert signed["doc_status"] == "final" and signed["signatures"][0]["role"] == "physician"
    doc = store.fhir.read("DocumentReference", view["id"])
    assert doc["authenticator"]["reference"] == f"Practitioner/{physician.practitioner_id}"
    assert "DRAFT" not in D.read_texts(doc)["en"]


def test_a_medicine_from_a_note_is_blocked_until_removed(fresh_state):
    store = fresh_state
    stay = stay_of(store, "Condition", "cond-doc-htn-note")
    drug = store.fhir.read("Condition", "cond-doc-htn-note")["note"][0]["text"].split(" was ")[0].split(". ")[-1].lower()
    sign_discharge_medrec(store, stay)
    physician = user(store, Role.PHYSICIAN)
    view = service.draft_summary(store, physician, stay)
    blocked = [i for i in red(view) if i["value"].lower() == drug]
    assert blocked and {i["code"] for i in blocked} >= {"untraceable_medication"}
    assert not view["can_sign"] and view["sign_block"]
    with pytest.raises(service.DocProblem):
        service.sign(store, physician, view["id"])
    with pytest.raises(FhirAccessDenied):  # the chart's Sign button: the signing service refuses it too
        FhirGateway(Actor.of(physician), "discharge_summary").sign_document(view["id"])
    content = view["content"]
    content["medications"]["new"] = [m for m in content["medications"]["new"] if m["name"].lower() != drug]
    edited = service.save(store, physician, view["id"], content)
    assert not red(edited) and edited["can_sign"] and edited["sign_block"] is None
    assert edited["edits"] and edited["raw_validation"]["issues"]  # the raw hallucination stays on record
    assert service.sign(store, physician, view["id"])["doc_status"] == "final"
    # WP9: the journey gets to its step after the edited summary was signed: it does not draft again (the draft
    # tool refuses a final document), it records the signature
    done = journey.discharge_summary_step(store, stay)
    assert not done.refused and done.wait_for == [] and "already signed final" in done.detail


def test_nurse_cannot_sign_a_summary_and_other_units_are_refused(fresh_state, client, login):
    store = fresh_state
    stay = stay_of(store, "Procedure", "proc-doc-ablation")
    sign_discharge_medrec(store, stay)
    doctor, nurse = login("U-DOC-09"), login("U-NURS-05")
    r = client.post(f"/api/documentation/encounters/{stay}/discharge-summary", json={}, headers=doctor)
    assert r.status_code == 200, r.text
    doc = r.json()["id"]
    assert client.post(f"/api/documentation/documents/{doc}/sign", headers=nurse).status_code == 403
    assert client.post(f"/api/documentation/encounters/{stay}/discharge-summary", json={},
                       headers=nurse).status_code == 403
    assert client.get("/api/documentation/worklist", headers=login("U-PHAR-01")).status_code == 403
    items = client.get("/api/documentation/worklist", headers=doctor).json()["items"]
    assert any(i["encounter_id"] == stay and i["summary"] == "draft" for i in items)
    icu = next(e for e in store.fhir.search("Encounter", cls="IMP", status="in-progress", unit="ICU")
               if not FhirGateway(Actor.of(user(store, Role.PHYSICIAN)), "documentation").is_in_scope(
                   ref_id(e["subject"])))
    r = client.post(f"/api/documentation/encounters/{icu['id']}/discharge-summary", json={}, headers=doctor)
    assert r.status_code == 403


def test_template_only_without_ai_consent(fresh_state):
    store = fresh_state
    stay = stay_of(store, "Condition", "cond-doc-htn-note")
    sign_discharge_medrec(store, stay)
    physician = user(store, Role.PHYSICIAN)
    pid = ref_id(store.fhir.read("Encounter", stay)["subject"])
    consent.change_consent(FhirGateway(Actor.of(physician), "consent_management"), pid, "ai_processing", False)
    calls = len(store.llm_calls)
    view = service.draft_summary(store, physician, stay)
    assert view["mode"] == "template" and view["run_id"] is None
    assert len(store.llm_calls) == calls  # no model call
    assert view["content"]["discharge_diagnoses"] == [] and view["sources"]
    diagnosis = next(s for s in view["sources"] if s["kind"] == "diagnosis")
    content = view["content"]
    content["reason_for_admission"] = {"text": f"Admitted with {diagnosis['name']}.", "source_refs": [diagnosis["ref"]]}
    content["discharge_diagnoses"] = [{"name": diagnosis["name"], "code": diagnosis["code"], "text": diagnosis["name"],
                                       "source_refs": [diagnosis["ref"]]}]
    content["discharge_diagnoses"].append({"name": "Pneumonia", "code": "", "text": "Pneumonia",
                                           "source_refs": [diagnosis["ref"]]})
    edited = service.save(store, physician, view["id"], content)
    assert any(i["code"] == "untraceable_diagnosis" for i in red(edited)) or \
        any(i["code"] == "source_mismatch" for i in red(edited))


# ---------- patient instructions ----------


def _signed_summary(store, stay: str):
    sign_discharge_medrec(store, stay)
    physician = user(store, Role.PHYSICIAN)
    view = service.draft_summary(store, physician, stay)
    return service.sign(store, physician, view["id"])


def test_instructions_need_the_signed_summary(fresh_state):
    store = fresh_state
    stay = stay_of(store, "Procedure", "proc-doc-ablation")
    out = service.draft_instructions(store, user(store, Role.NURSE), stay)
    assert out["status"] == "insufficient"


def test_bilingual_instructions_from_one_structure(fresh_state):
    store = fresh_state
    stay = stay_of(store, "Procedure", "proc-doc-ablation")
    _signed_summary(store, stay)
    nurse = user(store, Role.NURSE)
    view = service.draft_instructions(store, nurse, stay)
    assert view["kind"] == "patient_instructions" and view["default_language"] in ("zh-Hans", "zh-Hant")
    r = view["renderings"]
    keys = {lang: item_keys(r[lang]) for lang in P.LANGS}
    assert keys["en"] == keys["zh-Hans"] == keys["zh-Hant"] and keys["en"]
    content = view["content"]
    warfarin = next(m for m in content["medicines"] if m["rxcui"] == "11289")
    assert warfarin["category"] in ("changed", "continued") and "leafy_greens" in warfarin["cautions"]
    flags = {f["code"] for f in content["red_flags"]}
    assert {"bleeding_signs", "puncture_site"} <= flags
    assert "steady_greens" in {c["code"] for c in content["daily_care"]}
    for lang in ("zh-Hans", "zh-Hant"):
        check = r[lang]["readability"]
        assert check["no_abbreviations"], check["abbreviations"]
        assert check["terms_explained"], check["unexplained_terms"]
    assert "华法林" in r["zh-Hans"]["text"] and "華法林" in r["zh-Hant"]["text"]
    assert r["en"]["readability"]["grade"] <= 8.5
    assert not red(view) and view["can_sign"]
    signed = service.sign(store, nurse, view["id"])
    assert signed["doc_status"] == "final"
    texts = D.read_texts(store.fhir.read("DocumentReference", view["id"]))
    assert set(texts) == {"en", "zh-Hans", "zh-Hant"}
    out = service.deliver(store, nurse, view["id"], "portal")
    assert out["sent"] and out["url"].endswith(view["id"])
    sms = service.deliver(store, nurse, view["id"], "sms")
    pid = ref_id(store.fhir.read("Encounter", stay)["subject"])
    expected = consent.consent_status(FhirGateway(Actor.system("t"), "documentation", LocalBackend(store)),
                                      pid)["sms"] == "permit"
    assert sms["sent"] == expected


def test_instructions_follow_the_summary_signature_in_the_background(fresh_state):
    store = fresh_state
    stay = stay_of(store, "Procedure", "proc-doc-ablation")
    _signed_summary(store, stay)
    done = service.intake(store)
    assert done == [D.document_id("patient_instructions", stay)]
    doc = store.fhir.read("DocumentReference", done[0])
    assert doc["docStatus"] == "preliminary" and D.read_data(doc)["mode"] == "ai"


# ---------- handoff ----------


def test_sbar_handoff_confirmed_by_the_nurse(fresh_state):
    store = fresh_state
    nurse = user(store, Role.NURSE)
    stay = stay_of(store, "Procedure", "proc-doc-ablation")
    view = service.draft_handoff(store, nurse, stay)
    assert view["kind"] == "handoff" and view["id"].startswith(f"sbar-{stay}-")
    content = view["content"]
    assert content["situation"]["text"] and content["assessment"] and content["recommendation"]
    sources = {s["ref"] for s in view["sources"]}
    for section in ("background", "assessment", "recommendation"):
        for item in content[section]:
            assert set(item["source_refs"]) <= sources
    # nursing_handoff@2: what the input does not hold is declared, never stated (an absence has nothing to cite)
    assert "intake_output" in content["not_recorded"]
    assert not any("not recorded" in a["text"].lower() for a in content["assessment"])
    assert "Not recorded: intake and output" in D.read_texts(service._document(service._fhir(nurse, service.VIEW),
                                                                              view["id"]))["en"]
    assert not red(view)
    again = service.draft_handoff(store, nurse, stay)
    assert again["id"] == view["id"] and again["run_id"] == view["run_id"]  # one per shift
    confirmed = service.sign(store, nurse, view["id"])
    assert confirmed["doc_status"] == "final" and len(confirmed["signatures"]) == 1
    with pytest.raises(service.DocDenied):
        service.draft_handoff(store, user(store, Role.PHYSICIAN), stay)


# ---------- journey steps (no Temporal) ----------


def test_journey_step_waits_for_missing_input_or_the_signed_document(fresh_state):
    store = fresh_state
    stay = stay_of(store, "Procedure", "proc-doc-ablation")
    first = journey.discharge_summary_step(store, stay)
    task_ref, doc_ref = first.wait_for
    assert task_ref.startswith("Task/") and doc_ref == f"DocumentReference/dsum-{stay}"
    assert "insufficient information" in store.fhir.read("Task", task_ref.split("/")[1])["description"].lower()
    again = journey.discharge_summary_step(store, stay)
    assert again.wait_for == first.wait_for  # the gateway replays the same Task
    sign_discharge_medrec(store, stay)
    view = service.draft_summary(store, user(store, Role.PHYSICIAN), stay)
    step = journey.discharge_summary_step(store, stay)
    assert step.wait_for == [doc_ref] and not step.refused
    service.sign(store, user(store, Role.PHYSICIAN), view["id"])
    task = store.fhir.read("Task", task_ref.split("/")[1])
    assert task["status"] == "completed"  # the signature confirmed the journey's by-hand Task
    done = journey.discharge_summary_step(store, stay)
    assert done.wait_for == [] and "already signed" in done.detail
    step = journey.patient_instructions_step(store, stay)
    assert step.wait_for == [f"DocumentReference/pinstr-{stay}"]


def test_hospital_clock_shift_key():
    from datetime import datetime

    assert D.shift_key(datetime(2026, 10, 9, 7, 0)) == "20261009D"
    assert D.shift_key(datetime(2026, 10, 9, 18, 59)) == "20261009D"
    assert D.shift_key(datetime(2026, 10, 9, 19, 0)) == "20261009N"
    assert D.shift_key(datetime(2026, 10, 10, 6, 59)) == "20261009N"

