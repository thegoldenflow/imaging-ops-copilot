// AI Ops overview (spec 6.6): six cards for the window (hospital-wide), the agent table, the failure list with
// its traces, the business metrics and the dimensions (agent, model, unit, day). Every number is a trace
// aggregate from /api/aiops/overview.

import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { AlertTriangle } from "lucide-react";
import { useState } from "react";
import { Badge, Card, EmptyState, ErrorState, Loading, Tabs } from "../../components/ui";
import { api } from "../../lib/api";
import { dateTime } from "../../lib/format";
import { count, metric, ms, NONE, rate, STATUS_TONE, usd } from "./fmt";
import { BudgetStrip } from "./BudgetStrip";
import { LiveTrace } from "./TracePanel";
import type { Card as CardT, DimensionRow, Overview as OverviewT } from "./types";

const REFRESH_MS = 1000 * 30;
const WINDOWS = [{ days: 1, label: "Today" }, { days: 7, label: "7 days" }, { days: 30, label: "30 days" }];

function value(card: CardT): string {
  if (card.format === "usd") return usd(card.value, card.estimated);
  if (card.format === "ms") return ms(card.value);
  if (card.format === "pct") return rate(card.value);
  return count(card.value);
}

function detail(card: CardT): string {
  return Object.entries(card.detail)
    .map(([k, v]) => `${k.replace(/_(ms|usd)$/, "").replaceAll("_", " ")} ${metric(k, v, card.estimated)}`).join(" · ");
}

