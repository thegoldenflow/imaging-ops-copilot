"""Phase 4 acceptance: business and compliance (systems 15-21)."""

from datetime import datetime, timedelta

from app.core.models import ACTIVE_STATUSES
from app.core.store import get_store
from app.modules.inventory import service as inventory


def _next_appointment(site_id: str | None = None, exam_codes: tuple[str, ...] | None = None, modality: str | None = None):
    store = get_store()
    now = datetime.now()
    return min((a for a in store.appointments.values()
                if a.status in ACTIVE_STATUSES and a.end >= now and (site_id is None or a.site_id == site_id)
                and (exam_codes is None or a.exam_code in exam_codes)
                and (modality is None or store.exams[a.exam_code].modality == modality)), key=lambda a: a.start)


def _audit(action: str, resource_type: str, outcome: str = "allowed"):
    return [e for e in get_store().audit.events()
            if e.action == action and e.resource_type == resource_type and e.outcome == outcome]


# ---------- System 15 · Inventory ----------

def test_completing_a_contrast_ct_deducts_stock_and_raises_low_stock(client, login):
    store = get_store()
    item = store.modules["inventory_items"]["INV-LKS-IOHEXOL-350"]
    kit = store.modules["inventory_items"]["INV-LKS-INJECTOR-KIT"]
    before, kits_before = item.quantity, kit.quantity
    first_lot = min(item.lots, key=lambda x: x.expiry)
    first_lot_before = first_lot.quantity
    assert before == item.reorder_point + 1 and inventory.open_order(store, item) is None

    appt = _next_appointment("LKS", ("CT_CHEST_C", "CT_ABD_PEL"))
    appt.protocol_id = None  # use the exam's own contrast flag
    r = client.post(f"/api/scheduling/appointments/{appt.id}/complete", headers=login("U-TECH"))
    assert r.status_code == 200, r.text
    assert item.quantity == before - 1 and kit.quantity == kits_before - 1
    assert first_lot.quantity == first_lot_before - 1  # first-expiring lot used first
    assert any(m.appointment_id == appt.id and m.item_id == item.id for m in inventory.movements(store))

    data = client.get("/api/inventory", headers=login("U-TECH")).json()
    assert {i["site_id"] for i in data["items"]} == {"LKS"}  # site-scoped technologist
    low = [a for a in data["alerts"] if a["kind"] == "low_stock" and a["item_id"] == item.id]
    assert low and low[0]["order_id"]
    po = store.modules["inventory_orders"][low[0]["order_id"]]
    assert po.status == "draft" and po.quantity == item.reorder_qty
    assert any(m.kind == "inventory_low" and po.id in m.body for m in store.outbox.values())


def test_non_contrast_and_xray_exams_use_no_contrast():
    store = get_store()
    appt = _next_appointment("LKS", ("CT_HEAD",))
    assert inventory.usage_for(store, appt) == {}
    appt = _next_appointment("LKS", ("XR_CHEST",))
    assert inventory.usage_for(store, appt) == {}
    appt = _next_appointment("LKS", modality="US")
    assert inventory.usage_for(store, appt) == {"US-GEL": 1}


def test_expiry_and_low_stock_alerts_are_seeded(client, login):
    data = client.get("/api/inventory", headers=login("U-OPS")).json()
    kinds = {(a["kind"], a["item_id"]) for a in data["alerts"]}
    assert ("expired", "INV-EVW-SALINE-10") in kinds
    assert ("expiring", "INV-NGT-GADOBUTROL-7.5") in kinds
    assert ("low_stock", "INV-WBK-US-GEL") in kinds


def test_expired_lots_are_never_consumed():
    store = get_store()
    saline = inventory.items(store)["INV-EVW-SALINE-10"]
    expired = next(lot for lot in saline.lots if lot.expiry < datetime.now().date())
    left = expired.quantity
    inventory.consume(store, saline, 2, datetime.now(), "test", None)
    assert expired.quantity == left


