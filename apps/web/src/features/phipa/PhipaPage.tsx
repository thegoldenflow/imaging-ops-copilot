import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { Download, Printer, ShieldCheck, ShieldAlert, X } from "lucide-react";
import { useState } from "react";
import { Badge, Button, Card, EmptyState, ErrorState, Loading, PageHeader, Stat, Tabs } from "../../components/ui";
import { api, download, post } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { dateTime } from "../../lib/format";
import { ROLE_LABEL, type PhipaAlert, type PhipaReport, type Role } from "../../lib/types";

interface AlertList { alerts: PhipaAlert[]; counts: Record<string, number>; rules: Record<string, string>; outcomes: Record<string, string>; staff: string[] }
const STATUS_TONE = { new: "red", investigating: "amber", closed: "green" } as const;

function Risk({ v }: { v: number }) {
  return <span className={clsx("tabular inline-flex w-10 justify-center rounded-md px-1.5 py-0.5 text-xs font-semibold", v >= 70 ? "bg-rose-600 text-white" : v >= 50 ? "bg-amber-100 text-amber-800" : "bg-slate-100 text-slate-700")}>{v}</span>;
}

function AlertDrawer({ id, outcomes, staff, onClose }: { id: string; outcomes: Record<string, string>; staff: string[]; onClose: () => void }) {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const q = useQuery({ queryKey: ["phipa-alert", id], queryFn: () => api<PhipaAlert>(`/api/phipa/alerts/${encodeURIComponent(id)}`) });
  const [note, setNote] = useState("");
  const [outcome, setOutcome] = useState("justified");
  const [assignee, setAssignee] = useState(user?.name ?? "");
  const act = useMutation({
    mutationFn: (body: Record<string, unknown>) => post<PhipaAlert>(`/api/phipa/alerts/${encodeURIComponent(id)}/actions`, body),
    onSuccess: (data) => { setNote(""); queryClient.setQueryData(["phipa-alert", id], data); queryClient.invalidateQueries({ queryKey: ["phipa"] }); },
  });
  const a = q.data;
  const inv = a?.investigation;
  return (
    <div className="fixed inset-0 z-20 flex justify-end bg-slate-900/30" onClick={onClose}>
      <aside className="flex h-full w-full max-w-2xl flex-col bg-white shadow-xl" onClick={(e) => e.stopPropagation()} aria-label="Access alert" data-testid="phipa-drawer">
        <header className="flex items-start justify-between border-b border-slate-100 px-5 py-4">
          <div>
            <p className="flex items-center gap-2 text-sm font-semibold text-slate-900">{a && <Risk v={a.risk} />} {a?.rule_label ?? "Alert"}</p>
            {a && <p className="text-xs text-slate-500">{a.user_name} ({ROLE_LABEL[a.role as Role] ?? a.role}{a.user_sites.length ? `, ${a.user_sites.join(", ")}` : ""}) · {a.patient_name ?? "–"} · {dateTime(a.first_at)}</p>}
          </div>
          <button onClick={onClose} className="rounded-md p-1 text-slate-500 hover:bg-slate-100" aria-label="Close"><X className="size-4" /></button>
        </header>
        <div className="flex-1 space-y-4 overflow-y-auto px-5 py-4 text-sm">
          {q.isLoading && <Loading />}
          {q.error && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
          {a && inv && (
            <>
              <p className="rounded-lg bg-slate-50 px-3 py-2 text-slate-800">{a.why}</p>
              <div>
                <p className="mb-1 text-xs font-semibold tracking-wide text-slate-500 uppercase">Evidence from the audit log ({a.evidence.length} events)</p>
                <div className="max-h-64 overflow-auto rounded-lg border border-slate-200">
                  <table className="w-full text-left text-xs" data-testid="evidence">
                    <thead className="sticky top-0 bg-slate-50 text-slate-500"><tr>{["Seq", "Time", "Action", "Record", "Patient", "Outcome", "IP"].map((h) => <th key={h} className="px-2 py-1.5 font-medium">{h}</th>)}</tr></thead>
                    <tbody className="tabular divide-y divide-slate-100">
                      {a.evidence.map((e) => (
                        <tr key={e.seq}><td className="px-2 py-1">#{e.seq}</td><td className="px-2 py-1 whitespace-nowrap">{dateTime(e.ts)}</td><td className="px-2 py-1">{e.action}</td>
                          <td className="px-2 py-1">{e.resource_type} {e.resource_id}</td><td className="px-2 py-1">{e.patient_id ?? "–"}</td>
                          <td className="px-2 py-1"><Badge tone={e.outcome === "denied" ? "red" : "slate"}>{e.outcome}</Badge></td><td className="px-2 py-1">{e.source_ip}</td></tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <p className="mt-1 text-xs text-slate-500">Sequence numbers point into the hash-chained audit log; editing any entry breaks the chain.</p>
              </div>
              <div>
                <div className="mb-1 flex items-center justify-between">
                  <p className="text-xs font-semibold tracking-wide text-slate-500 uppercase">Investigation</p>
                  <Badge tone={STATUS_TONE[inv.status]}>{inv.status}{inv.assignee ? ` · ${inv.assignee}` : ""}</Badge>
                </div>
                {inv.trail.length === 0 ? <p className="text-slate-500">Not started.</p> : (
                  <ol className="space-y-1.5 border-l-2 border-slate-200 pl-3" data-testid="phipa-trail">
                    {inv.trail.map((t, i) => (
                      <li key={i}><p className="text-xs text-slate-500">{dateTime(t.ts)} · {t.by}</p><p className="text-slate-800">{t.text}</p>{t.note && <p className="text-slate-600">{t.note}</p>}</li>
                    ))}
                  </ol>
                )}
              </div>
              {inv.status === "closed" ? (
                <div className="flex items-center justify-between rounded-lg bg-emerald-50 px-3 py-2 text-emerald-800">
                  <span>{outcomes[inv.outcome!]}</span>
                  <Button size="sm" variant="ghost" loading={act.isPending} onClick={() => act.mutate({ action: "reopen" })}>Reopen</Button>
                </div>
              ) : (
                <div className="space-y-2 rounded-lg border border-slate-200 p-3">
                  <div className="flex gap-2">
                    <select value={assignee} onChange={(e) => setAssignee(e.target.value)} className="h-9 flex-1 rounded-lg border border-slate-300 px-2" aria-label="Investigator">
                      {staff.map((s) => <option key={s} value={s}>{s}</option>)}
                    </select>
                    <Button size="sm" loading={act.isPending} onClick={() => act.mutate({ action: "assign", assignee })} data-testid="assign">Assign</Button>
                  </div>
                  <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={3} placeholder="Findings, interview notes, work reason…" className="w-full rounded-lg border border-slate-300 p-2" aria-label="Investigation note" />
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <Button size="sm" disabled={note.trim().length < 3} loading={act.isPending} onClick={() => act.mutate({ action: "note", note })} data-testid="add-note">Add note</Button>
                    <div className="flex gap-2">
                      <select value={outcome} onChange={(e) => setOutcome(e.target.value)} className="h-8 rounded-lg border border-slate-300 px-2 text-xs" aria-label="Outcome">
                        {Object.entries(outcomes).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                      </select>
                      <Button size="sm" variant="primary" disabled={note.trim().length < 5} loading={act.isPending} onClick={() => act.mutate({ action: "close", outcome, note })} data-testid="close-investigation">Close with findings</Button>
                    </div>
                  </div>
                </div>
              )}
              {act.error && <p className="text-rose-600" role="alert">{(act.error as Error).message}</p>}
            </>
          )}
        </div>
      </aside>
    </div>
  );
}

function Report() {
  const [days, setDays] = useState(30);
  const q = useQuery({ queryKey: ["phipa", "report", days], queryFn: () => api<PhipaReport>(`/api/phipa/report?days=${days}`) });
  const r = q.data;
  return (
    <Card title="Compliance report" actions={
      <>
        <select value={days} onChange={(e) => setDays(Number(e.target.value))} className="h-8 rounded-lg border border-slate-300 px-2 text-xs" aria-label="Period">
          {[7, 30, 90].map((d) => <option key={d} value={d}>Last {d} days</option>)}
        </select>
        <Button size="sm" onClick={() => window.print()}><Printer className="size-4" /> Print</Button>
      </>
    }>
      {q.isLoading && <Loading />}
      {q.error && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {r && (
        <div className="space-y-4 text-sm" data-testid="phipa-report">
          <p className="text-slate-600">Access monitoring from {r.since} to {r.generated_at.slice(0, 10)}: {r.alerts} alerts, {r.open} still open{r.median_hours_to_close != null && `, median ${r.median_hours_to_close} h to close`}.</p>
          <p className={clsx("flex items-center gap-2 font-medium", r.audit_chain.intact ? "text-emerald-700" : "text-rose-700")}>
            {r.audit_chain.intact ? <ShieldCheck className="size-4" /> : <ShieldAlert className="size-4" />}
            Audit log hash chain {r.audit_chain.intact ? "intact" : `broken at #${r.audit_chain.broken_at_seq}`} ({r.events_in_log} events)
          </p>
          <div className="grid gap-4 md:grid-cols-2">
            <table className="w-full text-left">
              <thead className="text-xs text-slate-500"><tr><th className="py-1 font-medium">Rule</th><th className="py-1 text-right font-medium">Alerts</th><th className="py-1 text-right font-medium">Closed</th></tr></thead>
              <tbody className="divide-y divide-slate-100">{Object.entries(r.by_rule).map(([k, v]) => <tr key={k}><td className="py-1.5">{v.label}</td><td className="tabular py-1.5 text-right">{v.alerts}</td><td className="tabular py-1.5 text-right">{v.closed}</td></tr>)}</tbody>
            </table>
            <table className="w-full text-left">
              <thead className="text-xs text-slate-500"><tr><th className="py-1 font-medium">Outcome of closed investigations</th><th className="py-1 text-right font-medium">Count</th></tr></thead>
              <tbody className="divide-y divide-slate-100">{Object.entries(r.outcomes).map(([k, v]) => <tr key={k}><td className="py-1.5">{k}</td><td className="tabular py-1.5 text-right">{v}</td></tr>)}</tbody>
            </table>
          </div>
        </div>
      )}
    </Card>
  );
}

export function PhipaPage() {
  const [tab, setTab] = useState<"queue" | "report">("queue");
  const [status, setStatus] = useState("");
  const [rule, setRule] = useState("");
  const [openId, setOpenId] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const qs = new URLSearchParams(Object.entries({ status, rule }).filter(([, v]) => v)).toString();
  const q = useQuery({ queryKey: ["phipa", "alerts", qs], queryFn: () => api<AlertList>(`/api/phipa/alerts${qs ? `?${qs}` : ""}`), refetchInterval: 5000 });
  const d = q.data;
  const exportCsv = async () => {
    setExporting(true);
    try { await download("/api/phipa/export.csv", `phipa-alerts-${new Date().toISOString().slice(0, 10)}.csv`); } finally { setExporting(false); }
  };
  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader title="PHIPA access monitoring" subtitle="Rules over the audit log flag unusual access to patient records. Every investigation step is recorded."
        actions={<Button onClick={exportCsv} loading={exporting} data-testid="export-phipa"><Download className="size-4" /> Export CSV</Button>} />
      {q.isLoading && <Loading />}
      {q.error && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {d && (
        <>
          <div className="mb-4 grid grid-cols-3 gap-3">
            <Stat label="New alerts" value={<span data-testid="kpi-new">{d.counts.new}</span>} tone={d.counts.new ? "red" : "green"} />
            <Stat label="Under investigation" value={d.counts.investigating} tone={d.counts.investigating ? "amber" : undefined} />
            <Stat label="Closed" value={d.counts.closed} />
          </div>
          <Tabs value={tab} onChange={setTab} tabs={[{ id: "queue", label: "Investigation queue" }, { id: "report", label: "Compliance report" }]} />
          {tab === "report" ? <Report /> : (
            <Card padded={false} actions={
              <div className="flex gap-2">
                <select value={status} onChange={(e) => setStatus(e.target.value)} className="h-8 rounded-lg border border-slate-300 px-2 text-xs" aria-label="Status"><option value="">Any status</option>{["new", "investigating", "closed"].map((s) => <option key={s} value={s}>{s}</option>)}</select>
                <select value={rule} onChange={(e) => setRule(e.target.value)} className="h-8 rounded-lg border border-slate-300 px-2 text-xs" aria-label="Rule"><option value="">Every rule</option>{Object.entries(d.rules).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
              </div>
            } title="Alerts (highest risk first)">
              {d.alerts.length === 0 ? <EmptyState title="No alerts match" /> : (
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-sm">
                    <thead className="bg-slate-50 text-xs text-slate-500"><tr>{["Risk", "Rule", "User", "Patient", "When", "Why", "Status"].map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}</tr></thead>
                    <tbody className="divide-y divide-slate-100">
                      {d.alerts.map((a) => (
                        <tr key={a.id} className="cursor-pointer hover:bg-slate-50" onClick={() => setOpenId(a.id)} data-testid={`phipa-${a.rule}-${a.user_id}`}>
                          <td className="px-3 py-2"><Risk v={a.risk} /></td>
                          <td className="px-3 py-2 font-medium text-slate-800">{a.rule_label}</td>
                          <td className="px-3 py-2">{a.user_name}<p className="text-xs text-slate-500">{ROLE_LABEL[a.role as Role] ?? a.role}{a.user_sites.length ? ` · ${a.user_sites.join(", ")}` : ""}</p></td>
                          <td className="px-3 py-2 text-xs">{a.patient_name ?? "–"}</td>
                          <td className="tabular px-3 py-2 text-xs whitespace-nowrap">{dateTime(a.first_at)}</td>
                          <td className="px-3 py-2 text-xs text-slate-600">{a.why}</td>
                          <td className="px-3 py-2"><Badge tone={STATUS_TONE[a.investigation.status]}>{a.investigation.status}</Badge></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>
          )}
          {openId && <AlertDrawer id={openId} outcomes={d.outcomes} staff={d.staff} onClose={() => setOpenId(null)} />}
        </>
      )}
    </div>
  );
}