export function Overview({ trace, onTrace }: { trace: string | null; onTrace: (runId: string | null) => void }) {
  const [days, setDays] = useState(WINDOWS[0].days);
  const [dimension, setDimension] = useState<"by_model" | "by_unit" | "by_day" | "by_agent">("by_model");
  const q = useQuery({ queryKey: ["aiops-overview", days], queryFn: () => api<OverviewT>(`/api/aiops/overview?days=${days}`),
    refetchInterval: REFRESH_MS });
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const d = q.data!;
  const t = d.totals;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex gap-1" role="group" aria-label="Window">
          {WINDOWS.map((w) => (
            <button key={w.days} onClick={() => setDays(w.days)} aria-pressed={days === w.days}
              className={clsx("rounded-md px-2.5 py-1 text-xs font-medium", days === w.days ? "bg-brand-600 text-white" : "bg-slate-100 text-slate-600 hover:bg-slate-200")}>
              {w.label}
            </button>
          ))}
        </div>
        <p className="text-xs text-slate-500">Hospital-wide, {dateTime(d.window.from)} to now · model mode {d.mode}{t.cost_estimated ? " · ≈ cost: mock calls priced at the model's list price (nothing billed)" : ""}</p>
      </div>
      {d.budget && <BudgetStrip budget={d.budget} idle={d.idle} backgroundAi={d.background_ai} />}
      {d.alerts.open.length > 0 && (
        <div className="flex items-center gap-2 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800" data-testid="aiops-open-alerts">
          <AlertTriangle className="size-4" /> {d.alerts.open.length} open AI Ops alert{d.alerts.open.length === 1 ? "" : "s"} for the admin (Alerts tab).
        </div>
      )}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6" data-testid="aiops-cards">
        {d.cards.map((c) => (
          <div key={c.key} className="rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm" data-testid={`aiops-card-${c.key}`}>
            <p className="text-xs font-medium text-slate-500">{c.label}</p>
            <p className="tabular mt-1 text-2xl font-semibold text-slate-900">{value(c)}</p>
            <p className="mt-0.5 text-xs text-slate-500">{detail(c)}</p>
          </div>
        ))}
      </div>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5" data-testid="aiops-business">
        {[["Escalation", rate(t.escalation_rate)], ["Task completion", rate(t.task_completion_rate)],
          ["Agent-written Tasks", count(t.tasks_written)], ["Completed workflows", count(t.completed_workflows ?? null)],
          ["Cost per completed workflow", usd(t.cost_per_completed_workflow_usd ?? null, t.cost_estimated)]].map(([label, v]) => (
          <div key={label} className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
            <p className="text-xs text-slate-500">{label}</p><p className="tabular text-sm font-semibold">{v}</p>
          </div>
        ))}
      </div>
      <AgentTable data={d} />
      <div className={clsx("grid gap-4", trace && "lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]")}>
        <Card title={`Failures (${d.failure_count})`} padded={false}>
          {d.failures.length === 0 ? <EmptyState title="No failures in this window" /> : (
            <ul className="max-h-[32rem] divide-y divide-slate-100 overflow-auto" data-testid="aiops-failures">
              {d.failures.map((f) => (
                <li key={f.run_id}>
                  <button onClick={() => onTrace(f.run_id)} data-testid={`aiops-failure-${f.run_id}`}
                    className={clsx("w-full px-3 py-2 text-left text-sm hover:bg-slate-50", trace === f.run_id && "bg-brand-50")}>
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-medium text-slate-900">{f.agent_id}</span>
                      <Badge tone={STATUS_TONE[f.outcome] ?? "slate"}>{f.outcome}</Badge>
                      <span className="text-xs text-slate-500">{f.run_id} · {dateTime(f.started_at)} · {f.unit}</span>
                    </div>
                    <p className="text-xs text-rose-700">{f.failures.join("; ")}</p>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>
        {trace && <LiveTrace runId={trace} onClose={() => onTrace(null)} />}
      </div>
      <Card title="Operations and cost by dimension" padded={false}>
        <div className="px-4 pt-3">
          <Tabs value={dimension} onChange={setDimension} tabs={[{ id: "by_model", label: "Model" }, { id: "by_unit", label: "Unit" },
            { id: "by_day", label: "Day" }, { id: "by_agent", label: "Agent" }]} />
        </div>
        <Dimension rows={d[dimension]} keyName={{ by_model: "model", by_unit: "unit", by_day: "day", by_agent: "agent_id" }[dimension]} />
      </Card>
    </div>
  );
}

function AgentTable({ data }: { data: OverviewT }) {
  return (
    <Card title="Agents" padded={false}>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm" data-testid="aiops-agents">
          <thead className="bg-slate-50 text-xs text-slate-500">
            <tr>{["Agent", "Version", "Eval status", "Pass rate", "Human override", "p95 latency", "Cost (window)", "Runs", "Failed"].map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}</tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {data.agents.map((a) => (
              <tr key={a.agent_id} data-testid={`aiops-agent-${a.agent_id}`}>
                <td className="px-3 py-2"><p className="font-medium text-slate-900">{a.agent_id}</p><p className="text-xs text-slate-500">{a.prompt_version ?? "no prompt"} · {a.risk_tier}</p></td>
                <td className="px-3 py-2 font-mono text-xs">{a.version}</td>
                <td className="px-3 py-2" title={a.eval_note}><Badge tone={STATUS_TONE[a.eval_status] ?? "slate"}>{a.eval_status}</Badge>{a.eval_source === "run" && <span className="ml-1 text-xs text-slate-400">from run</span>}</td>
                <td className="tabular px-3 py-2">{a.pass_rate == null ? NONE : `${rate(a.pass_rate)} (bar ${rate(a.threshold, 0)})`}</td>
                <td className="tabular px-3 py-2">{a.decided ? `${rate(a.override_rate)} of ${a.decided}` : NONE}</td>
                <td className="tabular px-3 py-2">{ms(a.latency_p95_ms)}</td>
                <td className="tabular px-3 py-2">{usd(a.cost_usd, a.cost_estimated)}</td>
                <td className="tabular px-3 py-2">{count(a.requests)}</td>
                <td className="tabular px-3 py-2">{count(a.failed_runs)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

const DIM_COLUMNS: [keyof DimensionRow, string][] = [
  ["requests", "Runs"], ["model_calls", "Model calls"], ["tokens", "Tokens"], ["cost_usd", "Cost"],
  ["latency_p50_ms", "p50"], ["latency_p95_ms", "p95"], ["timeouts", "Timeouts"], ["schema_failures", "Schema failures"],
  ["tool_failures", "Tool failures"], ["tool_refusals", "Refused"], ["retrieval_failures", "Retrieval failures"],
  ["fallbacks", "Fallbacks"], ["retries", "Retries"], ["adoption_rate", "Adoption"], ["human_override_rate", "Override"],
  ["escalation_rate", "Escalation"], ["task_completion_rate", "Task completion"],
];

function Dimension({ rows, keyName }: { rows: DimensionRow[]; keyName: string }) {
  if (rows.length === 0) return <EmptyState title="No agent runs in this window" />;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-xs" data-testid="aiops-dimension">
        <thead className="bg-slate-50 text-slate-500"><tr><th className="px-3 py-2">{keyName}</th>{DIM_COLUMNS.map(([, h]) => <th key={h} className="px-2 py-2 font-medium">{h}</th>)}</tr></thead>
        <tbody className="divide-y divide-slate-100">
          {rows.map((r) => (
            <tr key={String(r[keyName])}>
              <td className="px-3 py-1.5 font-medium">{String(r[keyName])}</td>
              {DIM_COLUMNS.map(([k]) => <td key={String(k)} className="tabular px-2 py-1.5">{metric(String(k), r[k] as number | null, r.cost_estimated)}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
