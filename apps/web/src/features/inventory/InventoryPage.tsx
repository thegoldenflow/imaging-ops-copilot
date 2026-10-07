import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { AlertTriangle, PackageCheck, X } from "lucide-react";
import { useState } from "react";
import { Badge, Button, Card, EmptyState, ErrorState, Loading, PageHeader, Stat, Tabs } from "../../components/ui";
import { api, post, put } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { dateTime } from "../../lib/format";
import type { InventoryItem, InventoryOverview, PurchaseOrder } from "../../lib/types";

type Tab = "stock" | "orders" | "movements";
const STOCK_KEEPERS = ["technologist", "operations_manager", "admin"];
const BUYERS = ["operations_manager", "admin"];
const LOT_TONE = { ok: "slate", expiring: "amber", expired: "red", empty: "slate" } as const;
const MOVE_LABEL = { consumed: "Used", received: "Received", adjusted: "Count correction", discarded: "Discarded" };

function ItemDrawer({ item, onClose }: { item: InventoryItem; onClose: () => void }) {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const [lot, setLot] = useState(item.lots.find((l) => l.quantity > 0)?.lot ?? item.lots[0]?.lot ?? "");
  const [counted, setCounted] = useState("");
  const [reason, setReason] = useState("");
  const [levels, setLevels] = useState({ reorder_point: item.reorder_point, reorder_qty: item.reorder_qty });
  const done = () => queryClient.invalidateQueries({ queryKey: ["inventory"] });
  const adjust = useMutation({ mutationFn: () => post(`/api/inventory/items/${item.id}/adjust`, { lot, counted: Number(counted), reason }), onSuccess: () => { setCounted(""); setReason(""); done(); } });
  const discard = useMutation({ mutationFn: (l: string) => post(`/api/inventory/items/${item.id}/lots/${encodeURIComponent(l)}/discard`), onSuccess: done });
  const saveLevels = useMutation({ mutationFn: () => put(`/api/inventory/items/${item.id}/levels`, levels), onSuccess: done });
  const canKeep = user && STOCK_KEEPERS.includes(user.role);
  const error = adjust.error ?? discard.error ?? saveLevels.error;
  return (
    <div className="fixed inset-0 z-20 flex justify-end bg-slate-900/30" onClick={onClose}>
      <aside className="flex h-full w-full max-w-xl flex-col bg-white shadow-xl" onClick={(e) => e.stopPropagation()} aria-label="Inventory item" data-testid="item-drawer">
        <header className="flex items-start justify-between border-b border-slate-100 px-5 py-4">
          <div>
            <p className="text-sm font-semibold text-slate-900">{item.name}</p>
            <p className="text-xs text-slate-500">{item.site_name} · {item.product_code} · {item.uses}</p>
          </div>
          <button onClick={onClose} className="rounded-md p-1 text-slate-500 hover:bg-slate-100" aria-label="Close"><X className="size-4" /></button>
        </header>
        <div className="flex-1 space-y-4 overflow-y-auto px-5 py-4">
          <div className="grid grid-cols-3 gap-3 text-sm">
            <div className="rounded-lg border border-slate-200 p-3"><p className="text-xs text-slate-500">On hand</p><p className="tabular text-xl font-semibold" data-testid="drawer-quantity">{item.quantity}</p><p className="text-xs text-slate-500">{item.usable} usable</p></div>
            <div className="rounded-lg border border-slate-200 p-3"><p className="text-xs text-slate-500">Use per day</p><p className="tabular text-xl font-semibold">{item.usage_per_day}</p><p className="text-xs text-slate-500">last 14 days</p></div>
            <div className="rounded-lg border border-slate-200 p-3"><p className="text-xs text-slate-500">Days of stock</p><p className="tabular text-xl font-semibold">{item.days_left ?? "–"}</p><p className="text-xs text-slate-500">at current use</p></div>
          </div>
          <div>
            <p className="mb-1 text-xs font-semibold tracking-wide text-slate-500 uppercase">Lots (first to expire is used first)</p>
            <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200">
              {item.lots.map((l) => (
                <li key={l.lot} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-sm" data-testid={`lot-${l.lot}`}>
                  <div>
                    <p className="font-medium text-slate-800">Lot {l.lot} · <span className="tabular">{l.quantity}</span> {item.unit}s</p>
                    <p className="text-xs text-slate-500">Expires {l.expiry} ({l.days_to_expiry < 0 ? `${-l.days_to_expiry} days ago` : `in ${l.days_to_expiry} days`})</p>
                  </div>
                  <div className="flex items-center gap-2">
                    {l.status !== "ok" && <Badge tone={LOT_TONE[l.status]}>{l.status}</Badge>}
                    {canKeep && l.status === "expired" && l.quantity > 0 && (
                      <Button size="sm" variant="danger" loading={discard.isPending} onClick={() => discard.mutate(l.lot)} data-testid={`discard-${l.lot}`}>Discard</Button>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          </div>
          {canKeep && (
            <div className="space-y-2 rounded-lg border border-slate-200 p-3">
              <p className="text-sm font-medium text-slate-800">Count correction</p>
              <div className="grid grid-cols-2 gap-2">
                <select value={lot} onChange={(e) => setLot(e.target.value)} className="h-9 rounded-lg border border-slate-300 px-2 text-sm" aria-label="Lot">
                  {item.lots.map((l) => <option key={l.lot} value={l.lot}>Lot {l.lot} ({l.quantity})</option>)}
                </select>
                <input type="number" min={0} value={counted} onChange={(e) => setCounted(e.target.value)} placeholder="Counted" className="h-9 rounded-lg border border-slate-300 px-2 text-sm" aria-label="Counted quantity" />
              </div>
              <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Reason (e.g. monthly count)" className="h-9 w-full rounded-lg border border-slate-300 px-2 text-sm" aria-label="Reason" />
              <div className="flex justify-end"><Button size="sm" variant="primary" disabled={counted === "" || reason.trim().length < 3} loading={adjust.isPending} onClick={() => adjust.mutate()}>Save count</Button></div>
            </div>
          )}
          {user && BUYERS.includes(user.role) && (
            <div className="space-y-2 rounded-lg border border-slate-200 p-3">
              <p className="text-sm font-medium text-slate-800">Stocking levels</p>
              <div className="grid grid-cols-2 gap-2 text-sm">
                <label className="text-xs text-slate-600">Reorder point
                  <input type="number" min={0} value={levels.reorder_point} onChange={(e) => setLevels({ ...levels, reorder_point: Number(e.target.value) })} className="mt-1 h-9 w-full rounded-lg border border-slate-300 px-2 text-sm" />
                </label>
                <label className="text-xs text-slate-600">Reorder quantity
                  <input type="number" min={1} value={levels.reorder_qty} onChange={(e) => setLevels({ ...levels, reorder_qty: Number(e.target.value) })} className="mt-1 h-9 w-full rounded-lg border border-slate-300 px-2 text-sm" />
                </label>
              </div>
              <div className="flex justify-end"><Button size="sm" loading={saveLevels.isPending} onClick={() => saveLevels.mutate()}>Save levels</Button></div>
            </div>
          )}
          {error && <p className="text-sm text-rose-600" role="alert">{(error as Error).message}</p>}
        </div>
      </aside>
    </div>
  );
}

function Orders({ orders }: { orders: PurchaseOrder[] }) {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const act = useMutation({
    mutationFn: ({ id, action }: { id: string; action: "submit" | "receive" }) => post(`/api/inventory/orders/${id}/${action}`, {}),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["inventory"] }),
  });
  if (orders.length === 0) return <Card><EmptyState title="No purchase orders" hint="Orders are drafted automatically when stock reaches its reorder point." /></Card>;
  return (
    <Card padded={false} title="Purchase orders">
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead className="bg-slate-50 text-xs text-slate-500">
            <tr>{["Order", "Item", "Site", "Qty", "Supplier", "Status", ""].map((h, i) => <th key={i} className="px-3 py-2 font-medium">{h}</th>)}</tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {orders.map((o) => (
              <tr key={o.id} data-testid={`order-${o.id}`}>
                <td className="px-3 py-2"><p className="font-medium text-slate-800">{o.id}</p><p className="text-xs text-slate-500">{dateTime(o.created_at)}</p></td>
                <td className="px-3 py-2">{o.item_name}<p className="text-xs text-slate-500">{o.reason}</p></td>
                <td className="px-3 py-2 text-xs">{o.site_name}</td>
                <td className="tabular px-3 py-2">{o.quantity}</td>
                <td className="px-3 py-2 text-xs">{o.supplier}</td>
                <td className="px-3 py-2"><Badge tone={o.status === "draft" ? "amber" : o.status === "submitted" ? "blue" : "green"}>{o.status}</Badge>{o.submitted_by && <p className="mt-0.5 text-xs text-slate-500">by {o.submitted_by}</p>}</td>
                <td className="px-3 py-2 text-right whitespace-nowrap">
                  {o.status === "draft" && user && BUYERS.includes(user.role) && (
                    <Button size="sm" variant="primary" loading={act.isPending && act.variables?.id === o.id} onClick={() => act.mutate({ id: o.id, action: "submit" })} data-testid={`submit-${o.id}`}>Submit to supplier</Button>
                  )}
                  {o.status === "draft" && user && !BUYERS.includes(user.role) && <span className="text-xs text-slate-500">Waiting for operations to submit</span>}
                  {o.status === "submitted" && user && STOCK_KEEPERS.includes(user.role) && (
                    <Button size="sm" loading={act.isPending && act.variables?.id === o.id} onClick={() => act.mutate({ id: o.id, action: "receive" })} data-testid={`receive-${o.id}`}><PackageCheck className="size-3.5" /> Receive</Button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="px-4 py-2 text-xs text-slate-500">Draft orders are prepared by the system; a person submits them. Suppliers are fictional.</p>
      {act.error && <p className="px-4 pb-3 text-sm text-rose-600" role="alert">{(act.error as Error).message}</p>}
    </Card>
  );
}

export function InventoryPage() {
  const { user } = useAuth();
  const [site, setSite] = useState("");
  const [tab, setTab] = useState<Tab>("stock");
  const [openId, setOpenId] = useState<string | null>(null);
  const q = useQuery({
    queryKey: ["inventory", site],
    queryFn: () => api<InventoryOverview>(`/api/inventory${site ? `?site_id=${site}` : ""}`),
    refetchInterval: 5000,
  });
  const d = q.data;
  const open = d?.items.find((i) => i.id === openId);
  const count = (kind: string) => d?.alerts.filter((a) => a.kind === kind).length ?? 0;
  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader
        title="Inventory"
        subtitle="Contrast and consumables by site. Exams deduct what they use when the technologist marks them done."
        actions={user && user.site_ids.length === 0 && d && (
          <select value={site} onChange={(e) => setSite(e.target.value)} className="h-9 rounded-lg border border-slate-300 px-2 text-sm" aria-label="Site">
            <option value="">All sites</option>
            {d.sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        )}
      />
      {q.isLoading && <Loading />}
      {q.error && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {d && (
        <>
          <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat label="Items at or below reorder point" value={<span data-testid="kpi-low">{count("low_stock")}</span>} tone={count("low_stock") ? "red" : "green"} />
            <Stat label={`Lots expiring within ${d.expiry_warning_days} days`} value={count("expiring")} tone={count("expiring") ? "amber" : undefined} />
            <Stat label="Expired lots on the shelf" value={count("expired")} tone={count("expired") ? "red" : "green"} />
            <Stat label="Draft purchase orders" value={d.orders.filter((o) => o.status === "draft").length} />
          </div>
          <Card title="Alerts" className="mb-4" padded={false}>
            {d.alerts.length === 0 ? <EmptyState title="No alerts" hint="Stock is above reorder points and no lot is close to expiry." /> : (
              <ul className="divide-y divide-slate-100" data-testid="inventory-alerts">
                {d.alerts.map((a, i) => (
                  <li key={i} className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5 text-sm" data-testid={`alert-${a.kind}-${a.item_id}`}>
                    <div className="flex items-start gap-2">
                      <AlertTriangle className={clsx("mt-0.5 size-4 shrink-0", a.severity === "red" ? "text-rose-600" : "text-amber-600")} />
                      <div>
                        <p className="font-medium text-slate-800">{a.name} <span className="font-normal text-slate-500">· {a.site_id}</span></p>
                        <p className="text-xs text-slate-600">{a.text}</p>
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      <Badge tone={a.severity}>{a.kind === "low_stock" ? "Low stock" : a.kind === "expired" ? "Expired" : "Expiring soon"}</Badge>
                      {a.order_id && <Badge tone="blue">Order {a.order_id} drafted</Badge>}
                      <Button size="sm" variant="ghost" onClick={() => setOpenId(a.item_id)}>Open</Button>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <Tabs value={tab} onChange={setTab} tabs={[
            { id: "stock", label: "Stock" },
            { id: "orders", label: `Purchase orders (${d.orders.filter((o) => o.status !== "received").length})` },
            { id: "movements", label: "Movements" },
          ]} />
          {tab === "stock" && (
            <Card padded={false}>
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead className="bg-slate-50 text-xs text-slate-500">
                    <tr>{["Item", "Site", "On hand", "Reorder point", "Use / day", "Days left", "Lots"].map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}</tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {d.items.map((i) => (
                      <tr key={i.id} className="cursor-pointer hover:bg-slate-50" onClick={() => setOpenId(i.id)} data-testid={`item-${i.id}`}>
                        <td className="px-3 py-2"><p className="font-medium text-slate-800">{i.name}</p><p className="text-xs text-slate-500">{i.category === "contrast" ? "Contrast agent" : "Consumable"} · {i.uses}</p></td>
                        <td className="px-3 py-2 text-xs">{i.site_name}</td>
                        <td className="px-3 py-2"><span className={clsx("tabular font-semibold", i.status === "low" ? "text-rose-600" : "text-slate-900")} data-testid={`qty-${i.id}`}>{i.quantity}</span> <span className="text-xs text-slate-500">{i.unit}s</span></td>
                        <td className="tabular px-3 py-2">{i.reorder_point}</td>
                        <td className="tabular px-3 py-2">{i.usage_per_day}</td>
                        <td className="tabular px-3 py-2">{i.days_left ?? "–"}</td>
                        <td className="px-3 py-2"><div className="flex flex-wrap gap-1">{i.lots.map((l) => <Badge key={l.lot} tone={LOT_TONE[l.status]}>{l.lot}: {l.quantity}</Badge>)}</div></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          )}
          {tab === "orders" && <Orders orders={d.orders} />}
          {tab === "movements" && (
            <Card padded={false} title="Recent stock movements">
              {d.movements.length === 0 ? <EmptyState title="No movements yet" /> : (
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-sm">
                    <thead className="bg-slate-50 text-xs text-slate-500">
                      <tr>{["When", "Item", "Change", "Lot", "Exam / note", "By"].map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}</tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {d.movements.map((m) => (
                        <tr key={m.id} data-testid={`move-${m.id}`}>
                          <td className="tabular px-3 py-2 text-xs whitespace-nowrap text-slate-600">{dateTime(m.at)}</td>
                          <td className="px-3 py-2">{m.item_name}<p className="text-xs text-slate-500">{m.site_id}</p></td>
                          <td className="px-3 py-2"><span className={clsx("tabular font-medium", m.quantity < 0 ? "text-rose-600" : "text-emerald-600")}>{m.quantity > 0 ? `+${m.quantity}` : m.quantity}</span> <span className="text-xs text-slate-500">{MOVE_LABEL[m.kind]}</span></td>
                          <td className="px-3 py-2 text-xs">{m.lot ?? "–"}</td>
                          <td className="px-3 py-2 text-xs">{m.appointment_id ? `Exam ${m.appointment_id}` : m.note}</td>
                          <td className="px-3 py-2 text-xs">{m.by}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>
          )}
          {open && <ItemDrawer key={open.id + open.quantity} item={open} onClose={() => setOpenId(null)} />}
        </>
      )}
    </div>
  );
}
