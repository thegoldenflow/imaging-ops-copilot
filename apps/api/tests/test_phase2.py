"""Phase 2 acceptance: requisition pipeline (systems 5-10)."""

import time
from datetime import datetime, timedelta

from app.core.store import get_store
from app.integrations.mocks import MOCK_CONFIG
from app.modules.mri_safety import service as mri
from app.modules.priors import service as priors
from app.modules.requisitions import service as requisitions


def _process_all():
    requisitions.process_pending(get_store())


def _approve_and_book(client, login, req_id, protocol_id):
    rad, fd = login("U-RAD"), login("U-FD")
    assert client.post(f"/api/requisitions/{req_id}/protocol/approve", headers=rad,
                       json={"protocol_id": protocol_id}).status_code == 200
    r = client.post(f"/api/requisitions/{req_id}/book", headers=fd)
    assert r.status_code == 200, r.text
    return r.json()["appointment"]


# ---------- Shared extraction ----------

def test_new_requisition_is_extracted_triaged_and_queued_within_a_minute(client, login):
    headers = login("U-FD")
    sample = client.get("/api/requisitions/samples", headers=headers).json()["samples"][0]
    started = time.monotonic()
    created = client.post("/api/requisitions", headers=headers, json={
        "patient_id": sample["patient_id"], "referrer_id": sample["referrer_id"], "text": sample["text"]}).json()
    _process_all()
    assert time.monotonic() - started < 60
    detail = client.get(f"/api/requisitions/{created['id']}", headers=login("U-RAD")).json()
    assert detail["summary"]["status"] == "ready"
    assert detail["triage"]["ai_priority"] in ("P1", "P2", "P3", "P4")
    assert detail["protocol"]["primary_id"]
    queue = client.get("/api/requisitions", headers=headers).json()["requisitions"]
    assert any(r["id"] == created["id"] for r in queue)


def test_extraction_fields_carry_source_quotes_from_the_text(client, login):
    _process_all()
    detail = client.get("/api/requisitions/REQ-NEW2", headers=login("U-RAD")).json()
    fields, text = detail["extraction"]["fields"], detail["text"]
    for key in ("requested_exam", "clinical_indication"):
        assert fields[key]["source_quote"] and fields[key]["source_quote"] in text
    assert any("diabetes" in f["value"].lower() for f in fields["renal_or_diabetes"])


def test_staff_can_correct_an_extracted_field(client, login):
    _process_all()
    headers = login("U-RAD")
    r = client.patch("/api/requisitions/REQ-NEW3/fields/clinical_indication", headers=headers,
                     json={"value": "Swollen painful left calf for 2 days, query DVT"}).json()
    field = r["extraction"]["fields"]["clinical_indication"]
    assert field["confidence"] == 1.0 and field["corrected_by"] == "Dr. Priya Raman"
    assert r["extraction"]["corrections"][-1]["field"] == "clinical_indication"


def test_extraction_sends_no_patient_identifiers_to_the_model():
    from app.llm.gateway import LlmGateway, set_gateway
    from app.llm.providers import MockProvider

    seen = []

    class Spy(MockProvider):
        def complete_json(self, **kwargs):
            seen.append(kwargs["text"])
            return super().complete_json(**kwargs)

    set_gateway(LlmGateway(Spy(latency_s=0)))
    store = get_store()
    req = store.requisitions["REQ-NEW1"]
    patient = store.patients[req.patient_id]
    requisitions.process(store, req)
    assert seen and all(patient.family_name not in t and patient.health_card not in t for t in seen)


# ---------- System 5: triage ----------

def test_override_requires_a_reason_and_is_audited(client, login):
    _process_all()
    headers = login("U-RAD")
    assert client.post("/api/requisitions/REQ-NEW3/triage/override", headers=headers,
                       json={"priority": "P3", "reason": ""}).status_code == 422
    r = client.post("/api/requisitions/REQ-NEW3/triage/override", headers=headers,
                    json={"priority": "P3", "reason": "Referrer called: swelling has resolved"}).json()
    assert r["triage"]["final_priority"] == "P3" and r["triage"]["review_action"] == "overridden"
    assert any(e.action == "override" and e.resource_id == "REQ-NEW3" for e in get_store().audit.events())
    agreement = client.get("/api/requisitions/triage/agreement", headers=headers).json()
    assert agreement["reviewed"] > 0 and 0 <= agreement["agreement"] <= 1


