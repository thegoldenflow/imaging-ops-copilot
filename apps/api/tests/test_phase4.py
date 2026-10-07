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