def test_order_submit_receive_and_count_correction(client, login):
    store = get_store()
    ops = login("U-OPS")
    gel = inventory.items(store)["INV-WBK-US-GEL"]
    po = inventory.open_order(store, gel)
    assert po.status == "draft"
    assert client.post(f"/api/inventory/orders/{po.id}/submit", json={"quantity": 250}, headers=ops).status_code == 200
    before = gel.quantity
    r = client.post(f"/api/inventory/orders/{po.id}/receive", headers=ops)
    assert r.status_code == 200 and gel.quantity == before + 250 and po.status == "received"
    assert not any(a["kind"] == "low_stock" and a["item_id"] == gel.id
                   for a in client.get("/api/inventory", headers=ops).json()["alerts"])

    lot = gel.lots[-1].lot
    r = client.post(f"/api/inventory/items/{gel.id}/adjust", json={"lot": lot, "counted": 240, "reason": "Monthly count"},
                    headers=ops)
    assert r.status_code == 200 and gel.lots[-1].quantity == 240
    assert _audit("update", "inventory_item")


def test_technologist_cannot_touch_another_sites_stock(client, login):
    r = client.post("/api/inventory/items/INV-EVW-SALINE-10/lots/X/discard", headers=login("U-TECH"))
    assert r.status_code == 403
    assert _audit("post", "inventory_item", "denied")
    assert client.get("/api/inventory", headers=login("U-FD")).status_code == 403


# ---------- System 16 · Referral analytics ----------

def test_referral_dashboard_filters_and_trends(client, login):
    ops = login("U-OPS")
    data = client.get("/api/referrals/overview", headers=ops).json()
    k = data["kpis"]
    assert k["total"] == sum(data["series"][0]["values"]) and len(data["labels"]) == 12
    assert k["last_week"] == data["series"][0]["values"][-1]
    assert sum(b["total"] for b in data["by_modality"]) == k["total"]
    ct = client.get("/api/referrals/overview?modality=CT&site_id=LKS", headers=ops).json()
    assert 0 < ct["kpis"]["total"] < k["total"] and [b["key"] for b in ct["by_modality"]] == ["CT"]
    assert [b["key"] for b in ct["by_site"]] == ["LKS"]
    one = client.get("/api/referrals/overview?specialty=Neurology", headers=ops).json()
    assert {r["specialty"] for r in one["referrers"]} == {"Neurology"}
    assert client.get("/api/referrals/overview", headers=login("U-FD")).status_code == 403


def test_planted_declines_form_the_visit_list(client, login):
    data = client.get("/api/referrals/overview", headers=login("U-OPS")).json()
    planted = set(get_store().modules["referral_planted_declines"])
    flagged = {r["referrer_id"] for r in data["visit_list"]}
    assert planted <= flagged and len(flagged) <= len(planted) + 2
    r = client.put(f"/api/referrals/visits/{data['visit_list'][0]['referrer_id']}",
                   json={"status": "planned", "note": "Lunch visit"}, headers=login("U-OPS"))
    assert r.status_code == 200


def test_weekly_summary_numbers_all_come_from_facts(client, login):
    ops = login("U-OPS")
    summary = client.post("/api/referrals/summary", headers=ops).json()["summary"]
    assert summary["ai_status"] == "ok" and summary["status"] == "draft"
    data = client.get("/api/referrals/overview", headers=ops).json()
    tiles = {"kpi-last-week": str(data["kpis"]["last_week"]), "kpi-prior-week": str(data["kpis"]["prior_week"]),
             "kpi-change": data["kpis"]["week_change_label"], "kpi-avg": str(data["kpis"]["avg_per_week"]),
             "kpi-declining": str(data["kpis"]["declining"]), "kpi-week": data["kpis"]["week_label"]}
    tiles |= {f"mod-{b['key']}-week": str(b["last_week"]) for b in data["by_modality"]}
    tiles |= {f"site-{b['key']}-week": str(b["last_week"]) for b in data["by_site"]}
    tiles |= {f"ref-{r['referrer_id']}-week": str(r["last_week"]) for r in data["referrers"]}
    tiles |= {f"visit-{r['referrer_id']}-change": r["change_label"] for r in data["visit_list"]}
    numeric = 0
    for sentence in [summary["headline"], *summary["sentences"]]:
        for seg in sentence:
            if "fact" in seg:
                assert seg["tile"].split("-")[0] in ("kpi", "mod", "site", "ref", "visit")
                if seg["tile"] in tiles:  # value cells; name facts point at the row
                    assert tiles[seg["tile"]] == seg["value"], seg
                    numeric += 1
            else:
                assert not any(ch.isdigit() for ch in seg["text"])
    assert numeric >= 5
    assert client.post("/api/referrals/summary/approve", headers=ops).json()["summary"]["status"] == "approved"


