"""System 15 · Inventory Manager API."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.core.auth import audit_phi, ensure_site, require_roles
from app.core.models import Role, StaffUser
from app.core.store import Store, get_store
from app.modules.inventory import service

router = APIRouter(prefix="/api/inventory", tags=["inventory"])

VIEWERS = require_roles(Role.TECHNOLOGIST, Role.OPERATIONS_MANAGER, Role.MEDICAL_DIRECTOR, Role.ADMIN)
STOCK_KEEPERS = require_roles(Role.TECHNOLOGIST, Role.OPERATIONS_MANAGER, Role.ADMIN)
BUYERS = require_roles(Role.OPERATIONS_MANAGER, Role.ADMIN)


def item_view(store: Store, item: service.InventoryItem, now: datetime) -> dict:
    per_day = service.usage_per_day(store, item, now)
    usable = sum(lot.quantity for lot in item.lots if lot.expiry >= now.date())
    po = service.open_order(store, item)
    today = now.date()
    return {
        **item.model_dump(mode="json", exclude={"lots"}),
        "site_name": store.sites[item.site_id].name, "quantity": item.quantity, "usable": usable,
        "uses": service.PRODUCTS[item.product_code]["uses"],
        "status": "low" if item.quantity <= item.reorder_point else "ok",
        "usage_per_day": round(per_day, 2), "days_left": round(usable / per_day, 1) if per_day else None,
        "open_order": po.model_dump(mode="json") if po else None,
        "lots": [{**lot.model_dump(mode="json"), "status": service.lot_status(lot, today),
                  "days_to_expiry": (lot.expiry - today).days} for lot in sorted(item.lots, key=lambda x: x.expiry)],
    }


def _scope(user: StaffUser, site_id: str | None) -> list[str] | None:
    if site_id:
        return [site_id] if not user.site_ids or site_id in user.site_ids else user.site_ids
    return user.site_ids or None


def _item(request: Request, user: StaffUser, item_id: str) -> service.InventoryItem:
    item = service.items(get_store()).get(item_id)
    if item is None:
        raise HTTPException(404, "Item not found")
    ensure_site(request, user, item.site_id, "inventory_item", item_id)
    return item


def _order(request: Request, user: StaffUser, order_id: str) -> service.PurchaseOrder:
    po = service.orders(get_store()).get(order_id)
    if po is None:
        raise HTTPException(404, "Purchase order not found")
    ensure_site(request, user, po.site_id, "purchase_order", order_id)
    return po


@router.get("")
def overview(site_id: str | None = None, user: StaffUser = Depends(VIEWERS)):
    store, now = get_store(), datetime.now()
    sites = _scope(user, site_id)
    rows = [i for i in list(service.items(store).values()) if not sites or i.site_id in sites]
    rows.sort(key=lambda i: (i.site_id, i.category, i.name))
    orders = [o for o in service.orders(store).values() if not sites or o.site_id in sites]
    orders.sort(key=lambda o: ({"draft": 0, "submitted": 1}.get(o.status, 2), o.created_at), reverse=False)
    mv = [m for m in service.movements(store)[-400:] if not sites or m.site_id in sites][-60:]
    names = {i.id: i.name for i in service.items(store).values()}
    return {
        "items": [item_view(store, i, now) for i in rows],
        "alerts": service.alerts(store, now, sites),
        "orders": [{**o.model_dump(mode="json"), "item_name": names[o.item_id], "site_name": store.sites[o.site_id].name}
                   for o in orders],
        "movements": [{**m.model_dump(mode="json"), "item_name": names[m.item_id]} for m in reversed(mv)],
        "sites": [{"id": s.id, "name": s.name} for s in store.sites.values() if not user.site_ids or s.id in user.site_ids],
        "expiry_warning_days": service.EXPIRY_WARNING_DAYS,
    }


class Adjustment(BaseModel):
    lot: str
    counted: int = Field(ge=0)
    reason: str = Field(min_length=3)


@router.post("/items/{item_id}/adjust")
def adjust(item_id: str, body: Adjustment, request: Request, user: StaffUser = Depends(STOCK_KEEPERS)):
    store = get_store()
    item = _item(request, user, item_id)
    try:
        service.adjust(store, item, body.lot, body.counted, body.reason.strip(), user.name, datetime.now())
    except service.InventoryError as e:
        raise HTTPException(409, str(e))
    store.touch()
    audit_phi(request, user, action="update", resource_type="inventory_item", resource_id=item_id)
    return item_view(store, item, datetime.now())


@router.post("/items/{item_id}/lots/{lot}/discard")
def discard(item_id: str, lot: str, request: Request, user: StaffUser = Depends(STOCK_KEEPERS)):
    store = get_store()
    item = _item(request, user, item_id)
    try:
        service.discard(store, item, lot, user.name, datetime.now())
    except service.InventoryError as e:
        raise HTTPException(409, str(e))
    store.touch()
    audit_phi(request, user, action="update", resource_type="inventory_item", resource_id=item_id)
    return item_view(store, item, datetime.now())


class Levels(BaseModel):
    reorder_point: int = Field(ge=0)
    reorder_qty: int = Field(gt=0)


@router.put("/items/{item_id}/levels")
def set_levels(item_id: str, body: Levels, request: Request, user: StaffUser = Depends(BUYERS)):
    store = get_store()
    item = _item(request, user, item_id)
    item.reorder_point, item.reorder_qty = body.reorder_point, body.reorder_qty
    service.check_reorder(store, item, datetime.now())
    store.touch()
    audit_phi(request, user, action="update", resource_type="inventory_levels", resource_id=item_id)
    return item_view(store, item, datetime.now())


class Submit(BaseModel):
    quantity: int | None = None


@router.post("/orders/{order_id}/submit")
def submit(order_id: str, body: Submit, request: Request, user: StaffUser = Depends(BUYERS)):
    store = get_store()
    po = _order(request, user, order_id)
    try:
        service.submit_order(store, po, body.quantity, user.name, datetime.now())
    except service.InventoryError as e:
        raise HTTPException(409, str(e))
    store.touch()
    audit_phi(request, user, action="update", resource_type="purchase_order", resource_id=order_id)
    return po.model_dump(mode="json")


@router.post("/orders/{order_id}/receive")
def receive(order_id: str, request: Request, user: StaffUser = Depends(STOCK_KEEPERS)):
    store = get_store()
    po = _order(request, user, order_id)
    try:
        lot = service.receive_order(store, po, user.name, datetime.now())
    except service.InventoryError as e:
        raise HTTPException(409, str(e))
    store.touch()
    audit_phi(request, user, action="update", resource_type="purchase_order", resource_id=order_id)
    return {"order": po.model_dump(mode="json"), "lot": lot.model_dump(mode="json")}
