import { useQuery } from "@tanstack/react-query";
import { ShieldAlert, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { Badge, Card, EmptyState, ErrorState, Loading, PageHeader, Tabs } from "../../components/ui";
import { api } from "../../lib/api";
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
}

export function AuditPage() {
  const [filter, setFilter] = useState<"all" | "denied">("all");
  const events = useQuery({
    queryKey: ["audit", filter],
    queryFn: () => api<{ events: AuditEvent[]; total: number }>(`/api/admin/audit?limit=300${filter === "denied" ? "&outcome=denied" : ""}`),
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
        subtitle="Every PHI read and write, and every denied request. Append-only, protected by a SHA-256 hash chain."
        actions={
          verify.data &&
          (verify.data.intact ? (
            <Badge tone="green"><ShieldCheck className="size-3.5" /> Chain intact · {verify.data.events} events</Badge>
          ) : (
            <Badge tone="red"><ShieldAlert className="size-3.5" /> Chain broken at #{verify.data.broken_at_seq}</Badge>
          ))
        }
      />
      <Tabs value={filter} onChange={setFilter} tabs={[{ id: "all", label: "All events" }, { id: "denied", label: "Denied (403)" }]} />
      <Card padded={false}>
        {events.isLoading && <Loading />}
        {events.error && <ErrorState error={events.error} onRetry={() => events.refetch()} />}
        {events.data && events.data.events.length === 0 && <EmptyState title="No events yet" />}
        {events.data && events.data.events.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="bg-slate-50 text-xs text-slate-500">
                <tr>
                  {["#", "Time", "User", "Action", "Resource", "Outcome", "Reason", "Hash"].map((h) => (
                    <th key={h} className="px-3 py-2 font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {events.data.events.map((e) => (
                  <tr key={e.seq}>
                    <td className="tabular px-3 py-2 text-slate-400">{e.seq}</td>
                    <td className="px-3 py-2 whitespace-nowrap">{dateTime(e.ts)}</td>
                    <td className="px-3 py-2">
                      <p className="font-medium text-slate-800">{e.user_name}</p>
                      <p className="text-xs text-slate-500">{e.role}</p>
                    </td>
                    <td className="px-3 py-2">{e.action}</td>
                    <td className="px-3 py-2">
                      <p>{e.resource_type}</p>
                      {e.resource_id && <p className="text-xs text-slate-500">{e.resource_id}</p>}
                    </td>
                    <td className="px-3 py-2">
                      <Badge tone={e.outcome === "denied" ? "red" : "green"}>{e.outcome}</Badge>
                    </td>
                    <td className="px-3 py-2 text-slate-600">{e.reason}</td>
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
