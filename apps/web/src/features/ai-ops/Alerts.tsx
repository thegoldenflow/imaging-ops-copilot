// AI Ops alerts (spec 6.6): the thresholds in config/alerts.yaml, today's breaches and the admin Tasks they opened.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge, Button, Card, EmptyState, ErrorState, Loading } from "../../components/ui";
import { api, post } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { dateTime } from "../../lib/format";
import { metric, NONE, STATUS_TONE } from "./fmt";
import type { AlertTask } from "./types";

interface AlertConfig { id: string; title: string; metric: string; per: string; op: string; threshold: number; min_requests: number; priority: string }
interface Breach { alert_id: string; title: string; scope: string; per: string; metric: string; value: number; threshold: number; op: string; estimated: boolean; task?: string }

export function Alerts() {
  const { user } = useAuth();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["aiops-alerts"], queryFn: () => api<{ config: AlertConfig[]; tasks: AlertTask[]; breaches: Breach[] }>("/api/aiops/alerts") });
  const check = useMutation({
    mutationFn: () => post<{ opened: string[]; breaches: Breach[] }>("/api/aiops/alerts/check", {}),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["aiops-alerts"] }); qc.invalidateQueries({ queryKey: ["aiops-overview"] }); },
  });
  const resolve = useMutation({
    mutationFn: (id: string) => post(`/api/aiops/alerts/${id}/resolve`, { note: "Reviewed on the AI Ops page" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["aiops-alerts"] }),
  });
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorState error={q.error} />;
  const d = q.data!;
  return (
    <div className="space-y-4">
      <Card title="Thresholds (config/alerts.yaml)" actions={<Button size="sm" onClick={() => check.mutate()} loading={check.isPending} data-testid="alerts-check">Check now</Button>} padded={false}>
        <p className="px-4 pt-3 text-xs text-amber-800">Demo values: set low so an alert can be triggered in the demo. The API also checks every minute.</p>
        {check.data && <p className="px-4 pt-2 text-xs text-slate-600" data-testid="alerts-check-result">{check.data.opened.length ? `Opened ${check.data.opened.join(", ")}` : "No new alert Task (breaches already have one, or none)."}</p>}
        <table className="mt-2 w-full text-left text-sm" data-testid="alerts-config">
          <thead className="bg-slate-50 text-xs text-slate-500"><tr>{["Alert", "Metric", "Per", "Threshold", "Priority"].map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}</tr></thead>
          <tbody className="divide-y divide-slate-100">
            {d.config.map((a) => (
              <tr key={a.id}><td className="px-3 py-2">{a.title}<p className="font-mono text-xs text-slate-400">{a.id}</p></td><td className="px-3 py-2 text-xs">{a.metric}</td>
                <td className="px-3 py-2 text-xs">{a.per}</td><td className="tabular px-3 py-2 text-xs">{a.op} {metric(a.metric, a.threshold)}{a.min_requests ? ` (from ${a.min_requests} runs)` : ""}</td><td className="px-3 py-2 text-xs">{a.priority}</td></tr>
            ))}
          </tbody>
        </table>
      </Card>
      <Card title="Breaches today" padded={false}>
        {d.breaches.length === 0 ? <EmptyState title="No threshold passed today" /> : (
          <table className="w-full text-left text-sm" data-testid="alerts-breaches">
            <thead className="bg-slate-50 text-xs text-slate-500"><tr>{["Alert", "Scope", "Value", "Threshold"].map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}</tr></thead>
            <tbody className="divide-y divide-slate-100">
              {d.breaches.map((b) => (
                <tr key={`${b.alert_id}-${b.scope}`}><td className="px-3 py-2 text-xs">{b.alert_id}</td><td className="px-3 py-2">{b.per} {b.scope}</td>
                  <td className="tabular px-3 py-2">{metric(b.metric, b.value, b.estimated)}</td><td className="tabular px-3 py-2">{b.op} {metric(b.metric, b.threshold)}</td></tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      <Card title="Admin Tasks" padded={false}>
        {d.tasks.length === 0 ? <EmptyState title="No alert Task yet" /> : (
          <ul className="divide-y divide-slate-100" data-testid="alerts-tasks">
            {d.tasks.map((t) => (
              <li key={t.id} className="px-4 py-3 text-sm" data-testid={`alert-task-${t.id}`}>
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-xs">Task/{t.id}</span>
                  <Badge tone={STATUS_TONE[t.status] ?? (t.status === "requested" ? "red" : "slate")}>{t.status}</Badge>
                  <Badge>{t.priority}</Badge>
                  <span className="text-xs text-slate-500">{t.alert_id} · {t.scope} · {t.day} · {t.authored ? dateTime(t.authored) : NONE}</span>
                  {t.status === "requested" && user?.role === "admin" && (
                    <Button size="sm" variant="secondary" onClick={() => resolve.mutate(t.id)} loading={resolve.isPending} data-testid={`alert-resolve-${t.id}`}>Resolve</Button>
                  )}
                </div>
                <p className="mt-1 text-xs text-slate-700">{t.description}</p>
                {t.notes.length > 0 && <p className="mt-0.5 text-xs text-slate-500">{t.notes.join(" · ")}</p>}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
