"""System 15 · Inventory Manager.

Contrast agents and consumables are tracked per site as `InventoryItem`s, each
with one or more lots (lot number, expiry, quantity). When a technologist marks
an exam done, a completion hook deducts what the exam used, first-expiring lot
first. Falling to the reorder point raises a low-stock alert and drafts a
purchase order; lots close to expiry (or past it) raise their own alerts."""

import random
from datetime import date, datetime, timedelta

from pydantic import BaseModel, Field

from app.core.models import Appointment, AppointmentStatus, ImagingStudy, MessageOutbox, Modality
from app.core.store import Store
from app.modules.protocols.library import BY_ID as PROTOCOLS
from app.modules.scheduling import service as scheduling

EXPIRY_WARNING_DAYS = 60
USAGE_WINDOW_DAYS = 14

# Synthetic product catalogue. `per_exam` says which exams use the product and how many units.
PRODUCTS = {
    "IOHEXOL-350": {"name": "Iohexol 350 mgI/mL, 100 mL bottle", "category": "contrast", "unit": "bottle",
                    "uses": "Contrast CT", "modalities": [Modality.CT], "shelf_days": 720},
    "GADOBUTROL-7.5": {"name": "Gadobutrol 1.0 mmol/mL, 7.5 mL vial", "category": "contrast", "unit": "vial",
                       "uses": "Contrast MRI", "modalities": [Modality.MRI], "shelf_days": 900},
    "INJECTOR-KIT": {"name": "Power injector syringe kit", "category": "consumable", "unit": "kit",
                     "uses": "Contrast CT and MRI", "modalities": [Modality.CT, Modality.MRI], "shelf_days": 1080},
    "IV-20G": {"name": "IV cannula 20G", "category": "consumable", "unit": "each",
               "uses": "Contrast CT and MRI", "modalities": [Modality.CT, Modality.MRI], "shelf_days": 1080},
    "SALINE-10": {"name": "Saline flush syringe 10 mL", "category": "consumable", "unit": "syringe",
                  "uses": "Contrast CT and MRI (2 per exam)", "modalities": [Modality.CT, Modality.MRI], "shelf_days": 540},
    "US-GEL": {"name": "Ultrasound gel, single-use sachet", "category": "consumable", "unit": "sachet",
               "uses": "Every ultrasound", "modalities": [Modality.US], "shelf_days": 720},
}
# Default stocking levels per product: (reorder point, reorder quantity).
LEVELS = {"IOHEXOL-350": (12, 48), "GADOBUTROL-7.5": (8, 30), "INJECTOR-KIT": (15, 60), "IV-20G": (20, 100),
          "SALINE-10": (30, 150), "US-GEL": (40, 200)}
SUPPLIERS = {"contrast": "Demo Contrast Supply Co.", "consumable": "Sample Medical Distribution"}


class Lot(BaseModel):
    lot: str
    expiry: date
    quantity: int = Field(ge=0)
    received_at: datetime


class InventoryItem(BaseModel):
    id: str
    site_id: str
    product_code: str
    name: str
    category: str  # contrast, consumable
    unit: str
    reorder_point: int = Field(ge=0)
    reorder_qty: int = Field(gt=0)
    lots: list[Lot] = []

    @property
    def quantity(self) -> int:
        return sum(lot.quantity for lot in self.lots)


class Movement(BaseModel):
    id: str
    item_id: str
    site_id: str
    at: datetime
    kind: str  # consumed, received, adjusted, discarded
    quantity: int  # signed change
    lot: str | None = None
    appointment_id: str | None = None
    by: str
    note: str = ""


class PurchaseOrder(BaseModel):
    id: str
    item_id: str
    site_id: str
    supplier: str
    quantity: int
    status: str = "draft"  # draft, submitted, received, cancelled
    created_at: datetime
    reason: str
    submitted_by: str | None = None
    submitted_at: datetime | None = None
    received_at: datetime | None = None


class InventoryError(Exception):
    pass


def items(store: Store) -> dict[str, InventoryItem]:
    return store.module("inventory_items", dict)