def test_queue_is_ordered_by_days_left_to_target(client, login):
    _process_all()
    rows = client.get("/api/requisitions", headers=login("U-OPS")).json()["requisitions"]
    days = [r["days_left"] for r in rows if r["days_left"] is not None]
    assert days == sorted(days)


def test_only_radiologists_triage(client, login):
    _process_all()
    r = client.post("/api/requisitions/REQ-NEW3/triage/confirm", headers=login("U-FD"))
    assert r.status_code == 403


# ---------- System 6: protocol ----------

def test_every_new_requisition_gets_a_protocol_and_duration_sets_slot(client, login):
    _process_all()
    store = get_store()
    for req in store.requisitions.values():
        assert req.id in store.modules["protocols"]
    appt = _approve_and_book(client, login, "REQ-NEW3", "US-VEN-LE")
    minutes = (datetime.fromisoformat(appt["end"]) - datetime.fromisoformat(appt["start"])).total_seconds() / 60
    assert minutes == 30 and appt["protocol_id"] == "US-VEN-LE"
    stats = client.get("/api/protocols/stats", headers=login("U-RAD")).json()
    assert stats["approved"] > 0 and stats["adoption_rate"] is not None


def test_booking_requires_an_approved_protocol(client, login):
    _process_all()
    r = client.post("/api/requisitions/REQ-NEW3/book", headers=login("U-FD"))
    assert r.status_code == 409


# ---------- System 7: contrast ----------

def test_contrast_status_and_threshold_recompute(client, login):
    _process_all()
    md = login("U-MD")
    detail = client.get("/api/requisitions/REQ-NEW2", headers=md).json()
    assert detail["contrast"]["status"] == "needs_premedication"
    assert any("150 days old" in b for b in detail["contrast"]["basis"])
    checks = client.get("/api/contrast/checks", headers=md).json()
    assert all(row["check"]["status"] and row["check"]["basis"] for row in checks["checks"])
    before = checks["counts"].get("needs_review", 0)
    config = checks["config"] | {"egfr_threshold": 90}
    assert client.put("/api/contrast/config", headers=md, json=config).status_code == 200
    after = client.get("/api/contrast/checks", headers=md).json()["counts"].get("needs_review", 0)
    assert after > before


def test_only_medical_director_sets_thresholds(client, login):
    config = client.get("/api/contrast/config", headers=login("U-RAD")).json()
    assert client.put("/api/contrast/config", headers=login("U-RAD"), json=config).status_code == 403


# ---------- System 8: MRI safety ----------

def test_flagged_mri_appointment_cannot_be_confirmed_before_review(client, login):
    _process_all()
    store = get_store()
    appt = _approve_and_book(client, login, "REQ-NEW1", "MR-BR-TUMOUR")
    screening = mri.for_requisition(store, "REQ-NEW1")
    assert screening.status == "flagged"  # the requisition mentions a cochlear implant
    r = client.post(f"/api/public/mri-screening/{screening.token}",
                    json={"answers": {"cochlear_implant": True}, "free_text": "右耳有人工耳蜗", "language": "zh"}).json()
    assert r == {"received": True, "needs_review": True}
    assert screening.devices[0]["category"] == "ear_implant"
    confirmation = next(m for m in store.outbox.values() if m.appointment_id == appt["id"] and m.kind == "booking_confirmation")
    ops = login("U-OPS")
    assert client.post(f"/api/frontdesk/outbox/{confirmation.id}/reply", headers=ops, json={"text": "C"}).json()["action"] == "blocked"
    assert client.post(f"/api/mri-screening/{screening.id}/review", headers=login("U-FD"),
                       json={"decision": "cleared", "note": "ok"}).status_code == 403
    assert client.post(f"/api/mri-screening/{screening.id}/review", headers=login("U-TECH"),
                       json={"decision": "cleared", "note": "Implant card checked; magnet removal planned"}).status_code == 200
    assert client.post(f"/api/frontdesk/outbox/{confirmation.id}/reply", headers=ops, json={"text": "C"}).json()["action"] == "confirmed"


