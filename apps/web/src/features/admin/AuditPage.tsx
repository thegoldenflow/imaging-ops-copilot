import { useQuery } from "@tanstack/react-query";
import { Download, ShieldAlert, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { Badge, Button, Card, EmptyState, ErrorState, Loading, PageHeader, Tabs } from "../../components/ui";
import { api, download } from "../../lib/api";
import { dateTime } from "../../lib/format";

interface AuditEvent {
  seq: number;
  ts: string;
  user_name: string;
  role: string;
  action: string;
  resource_type: string;
  resource_id: string | null;
  outcome: string;
  source_ip: string | null;
  reason: string;
  hash: string;
  event_type: string | null;
  patient_mrn_hash: string | null;
  encounter_id: string | null;
  module: string | null;
  prompt_version: string | null;
}

type Filter = "all" | "denied" | "break_glass" | "sign" | "consent_change" | "ai_call";

const FILTERS: { id: Filter; label: string }[] = [
  { id: "all", label: "All events" },
  { id: "denied", label: "Denied (403)" },
  { id: "break_glass", label: "Break-glass" },
  { id: "sign", label: "Signatures" },
  { id: "consent_change", label: "Consent" },
  { id: "ai_call", label: "AI calls" },
];

const OUTCOME_TONE: Record<string, "red" | "green" | "amber"> = { denied: "red", not_justified: "red", needs_human: "amber", unavailable: "amber" };

export function AuditPage() {
  const [filter, setFilter] = useState<Filter>("all");
  const [mrn, setMrn] = useState("");
  const params = new URLSearchParams({ limit: "300" });
  if (filter === "denied") params.set("outcome", "denied");
  else if (filter !== "all") params.set("event_type", filter);
  if (mrn.trim().length === 8) params.set("mrn", mrn.trim());
  const query = params.toString();
  const events = useQuery({
    queryKey: ["audit", query],
    queryFn: () => api<{ events: AuditEvent[]; total: number }>(`/api/admin/audit?${query}`),
    refetchInterval: 5000,
  });
  const verify = useQuery({
    queryKey: ["audit-verify"],
    queryFn: () => api<{ intact: boolean; broken_at_seq: number | null; events: number }>("/api/admin/audit/verify"),
    refetchInterval: 5000,
  });

  return (
    <div>
      <PageHeader
        title="Audit log"
        subtitle="Every PHI read and write, AI call, signature, consent change, break-glass and denied request. Append-only, protected by a SHA-256 hash chain; export, never edit."
        actions={
          <>
            {verify.data &&
              (verify.data.intact ? (
                <Badge tone="green"><ShieldCheck className="size-3.5" /> Chain intact · {verify.data.events} events</Badge>
              ) : (
                <Badge tone="red"><ShieldAlert className="size-3.5" /> Chain broken at #{verify.data.broken_at_seq}</Badge>
              ))}
            <Button size="sm" onClick={() => download(`/api/admin/audit/export?${query}`, "audit-log.csv")}>
              <Download className="size-4" /> Export CSV
            </Button>
          </>
        }
      />
      <div className="flex flex-wrap items-end justify-between gap-3">
        <Tabs value={filter} onChange={setFilter} tabs={FILTERS} />
        <label className="mb-4 text-sm">
          <span className="mb-1 block text-xs font-medium text-slate-500">Patient MRN (matched by its keyed hash)</span>
          <input value={mrn} onChange={(e) => setMrn(e.target.value)} placeholder="8 digits" inputMode="numeric"
                 className="h-8 w-36 rounded-lg border border-slate-300 px-2 text-sm" data-testid="audit-mrn" />
        </label>
      </div>
      <Card padded={false}>
        {events.isLoading && <Loading />}
        {events.error && <ErrorState error={events.error} onRetry={() => events.refetch()} />}
        {events.data && events.data.events.length === 0 && <EmptyState title="No events yet" />}
        {events.data && events.data.events.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="bg-slate-50 text-xs text-slate-500">
                <tr>
                  {["#", "Time", "User", "Type", "Action", "Resource", "Module", "Outcome", "Reason", "Hash"].map((h) => (
                    <th key={h} className="px-3 py-2 font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {events.data.events.map((e) => (
                  <tr key={e.seq} data-testid={`audit-${e.event_type ?? "legacy"}`}>
                    <td className="tabular px-3 py-2 text-slate-400">{e.seq}</td>
                    <td className="px-3 py-2 whitespace-nowrap">{dateTime(e.ts)}</td>
                    <td className="px-3 py-2">
                      <p className="font-medium text-slate-800">{e.user_name}</p>
                      <p className="text-xs text-slate-500">{e.role}</p>
                    </td>
                    <td className="px-3 py-2">{e.event_type ? <Badge tone={e.event_type === "break_glass" ? "red" : "slate"}>{e.event_type}</Badge> : "–"}</td>
                    <td className="px-3 py-2">{e.action}</td>
                    <td className="px-3 py-2">
                      <p>{e.resource_type}</p>
                      {e.resource_id && <p className="text-xs text-slate-500">{e.resource_id}</p>}
                      {e.patient_mrn_hash && <p className="font-mono text-[11px] text-slate-400" title="Patient MRN, keyed hash">pt {e.patient_mrn_hash.slice(0, 8)}…</p>}
                    </td>
                    <td className="px-3 py-2 text-xs text-slate-600">
                      {e.module ?? "–"}
                      {e.prompt_version && <p className="text-slate-400">{e.prompt_version}</p>}
                    </td>
                    <td className="px-3 py-2">
                      <Badge tone={OUTCOME_TONE[e.outcome] ?? "green"}>{e.outcome}</Badge>
                    </td>
                    <td className="max-w-xs px-3 py-2 text-slate-600">{e.reason}</td>
                    <td className="px-3 py-2 font-mono text-xs text-slate-400">{e.hash.slice(0, 10)}…</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