def movements(store: Store) -> list[Movement]:
    return store.module("inventory_movements", list)


def orders(store: Store) -> dict[str, PurchaseOrder]:
    return store.module("inventory_orders", dict)


def item_for(store: Store, site_id: str, product_code: str) -> InventoryItem | None:
    return items(store).get(f"INV-{site_id}-{product_code}")


def uses_contrast(store: Store, appt: Appointment) -> bool:
    if appt.protocol_id and appt.protocol_id in PROTOCOLS:
        return PROTOCOLS[appt.protocol_id].contrast
    return store.exams[appt.exam_code].contrast


def usage_for(store: Store, appt: Appointment) -> dict[str, int]:
    """Units of each product one completed exam uses."""
    modality = store.exams[appt.exam_code].modality
    if modality == Modality.US:
        return {"US-GEL": 1}
    if modality in (Modality.CT, Modality.MRI) and uses_contrast(store, appt):
        agent = "IOHEXOL-350" if modality == Modality.CT else "GADOBUTROL-7.5"
        return {agent: 1, "INJECTOR-KIT": 1, "IV-20G": 1, "SALINE-10": 2}
    return {}


def _record(store: Store, item: InventoryItem, kind: str, qty: int, at: datetime, by: str, *, lot: str | None = None,
            appointment_id: str | None = None, note: str = "") -> Movement:
    mv = Movement(id=store.next_id("MOV"), item_id=item.id, site_id=item.site_id, at=at, kind=kind, quantity=qty,
                  lot=lot, appointment_id=appointment_id, by=by, note=note)
    movements(store).append(mv)
    return mv


def open_order(store: Store, item: InventoryItem) -> PurchaseOrder | None:
    return next((o for o in orders(store).values() if o.item_id == item.id and o.status in ("draft", "submitted")), None)


def _site_contact(store: Store, site_id: str) -> str:
    return f"operations.{site_id.lower()}@example.com"


def check_reorder(store: Store, item: InventoryItem, now: datetime, notify: bool = True) -> PurchaseOrder | None:
    """At or below the reorder point with no open order: draft one and tell the site."""
    if item.quantity > item.reorder_point or open_order(store, item):
        return None
    po = PurchaseOrder(id=store.next_id("PO"), item_id=item.id, site_id=item.site_id,
                       supplier=SUPPLIERS[item.category], quantity=item.reorder_qty, created_at=now,
                       reason=f"Stock {item.quantity} {item.unit}s at or below reorder point {item.reorder_point}")
    orders(store)[po.id] = po
    if notify:
        msg = MessageOutbox(
            id=store.next_id("MSG"), channel="email", kind="inventory_low", patient_id=None,
            to=_site_contact(store, item.site_id), language="en", scheduled_for=now,
            body=(f"Low stock at {store.sites[item.site_id].name}: {item.name} is at {item.quantity} "
                  f"(reorder point {item.reorder_point}). Purchase order draft {po.id} for {po.quantity} is ready to submit."),
        )
        store.outbox[msg.id] = msg
    return po


def consume(store: Store, item: InventoryItem, qty: int, at: datetime, by: str, appointment_id: str | None) -> int:
    """Take `qty` units, first-expiring usable lot first. Returns units actually taken."""
    taken = 0
    for lot in sorted(item.lots, key=lambda x: x.expiry):
        if taken == qty:
            break
        if lot.quantity == 0 or lot.expiry < at.date():
            continue  # never use an expired lot
        n = min(lot.quantity, qty - taken)
        lot.quantity -= n
        taken += n
        _record(store, item, "consumed", -n, at, by, lot=lot.lot, appointment_id=appointment_id)
    if taken < qty:
        _record(store, item, "consumed", 0, at, by, appointment_id=appointment_id,
                note=f"Short by {qty - taken}: no usable stock recorded")
    return taken


