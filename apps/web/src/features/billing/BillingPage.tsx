import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { Download, X } from "lucide-react";
import { useState } from "react";
import { Badge, Button, Card, EmptyState, ErrorState, Loading, PageHeader, Stat, Tabs } from "../../components/ui";
import { api, download, post } from "../../lib/api";
import { dateTime } from "../../lib/format";
import type { BillingOverview, Discrepancy } from "../../lib/types";

const money = (v: number) => `$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const KIND_TONE: Record<string, "red" | "amber" | "blue" | "slate"> = {
  missing: "red", duplicate: "amber", code_mismatch: "amber", amount_mismatch: "blue", rejected: "red", not_performed: "red",
};
const SUGGESTED: Record<string, string> = {
  missing: "claim_submitted", duplicate: "duplicate_voided", code_mismatch: "corrected_resubmitted",
  amount_mismatch: "corrected_resubmitted", rejected: "corrected_resubmitted", not_performed: "claim_voided",
};

function WorkDrawer({ item, outcomes, onClose }: { item: Discrepancy; outcomes: Record<string, string>; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [outcome, setOutcome] = useState(SUGGESTED[item.kind]);
  const [note, setNote] = useState("");
  const done = () => { queryClient.invalidateQueries({ queryKey: ["billing"] }); onClose(); };
  const resolve = useMutation({ mutationFn: () => post(`/api/billing/discrepancies/${item.id}/resolve`, { outcome, note }), onSuccess: done });
  const reopen = useMutation({ mutationFn: () => post(`/api/billing/discrepancies/${item.id}/reopen`), onSuccess: done });
  return (
    <div className="fixed inset-0 z-20 flex justify-end bg-slate-900/30" onClick={onClose}>
      <aside className="flex h-full w-full max-w-xl flex-col bg-white shadow-xl" onClick={(e) => e.stopPropagation()} aria-label="Discrepancy" data-testid="billing-drawer">
        <header className="flex items-start justify-between border-b border-slate-100 px-5 py-4">
          <div>
            <p className="text-sm font-semibold text-slate-900">{item.kind_label} · {item.patient_name}</p>
            <p className="text-xs text-slate-500">{item.exam_name} · {item.service_date} · {item.site_id} · exam {item.appointment_id} ({item.appointment_status})</p>
          </div>
          <button onClick={onClose} className="rounded-md p-1 text-slate-500 hover:bg-slate-100" aria-label="Close"><X className="size-4" /></button>
        </header>
        <div className="flex-1 space-y-4 overflow-y-auto px-5 py-4 text-sm">
          <p className="rounded-lg bg-slate-50 px-3 py-2 text-slate-800">{item.detail}</p>
          <div>
            <p className="mb-1 text-xs font-semibold tracking-wide text-slate-500 uppercase">Claims</p>
            {item.claims.length === 0 ? <p className="text-slate-500">No claim was submitted for this exam.</p> : (
              <table className="w-full text-left text-xs">
                <thead className="text-slate-500"><tr>{["Claim", "Code", "Amount", "Payer", "Submitted", "Status"].map((h) => <th key={h} className="py-1 font-medium">{h}</th>)}</tr></thead>
                <tbody className="divide-y divide-slate-100">
                  {item.claims.map((c) => (
                    <tr key={c.id}><td className="py-1.5">{c.id}</td><td>{c.fee_code}</td><td className="tabular">{money(c.amount)}</td><td>{c.payer}</td><td>{dateTime(c.submitted_at)}</td>
                      <td><Badge tone={c.status === "rejected" ? "red" : c.status === "paid" ? "green" : "blue"}>{c.status}</Badge>{c.rejection_reason && <p className="text-rose-700">{c.rejection_reason}</p>}</td></tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
          {item.status === "open" ? (
            <div className="space-y-2 rounded-lg border border-slate-200 p-3">
              <p className="font-medium text-slate-800">Record the outcome</p>
              <select value={outcome} onChange={(e) => setOutcome(e.target.value)} className="h-9 w-full rounded-lg border border-slate-300 px-2" aria-label="Outcome">
                {Object.entries(outcomes).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
              <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2} placeholder="Note (required for write-offs and no action)" className="w-full rounded-lg border border-slate-300 p-2" aria-label="Note" />
              <div className="flex justify-end"><Button variant="primary" size="sm" loading={resolve.isPending} onClick={() => resolve.mutate()} data-testid="resolve-discrepancy">Mark resolved</Button></div>
            </div>
          ) : (
            <div className="flex items-center justify-between rounded-lg bg-emerald-50 px-3 py-2 text-emerald-800">
              <span>{outcomes[item.outcome!]} by {item.resolved_by}{item.note && `: ${item.note}`}</span>
              <Button size="sm" variant="ghost" loading={reopen.isPending} onClick={() => reopen.mutate()}>Reopen</Button>
            </div>
          )}
          {item.history.length > 0 && (
            <div>
              <p className="mb-1 text-xs font-semibold tracking-wide text-slate-500 uppercase">Trail</p>
              <ol className="space-y-1 text-xs text-slate-600">{item.history.map((h, i) => <li key={i}>{dateTime(h.ts)} · {h.by}: {h.action}{h.note && ` (${h.note})`}</li>)}</ol>
            </div>
          )}
          {(resolve.error || reopen.error) && <p className="text-rose-600" role="alert">{((resolve.error ?? reopen.error) as Error).message}</p>}
        </div>
      </aside>
    </div>
  );
}

export function BillingPage() {
  const q = useQuery({ queryKey: ["billing"], queryFn: () => api<BillingOverview>("/api/billing/overview") });
  const [tab, setTab] = useState<"open" | "resolved" | "fees">("open");
  const [kind, setKind] = useState("");
  const [openId, setOpenId] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const d = q.data;
  const rows = d?.discrepancies.filter((r) => (tab === "fees" || r.status === tab) && (!kind || r.kind === kind)) ?? [];
  const openCount = d ? Object.values(d.kinds).reduce((n, k) => n + k.open, 0) : 0;
  const atStake = d ? Object.values(d.kinds).reduce((n, k) => n + k.at_stake, 0) : 0;
  const exportCsv = async () => {
    setExporting(true);
    try { await download("/api/billing/export.csv", `billing-discrepancies-${new Date().toISOString().slice(0, 10)}.csv`); } finally { setExporting(false); }
  };
  const item = d?.discrepancies.find((r) => r.id === openId);
  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader title="Billing & claims QA" subtitle={d && `Completed exams of the last ${d.window_days} days reconciled against submitted claims. Fee codes are synthetic, not the OHIP schedule.`}
        actions={<Button onClick={exportCsv} loading={exporting} data-testid="export-billing"><Download className="size-4" /> Export CSV</Button>} />
      {q.isLoading && <Loading />}
      {q.error && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {d && (
        <>
          <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat label="Open discrepancies" value={<span data-testid="kpi-open">{openCount}</span>} tone={openCount ? "red" : "green"} />
            <Stat label="Value at stake (open)" value={money(atStake)} />
            <Stat label="Claims in window" value={d.claims.total} hint={`${d.claims.paid} paid · ${d.claims.submitted} pending`} />
            <Stat label="Rejected claims" value={d.claims.rejected} tone={d.claims.rejected ? "amber" : undefined} />
          </div>
          <div className="mb-3 flex flex-wrap gap-2" aria-label="Discrepancy kinds">
            <button onClick={() => setKind("")} className={clsx("rounded-full border px-3 py-1 text-xs font-medium", !kind ? "border-brand-600 bg-brand-50 text-brand-700" : "border-slate-300 text-slate-600")}>All kinds</button>
            {Object.entries(d.kinds).map(([k, v]) => (
              <button key={k} onClick={() => setKind(k)} data-testid={`kind-${k}`}
                className={clsx("rounded-full border px-3 py-1 text-xs font-medium", kind === k ? "border-brand-600 bg-brand-50 text-brand-700" : "border-slate-300 text-slate-600")}>
                {v.label} <span className="tabular">{v.open}</span>
              </button>
            ))}
          </div>
          <Tabs value={tab} onChange={setTab} tabs={[{ id: "open", label: `Work queue (${openCount})` }, { id: "resolved", label: "Resolved" }, { id: "fees", label: "Fee table" }]} />
          {tab === "fees" ? (
            <Card padded={false}>
              <table className="w-full text-left text-sm">
                <thead className="bg-slate-50 text-xs text-slate-500"><tr>{["Exam", "Fee code", "Description", "Amount"].map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}</tr></thead>
                <tbody className="divide-y divide-slate-100">{d.fees.map((f) => <tr key={f.exam_code}><td className="px-3 py-2">{f.exam_name}</td><td className="px-3 py-2 font-mono text-xs">{f.fee_code}</td><td className="px-3 py-2 text-xs">{f.description}</td><td className="tabular px-3 py-2">{money(f.amount)}</td></tr>)}</tbody>
              </table>
              <p className="px-4 py-2 text-xs text-slate-500">Synthetic codes and amounts for the demo.</p>
            </Card>
          ) : (
            <Card padded={false}>
              {rows.length === 0 ? <EmptyState title={tab === "open" ? "Nothing to work" : "Nothing resolved yet"} /> : (
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-sm">
                    <thead className="bg-slate-50 text-xs text-slate-500"><tr>{["Service date", "Patient · exam", "Finding", "Detail", "At stake", ""].map((h, i) => <th key={i} className="px-3 py-2 font-medium">{h}</th>)}</tr></thead>
                    <tbody className="divide-y divide-slate-100">
                      {rows.map((r) => (
                        <tr key={r.id} className="cursor-pointer hover:bg-slate-50" onClick={() => setOpenId(r.id)} data-testid={`disc-${r.id}`}>
                          <td className="tabular px-3 py-2 text-xs whitespace-nowrap">{r.service_date}<p className="text-slate-500">{r.site_id}</p></td>
                          <td className="px-3 py-2"><p className="font-medium text-slate-800">{r.patient_name}</p><p className="text-xs text-slate-500">{r.exam_name}</p></td>
                          <td className="px-3 py-2"><Badge tone={KIND_TONE[r.kind]}>{r.kind_label}</Badge></td>
                          <td className="px-3 py-2 text-xs text-slate-600">{r.detail}</td>
                          <td className="tabular px-3 py-2">{money(r.at_stake)}</td>
                          <td className="px-3 py-2 text-right text-xs">{r.status === "resolved" ? <Badge tone="green">{d.outcomes[r.outcome!]}</Badge> : <span className="font-medium text-brand-700">Work</span>}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>
          )}
          {item && <WorkDrawer item={item} outcomes={d.outcomes} onClose={() => setOpenId(null)} />}
        </>
      )}
    </div>
  );
}