def test_summary_with_invented_numbers_fails_validation():
    from app.llm.gateway import LlmGateway, set_gateway
    from app.llm.providers import MockProvider, MOCK_FIXTURES
    from app.modules.referrals import service as referrals

    original = MOCK_FIXTURES["referral_weekly_summary"]
    MOCK_FIXTURES["referral_weekly_summary"] = lambda text, images, attempt: {
        "headline": "Referrals up 12%", "sentences": ["We had {last_week_total} referrals.", "About 40 more than usual."]}
    try:
        set_gateway(LlmGateway(MockProvider(latency_s=0)))
        summary = referrals.generate_summary(get_store(), datetime.now(), "test")
    finally:
        MOCK_FIXTURES["referral_weekly_summary"] = original
    assert summary.ai_status == "needs_human" and summary.sentences == []
    assert get_store().llm_calls[-1].outcome == "needs_human"


# ---------- System 17 · Referrer portal ----------

def test_referrer_sees_only_own_patients_and_others_are_denied(client, login):
    ref = login("U-REF")
    data = client.get("/api/portal/patients", headers=ref).json()
    ids = {p["id"] for p in data["patients"]}
    assert "PT-DEMO1" in ids and len(ids) >= 6
    assert client.get("/api/portal/patients/PT-DEMO1", headers=ref).status_code == 200
    other = next(pid for pid in get_store().patients if pid not in ids)
    r = client.get(f"/api/portal/patients/{other}", headers=ref)
    assert r.status_code == 403
    denied = _audit("get", "patient", "denied")
    assert denied and denied[-1].resource_id == other and denied[-1].role == "referrer"
    # Unknown ids get the same answer, so ids cannot be probed.
    assert client.get("/api/portal/patients/PT-NOPE", headers=ref).status_code == 403
    # Staff cannot use the portal API.
    assert client.get("/api/portal/patients", headers=login("U-FD")).status_code == 403


def test_portal_requisition_enters_the_pipeline(client, login):
    from app.modules.requisitions import service as requisitions

    ref = login("U-REF")
    body = {"new_patient": {"given_name": "Ana", "family_name": "Example", "dob": "1958-02-11", "sex": "F",
                            "phone": "+1-416-555-0101", "health_card": "1234567890", "health_card_version": "AB",
                            "preferred_language": "fr"},
            "exam_requested": "CT abdomen and pelvis with contrast",
            "clinical_information": "Right lower quadrant pain and weight loss over 2 months",
            "relevant_history": "Type 2 diabetes", "allergies": "", "urgent": True, "notes": "Please call with results"}
    r = client.post("/api/portal/requisitions", json=body, headers=ref)
    assert r.status_code == 200, r.text
    req = get_store().requisitions[r.json()["id"]]
    assert req.channel == "portal" and req.status == "received" and req.referrer_id == "R-DEMO"
    requisitions.process_pending(get_store())
    staff = client.get(f"/api/requisitions/{req.id}", headers=login("U-RAD")).json()
    assert staff["triage"]["ai_priority"] in ("P1", "P2") and staff["protocol"]["primary_id"] == "CT-AP-CONTRAST"
    mine = client.get("/api/portal/requisitions", headers=ref).json()["requisitions"]
    row = next(x for x in mine if x["id"] == req.id)
    assert row["status_text"] == "With the radiologist for review" and row["confirmed_priority"] is None
    assert req.patient_id in {p["id"] for p in client.get("/api/portal/patients", headers=ref).json()["patients"]}
    assert _audit("create", "requisition")