def deduct_for(store: Store, appt: Appointment, at: datetime, by: str = "auto (exam completed)",
               notify: bool = True) -> list[Movement]:
    before = len(movements(store))
    for code, qty in usage_for(store, appt).items():
        item = item_for(store, appt.site_id, code)
        if item is None:
            continue
        consume(store, item, qty, at, by, appt.id)
        check_reorder(store, item, at, notify=notify)
    return movements(store)[before:]


def on_completed(store: Store, appt: Appointment, study: ImagingStudy) -> None:
    deduct_for(store, appt, datetime.now())


scheduling.COMPLETION_HOOKS.append(on_completed)


# ---------- Reads ----------

def usage_per_day(store: Store, item: InventoryItem, now: datetime) -> float:
    since = now - timedelta(days=USAGE_WINDOW_DAYS)
    used = -sum(m.quantity for m in movements(store) if m.item_id == item.id and m.kind == "consumed" and m.at >= since)
    return used / USAGE_WINDOW_DAYS


def lot_status(lot: Lot, today: date) -> str:
    if lot.quantity == 0:
        return "empty"
    if lot.expiry < today:
        return "expired"
    if lot.expiry <= today + timedelta(days=EXPIRY_WARNING_DAYS):
        return "expiring"
    return "ok"


def alerts(store: Store, now: datetime, site_ids: list[str] | None = None) -> list[dict]:
    today = now.date()
    out = []
    for item in list(items(store).values()):
        if site_ids and item.site_id not in site_ids:
            continue
        if item.quantity <= item.reorder_point:
            po = open_order(store, item)
            out.append({"kind": "low_stock", "severity": "red" if item.quantity <= item.reorder_point // 2 else "amber",
                        "item_id": item.id, "site_id": item.site_id, "name": item.name,
                        "text": f"{item.quantity} {item.unit}s left, reorder point {item.reorder_point}",
                        "order_id": po.id if po else None})
        for lot in item.lots:
            status = lot_status(lot, today)
            if status in ("expired", "expiring"):
                days = (lot.expiry - today).days
                out.append({"kind": status, "severity": "red" if status == "expired" else "amber", "item_id": item.id,
                            "site_id": item.site_id, "name": item.name, "lot": lot.lot, "expiry": lot.expiry.isoformat(),
                            "text": (f"Lot {lot.lot} expired {-days} days ago ({lot.quantity} {item.unit}s): discard"
                                     if status == "expired" else
                                     f"Lot {lot.lot} expires in {days} days ({lot.quantity} {item.unit}s)")})
    order = {"expired": 0, "low_stock": 1, "expiring": 2}
    out.sort(key=lambda a: (order[a["kind"]], a["site_id"], a["name"]))
    return out


# ---------- Writes ----------

def adjust(store: Store, item: InventoryItem, lot_no: str, counted: int, reason: str, by: str, now: datetime) -> Movement:
    lot = next((x for x in item.lots if x.lot == lot_no), None)
    if lot is None:
        raise InventoryError("Unknown lot")
    if counted < 0:
        raise InventoryError("Count cannot be negative")
    delta = counted - lot.quantity
    lot.quantity = counted
    mv = _record(store, item, "adjusted", delta, now, by, lot=lot_no, note=reason)
    check_reorder(store, item, now)
    return mv


def discard(store: Store, item: InventoryItem, lot_no: str, by: str, now: datetime) -> Movement:
    lot = next((x for x in item.lots if x.lot == lot_no), None)
    if lot is None:
        raise InventoryError("Unknown lot")
    if lot.quantity == 0:
        raise InventoryError("Lot is already empty")
    qty, lot.quantity = lot.quantity, 0
    mv = _record(store, item, "discarded", -qty, now, by, lot=lot_no, note="Expired or damaged stock removed")
    check_reorder(store, item, now)
    return mv


def submit_order(store: Store, po: PurchaseOrder, quantity: int | None, by: str, now: datetime) -> PurchaseOrder:
    if po.status != "draft":
        raise InventoryError(f"Order is {po.status}")
    if quantity is not None:
        if quantity <= 0:
            raise InventoryError("Quantity must be positive")
        po.quantity = quantity
    po.status, po.submitted_by, po.submitted_at = "submitted", by, now
    return po


