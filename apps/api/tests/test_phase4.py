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