def test_referrer_cannot_order_for_someone_elses_patient(client, login):
    ref = login("U-REF")
    from app.modules.portal.service import patient_ids

    mine = patient_ids(get_store(), "R-DEMO")
    other = next(pid for pid in get_store().patients if pid not in mine)
    r = client.post("/api/portal/requisitions", json={"patient_id": other, "exam_requested": "MRI knee",
                                                      "clinical_information": "Knee pain"}, headers=ref)
    assert r.status_code == 403 and _audit("post", "patient", "denied")
    assert not any(q.patient_id == other and q.channel == "portal" for q in get_store().requisitions.values())


# ---------- System 18 · Billing ----------

def test_reconciliation_finds_every_planted_discrepancy(client, login):
    data = client.get("/api/billing/overview", headers=login("U-ADMIN")).json()
    found = {(r["kind"], r["appointment_id"]) for r in data["discrepancies"]}
    planted = set(get_store().modules["billing_planted"])
    assert {k for k, _ in planted} == set(data["kinds"])  # every kind is seeded
    assert planted <= found
    assert found == planted  # and nothing else is flagged in the seeded data
    assert data["kinds"]["missing"]["resolved"] == 1


def test_resolve_export_and_access(client, login):
    ops = login("U-OPS")
    rows = client.get("/api/billing/overview", headers=ops).json()["discrepancies"]
    dup = next(r for r in rows if r["kind"] == "duplicate" and r["status"] == "open")
    assert client.post(f"/api/billing/discrepancies/{dup['id']}/resolve", json={"outcome": "written_off"},
                       headers=ops).status_code == 422  # write-off needs a reason
    r = client.post(f"/api/billing/discrepancies/{dup['id']}/resolve",
                    json={"outcome": "duplicate_voided", "note": "Second claim voided"}, headers=ops)
    assert r.status_code == 200 and r.json()["resolved_by"] == "Jordan Lee"
    csv_text = client.get("/api/billing/export.csv", headers=ops).text
    assert csv_text.splitlines()[0].startswith("id,kind_label,status") and dup["id"] in csv_text
    assert _audit("export", "billing_discrepancies")
    assert client.get("/api/billing/overview", headers=login("U-FD")).status_code == 403
    assert client.post("/api/billing/discrepancies/missing.AP-NOPE/resolve", json={"outcome": "no_action"},
                       headers=ops).status_code == 404


def test_cancelled_exam_billed_is_flagged_live(client, login):
    store = get_store()
    from app.modules.billing import service as billing

    appt = next(a for a in store.appointments.values() if a.status == "cancelled"
                and datetime.now() - timedelta(days=5) < a.start < datetime.now()
                and not any(c.appointment_id == a.id for c in billing.claims(store).values()))
    billing.claims(store)["CLM-X"] = billing.Claim(id="CLM-X", appointment_id=appt.id, patient_id=appt.patient_id,
                                                   site_id=appt.site_id, payer="ohip", fee_code="SYN-X101", amount=34.5,
                                                   service_date=appt.start.date().isoformat(), submitted_at=datetime.now(),
                                                   status="submitted")
    rows = client.get("/api/billing/overview", headers=login("U-OPS")).json()["discrepancies"]
    assert any(r["id"] == f"not_performed.{appt.id}" for r in rows)


# ---------- System 19 · Patient feedback ----------

def test_completion_sends_survey_in_patient_language_and_low_rating_alerts(client, login):
    from app.modules.feedback import service as feedback

    store = get_store()
    appt = _next_appointment("LKS")
    store.patients[appt.patient_id].preferred_language = "zh"
    assert client.post(f"/api/scheduling/appointments/{appt.id}/complete", headers=login("U-TECH")).status_code == 200
    survey = next(s for s in feedback.surveys(store).values() if s.appointment_id == appt.id)
    msg = next(m for m in store.outbox.values() if m.kind == "feedback_survey" and m.appointment_id == appt.id)
    assert survey.language == "zh" and f"/feedback/{survey.token}" in msg.body and "感谢" in msg.body

    info = client.get(f"/api/public/feedback/{survey.token}").json()
    assert info["language"] == "zh" and not info["submitted"]
    r = client.post(f"/api/public/feedback/{survey.token}", json={"rating": 1, "comment": "等了一个多小时，前台态度差。"})
    assert r.status_code == 200
    resp = feedback.responses(store)[r.json()["id"]]
    alert = next(a for a in feedback.alerts(store).values() if a.response_id == resp.id)  # immediate, no AI needed
    assert alert.status == "open" and "site manager" in alert.notified
    assert any(m.kind == "feedback_alert" and resp.id in m.body for m in store.outbox.values())
    assert client.post(f"/api/public/feedback/{survey.token}", json={"rating": 5}).status_code == 409

    feedback.process_pending(store)
    assert resp.ai_status == "ok" and resp.ai_sentiment == "negative"
    assert {"wait_time", "staff_attitude"} <= set(resp.ai_themes)
    assert any(c.task == "feedback_classify" for c in store.llm_calls)