def receive_order(store: Store, po: PurchaseOrder, by: str, now: datetime, lot_no: str | None = None) -> Lot:
    if po.status != "submitted":
        raise InventoryError("Only submitted orders can be received")
    item = items(store)[po.item_id]
    lot = Lot(lot=lot_no or f"L{now:%y%m}{random.randint(100, 999)}",
              expiry=(now + timedelta(days=PRODUCTS[item.product_code]["shelf_days"])).date(),
              quantity=po.quantity, received_at=now)
    item.lots.append(lot)
    po.status, po.received_at = "received", now
    _record(store, item, "received", po.quantity, now, by, lot=lot.lot, note=f"Purchase order {po.id}")
    return lot


# ---------- Seed ----------

def seed(s: Store, rng: random.Random, now: datetime) -> None:
    """Stock for every site that does the exams, 30 days of consumption history and
    a few planted situations: Lakeshore's iohexol one exam above its reorder point,
    a gadobutrol lot at Northgate close to expiry, an expired saline lot at Eastview
    and gel already low at Westbrook (with a draft order)."""
    today = now.date()
    for site in s.sites.values():
        for code, product in PRODUCTS.items():
            if not any(m in site.modalities for m in product["modalities"]):
                continue
            point, qty = LEVELS[code]
            item = InventoryItem(id=f"INV-{site.id}-{code}", site_id=site.id, product_code=code, name=product["name"],
                                 category=product["category"], unit=product["unit"], reorder_point=point, reorder_qty=qty)
            older = Lot(lot=f"L{rng.randint(2400, 2599)}{rng.choice('ABCDEF')}", quantity=rng.randint(point, point * 2),
                        expiry=today + timedelta(days=rng.randint(90, 300)), received_at=now - timedelta(days=rng.randint(60, 120)))
            newer = Lot(lot=f"L{rng.randint(2600, 2699)}{rng.choice('ABCDEF')}", quantity=rng.randint(qty // 2, qty),
                        expiry=today + timedelta(days=rng.randint(400, 700)), received_at=now - timedelta(days=rng.randint(5, 30)))
            item.lots = [older, newer]
            items(s)[item.id] = item

    # Consumption history from the last 30 days of completed exams (deducted from a
    # notional earlier stock, so current lot levels are not changed by the history).
    since = now - timedelta(days=30)
    for appt in sorted(s.appointments.values(), key=lambda a: a.start):
        if appt.status != AppointmentStatus.COMPLETED or appt.start < since:
            continue
        for code, qty in usage_for(s, appt).items():
            item = item_for(s, appt.site_id, code)
            if item:
                lot = min(item.lots, key=lambda x: x.expiry)
                _record(s, item, "consumed", -qty, min(appt.end, now), "auto (exam completed)", lot=lot.lot,
                        appointment_id=appt.id)

    # Planted situations for the demo.
    ioh = items(s)["INV-LKS-IOHEXOL-350"]
    ioh.lots[0].quantity, ioh.lots[1].quantity = 5, ioh.reorder_point + 1 - 5
    gad = items(s)["INV-NGT-GADOBUTROL-7.5"]
    gad.lots[0].expiry = today + timedelta(days=18)
    saline = items(s)["INV-EVW-SALINE-10"]
    saline.lots[0].expiry = today - timedelta(days=4)
    gel = items(s)["INV-WBK-US-GEL"]
    gel.lots[0].quantity, gel.lots[1].quantity = 0, gel.reorder_point - 12
    check_reorder(s, gel, now - timedelta(hours=20), notify=False)
    # A submitted order on its way, so the receive step can be shown.
    kit = items(s)["INV-NGT-INJECTOR-KIT"]
    po = PurchaseOrder(id=s.next_id("PO"), item_id=kit.id, site_id="NGT", supplier=SUPPLIERS["consumable"], quantity=60,
                       status="submitted", created_at=now - timedelta(days=3), reason="Scheduled monthly top-up",
                       submitted_by="Jordan Lee", submitted_at=now - timedelta(days=3))
    orders(s)[po.id] = po