def test_no_flags_never_auto_clears(client, login):
    _process_all()
    store = get_store()
    req = store.requisitions["REQ-NEW1"]
    store.modules["extractions"][req.id].fields["implant_hints"] = []
    store.modules.pop("mri_screenings", None)
    screening = mri.create_for(store, req)
    mri.submit(store, screening, {}, "", "en")
    assert screening.status == "no_flags" and screening.review_decision is None


def test_questionnaire_link_goes_out_in_patient_language(client, login):
    _process_all()
    store = get_store()
    patient = store.patients[store.requisitions["REQ-NEW1"].patient_id]
    msg = next(m for m in store.outbox.values() if m.kind == "mri_screening")
    assert msg.language == patient.preferred_language == "pa" and "/mri-screening/" in msg.body


# ---------- System 9: prep ----------

def test_prep_goes_out_in_preferred_language_only_when_approved(client, login):
    _process_all()
    store = get_store()
    appt = _approve_and_book(client, login, "REQ-NEW1", "MR-BR-TUMOUR")
    prep_msg = next(m for m in store.outbox.values() if m.appointment_id == appt["id"] and m.kind == "prep")
    assert prep_msg.language == "en" and "not approved" in prep_msg.note  # Punjabi MRI text is still a draft
    md = login("U-MD")
    assert client.post("/api/prep/templates/mri/pa/approve", headers=md).status_code == 200
    appt2 = _approve_and_book(client, login, "REQ-NEW2", "CT-AP-CONTRAST")
    english_patient = store.patients[appt2["patient_id"]]
    msg2 = next(m for m in store.outbox.values() if m.appointment_id == appt2["id"] and m.kind == "prep")
    assert msg2.language == english_patient.preferred_language


def test_ai_drafted_translation_starts_unapproved(client, login):
    md = login("U-MD")
    draft = client.post("/api/prep/templates/fasting/zh/draft", headers=md).json()
    assert draft["status"] == "draft" and draft["source"] == "ai"
    templates = {t["key"]: t for t in client.get("/api/prep/templates", headers=md).json()["templates"]}
    assert templates["fasting"]["translations"]["zh"]["status"] == "draft"


# ---------- System 10: prior imaging ----------

def test_booking_creates_retrieval_tasks_that_complete(client, login):
    _process_all()
    store = get_store()
    appt = _approve_and_book(client, login, "REQ-NEW2", "CT-AP-CONTRAST")
    tasks = [t for t in priors.tasks(store).values() if t.appointment_id == appt["id"]]
    assert tasks and tasks[0].facility == "Lakeview Diagnostics"
    priors.process_due(store)
    assert tasks[0].status == "received" and tasks[0].imported_study_ids
    study = store.studies[tasks[0].imported_study_ids[0]]
    assert study.source_facility == "Lakeview Diagnostics" and study.prior_for_appointment_id == appt["id"]


def test_failing_archive_retries_with_backoff_then_fails(client, login):
    _process_all()
    store = get_store()
    MOCK_CONFIG["outside_archive"].failure_rate = 1.0
    MOCK_CONFIG["outside_archive"].latency_ms = 0
    try:
        appt = _approve_and_book(client, login, "REQ-NEW2", "CT-AP-CONTRAST")
        task = next(t for t in priors.tasks(store).values() if t.appointment_id == appt["id"])
        now = datetime.now()
        for _ in range(priors.MAX_ATTEMPTS):
            priors.process_due(store, now)
            now += timedelta(minutes=5)
        assert task.status == "failed" and task.attempts == priors.MAX_ATTEMPTS
        assert sum("Retrying" in e["text"] for e in task.events) == priors.MAX_ATTEMPTS - 1
    finally:
        MOCK_CONFIG["outside_archive"].failure_rate = 0.0
        MOCK_CONFIG["outside_archive"].latency_ms = 300