def test_negative_comment_with_good_rating_alerts_after_ai(client):
    from app.modules.feedback import service as feedback

    store = get_store()
    survey = next(s for s in feedback.surveys(store).values() if s.status == "sent")
    resp = feedback.submit(store, survey, 3, "The waiting room was dirty and the technologist was rude.", datetime.now())
    assert not any(a.response_id == resp.id for a in feedback.alerts(store).values())
    feedback.process_pending(store)
    assert any(a.response_id == resp.id and "AI" in a.reason for a in feedback.alerts(store).values())


def test_ai_unavailable_keeps_feedback_working(client):
    from app.llm.gateway import LlmGateway, set_gateway
    from app.llm.providers import ProviderUnavailable
    from app.modules.feedback import service as feedback

    class Down:
        mode = "anthropic"

        def complete_json(self, **_):
            raise ProviderUnavailable("timeout")

    set_gateway(LlmGateway(Down()))
    store = get_store()
    survey = next(s for s in feedback.surveys(store).values() if s.status == "sent")
    resp = feedback.submit(store, survey, 2, "Too long a wait", datetime.now())
    feedback.process_pending(store)
    assert resp.ai_status == "unavailable" and resp.sentiment is None
    assert any(a.response_id == resp.id for a in feedback.alerts(store).values())  # rule still fired


def test_feedback_dashboard_confirm_and_follow_up(client, login):
    from app.modules.feedback import service as feedback

    ops = login("U-OPS")
    feedback.process_pending(get_store())
    data = client.get("/api/feedback/overview", headers=ops).json()
    assert len(data["by_site"]) == 5 and data["kpis"]["open_alerts"] > 0
    wbk = next(s for s in data["by_site"] if s["site_id"] == "WBK")
    others = [s["avg_rating_30d"] for s in data["by_site"] if s["site_id"] != "WBK" and s["avg_rating_30d"]]
    assert wbk["avg_rating_30d"] < min(others)  # the seeded bad month
    item = next(r for r in data["responses"] if not r["confirmed_by"] and r["ai_status"] in ("ok", "seeded"))
    r = client.post(f"/api/feedback/responses/{item['id']}/confirm", json={"sentiment": "neutral", "themes": ["billing"]},
                    headers=ops)
    assert r.status_code == 200 and r.json()["confirmed_by"] == "Jordan Lee" and r.json()["themes"] == ["billing"]
    assert client.post(f"/api/feedback/responses/{item['id']}/confirm", json={"sentiment": "angry", "themes": []},
                       headers=ops).status_code == 422
    alert = next(a for a in data["alerts"] if a["status"] == "open")
    r = client.post(f"/api/feedback/alerts/{alert['id']}/follow-up", json={"note": "Called the patient"}, headers=ops)
    assert r.status_code == 200 and r.json()["status"] == "followed_up"
    fd = client.get("/api/feedback/overview", headers=login("U-FD")).json()
    assert {s["site_id"] for s in fd["by_site"]} == {"LKS"}


# ---------- System 20 · PHIPA access monitoring ----------

def test_every_planted_anomaly_is_caught_with_evidence(client, login):
    data = client.get("/api/phipa/alerts", headers=login("U-ADMIN")).json()
    caught = {(a["rule"], a["user_id"]) for a in data["alerts"]}
    planted = set(get_store().modules["phipa_planted"])
    assert {r for r, _ in planted} == set(data["rules"])  # one planted case per rule
    assert planted <= caught
    events = {e.seq: e for e in get_store().audit.events()}
    for a in data["alerts"]:
        assert 0 < a["risk"] <= 100 and a["evidence"]
        for ev in a["evidence"]:
            assert events[ev["seq"]].resource_id == ev["resource_id"]  # evidence points into the hash-chained log
    assert get_store().audit.verify() == (True, None)
    assert client.get("/api/phipa/alerts", headers=login("U-OPS")).status_code == 403


def test_live_access_is_monitored(client, login):
    """Three refused requests in a row from live traffic raise an alert."""
    from app.modules.phipa import service as phipa

    store = get_store()
    fd = login("U-FD")
    for appt_id in [a.id for a in store.appointments.values() if a.site_id == "NGT"][:3]:
        assert client.post(f"/api/scheduling/appointments/{appt_id}/cancel", json={"reason": "x"},
                           headers=fd).status_code == 403
    alerts = phipa.detect(store)
    assert any(a["rule"] == "repeated_denials" and a["user_id"] == "U-FD" for a in alerts)


def test_investigation_trail_and_report(client, login):
    admin = login("U-ADMIN")
    data = client.get("/api/phipa/alerts?status=new", headers=admin).json()
    alert = next(a for a in data["alerts"] if a["rule"] == "own_record")
    url = f"/api/phipa/alerts/{alert['id']}/actions"
    assert client.post(url, json={"action": "close", "outcome": "justified", "note": ""}, headers=admin).status_code == 422
    assert client.post(url, json={"action": "assign", "assignee": "Casey Brooks"}, headers=admin).status_code == 200
    assert client.post(url, json={"action": "note", "note": "Asked Sam Rivera; he checked his own booking."},
                       headers=admin).status_code == 200
    r = client.post(url, json={"action": "close", "outcome": "education", "note": "Reminded of the self-access policy."},
                    headers=admin)
    inv = r.json()["investigation"]
    assert inv["status"] == "closed" and [t["action"] for t in inv["trail"]] == ["assign", "note", "close"]
    assert all(t["by"] == "Casey Brooks" and t["ts"] for t in inv["trail"])
    assert client.post(url, json={"action": "note", "note": "late"}, headers=admin).status_code == 422
    assert len(_audit("investigation_close", "phipa_alert")) == 1  # each step is in the audit log too
    rep = client.get("/api/phipa/report", headers=admin).json()
    assert rep["alerts"] >= 6 and rep["by_rule"]["own_record"]["closed"] == 1 and rep["audit_chain"]["intact"]
    csv_text = client.get("/api/phipa/export.csv", headers=admin).text
    assert alert["id"] in csv_text and _audit("export", "phipa_alerts")


# ---------- System 21 · Inspection readiness ----------

def test_reminders_fire_at_each_stage_and_once(client, login):
    from app.modules.inspection import service as inspection

    store = get_store()
    doc = inspection.documents(store)["CRED-U-RAD3-REGISTRATION"]
    now = datetime.now()
    doc.due = (now + timedelta(days=90)).date()
    assert not [r for r in inspection.process_reminders(store, now) if r["doc_id"] == doc.id]  # not yet
    doc.due = (now + timedelta(days=25)).date()
    new = [r for r in inspection.process_reminders(store, now) if r["doc_id"] == doc.id]
    assert [r["stage"] for r in new] == [30]
    assert any(m.kind == "inspection_reminder" and "Aisha Nwosu" in m.body for m in store.outbox.values())
    assert not inspection.process_reminders(store, now)  # sent once per stage
    later = [r for r in inspection.process_reminders(store, now + timedelta(days=20)) if r["doc_id"] == doc.id]
    assert [r["stage"] for r in later] == [7]
    overdue = [r for r in inspection.process_reminders(store, now + timedelta(days=26)) if r["doc_id"] == doc.id]
    assert [r["stage"] for r in overdue] == [0]
    data = client.get("/api/inspection/overview", headers=login("U-OPS")).json()
    assert any(r["doc_id"] == "CRED-U-TECH2-REGISTRATION" and r["stage"] == 30 for r in data["reminders"])


def test_checklist_reflects_records_and_renewal_fixes_it(client, login):
    ops = login("U-OPS")
    data = client.get("/api/inspection/overview", headers=ops).json()
    items = {c["key"]: c for c in data["checklist"]}
    assert not items["Preventive maintenance"]["ok"] and "EQ-NGT-MRI1-PM" in items["Preventive maintenance"]["evidence"]
    assert not items["BLS / CPR"]["ok"] and items["Registration"]["ok"] and items["qa_report"]["ok"]
    today = datetime.now().date()
    r = client.put("/api/inspection/documents/EQ-NGT-MRI1-PM/record",
                   json={"performed": today.isoformat(), "due": (today + timedelta(days=182)).isoformat(), "result": "Done"},
                   headers=ops)
    assert r.status_code == 200 and r.json()["status"] == "ok"
    items = {c["key"]: c for c in client.get("/api/inspection/overview", headers=ops).json()["checklist"]}
    assert items["Preventive maintenance"]["ok"]
    assert client.put("/api/inspection/documents/EQ-NGT-MRI1-PM/record", json={"performed": today.isoformat(),
                      "due": today.isoformat()}, headers=login("U-TECH")).status_code == 403


def test_policy_qa_cites_verbatim_and_admits_when_not_found(client, login):
    from app.modules.inspection import service as inspection

    tech = login("U-TECH")
    r = client.post("/api/inspection/ask", json={"question": "How long do outpatients stay after a contrast injection?"},
                    headers=tech).json()
    assert r["found"] and r["ai_status"] == "ok" and r["citations"]
    store = get_store()
    for c in r["citations"]:
        doc = inspection.documents(store)[c["doc_id"]]
        section = next(s for s in doc.current.sections if s.id == c["chunk_id"])
        assert c["quote"] in section.text
    assert r["citations"][0]["doc_id"] == "POL-CONTRAST"
    r = client.post("/api/inspection/ask", json={"question": "What is the staff parking validation fee?"}, headers=tech).json()
    assert not r["found"] and r["citations"] == [] and "could not find" in r["answer"]


def test_policy_qa_rejects_invented_citations():
    from app.llm.providers import MOCK_FIXTURES
    from app.modules.inspection import service as inspection

    original = MOCK_FIXTURES["policy_qa"]
    MOCK_FIXTURES["policy_qa"] = lambda text, images, attempt: {
        "found": True, "answer": "Stay 45 minutes.", "citations": [{"chunk_id": "POL-CONTRAST#s5", "quote": "Stay 45 minutes."}]}
    try:
        r = inspection.ask(get_store(), "How long do outpatients stay after a contrast injection?", "t", datetime.now())
    finally:
        MOCK_FIXTURES["policy_qa"] = original
    assert r["ai_status"] == "needs_human" and not r["found"] and r["citations"] == [] and r["suggested"]


def test_uploaded_and_versioned_documents_are_answerable(client, login):
    md = login("U-MD")
    text = "# Visitors\nEach patient may bring one support person into the waiting area.\n\n# Children\nChildren under 12 cannot wait alone in the waiting room."
    r = client.post("/api/inspection/documents", json={"title": "Visitor policy", "owner": "Operations manager", "text": text},
                    headers=md)
    assert r.status_code == 200
    doc_id = r.json()["id"]
    ans = client.post("/api/inspection/ask", json={"question": "Can children under 12 wait alone in the waiting room?"},
                      headers=md).json()
    assert ans["found"] and ans["citations"][0]["doc_id"] == doc_id
    v2 = text.replace("one support person", "two support people")
    r = client.post(f"/api/inspection/documents/{doc_id}/versions", json={"version": "1.1", "change_note": "Two visitors",
                                                                          "text": v2}, headers=md)
    assert r.status_code == 200 and [v["version"] for v in r.json()["versions"]] == ["1.1", "1.0"]
    ans = client.post("/api/inspection/ask", json={"question": "How many support people may each patient bring?"},
                      headers=md).json()
    assert "two support people" in ans["answer"] and ans["citations"][0]["version"] == "1.1"
    assert client.post("/api/inspection/documents", json={"title": "x", "owner": "y", "text": "z" * 30},
                       headers=login("U-TECH")).status_code == 403
