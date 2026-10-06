import { useQuery } from "@tanstack/react-query";
import { Card, EmptyState, ErrorState, Loading, PageHeader, Stat, Badge } from "../../components/ui";
import { api } from "../../lib/api";
import { dateTime, pct } from "../../lib/format";

interface TaskRow {
  task: string;
  calls: number;
  failure_rate: number;
  avg_latency_ms: number;
  cost_usd: number;
  input_tokens: number;
  output_tokens: number;
}

interface Call {
  id: string;
  ts: string;
  task: string;
  model: string;
  mode: string;
  prompt_version: string;
  input_tokens: number;
  output_tokens: number;
  latency_ms: number;
  cost_usd: number;
  outcome: string;
  input_hash: string;
}

const OUTCOME_TONE: Record<string, "green" | "amber" | "red"> = {
  ok: "green",
  retried_ok: "amber",
  needs_human: "amber",
  unavailable: "red",
};

export function AiUsagePage() {
  const usage = useQuery({
    queryKey: ["ai-usage"],
    queryFn: () => api<{ mode: string; tasks: TaskRow[]; recent: Call[] }>("/api/admin/ai-usage"),
    refetchInterval: 5000,
  });

  const totals = usage.data?.tasks.reduce(
    (acc, t) => ({ calls: acc.calls + t.calls, cost: acc.cost + t.cost_usd }),
    { calls: 0, cost: 0 },
  );

  return (
    <div>
      <PageHeader
        title="AI usage"
        subtitle="Every Claude call goes through one gateway: de-identified input, schema-checked output, logged without PHI."
      />
      {usage.isLoading && <Loading />}
      {usage.error && <ErrorState error={usage.error} onRetry={() => usage.refetch()} />}
      {usage.data && (
        <>
          <div className="mb-4 grid gap-3 sm:grid-cols-3">
            <Stat label="Mode" value={usage.data.mode === "anthropic" ? "Claude API" : "Mock"} hint={usage.data.mode === "mock" ? "Set ANTHROPIC_API_KEY to use Claude" : undefined} />
            <Stat label="Calls" value={totals?.calls ?? 0} />
            <Stat label="Cost" value={`$${(totals?.cost ?? 0).toFixed(4)}`} hint="Estimated from token usage" />
          </div>
          <Card title="By feature" className="mb-4" padded={false}>
            {usage.data.tasks.length === 0 ? (
              <EmptyState title="No AI calls yet" hint="Generate a report draft or end a phone call to see entries here." />
            ) : (
              <table className="w-full text-left text-sm">
                <thead className="bg-slate-50 text-xs text-slate-500">
                  <tr>{["Task", "Calls", "Failure rate", "Avg latency", "Tokens in / out", "Cost"].map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}</tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {usage.data.tasks.map((t) => (
                    <tr key={t.task}>
                      <td className="px-3 py-2 font-medium">{t.task}</td>
                      <td className="tabular px-3 py-2">{t.calls}</td>
                      <td className="tabular px-3 py-2">{pct(t.failure_rate)}</td>
                      <td className="tabular px-3 py-2">{t.avg_latency_ms} ms</td>
                      <td className="tabular px-3 py-2">{t.input_tokens.toLocaleString()} / {t.output_tokens.toLocaleString()}</td>
                      <td className="tabular px-3 py-2">${t.cost_usd.toFixed(4)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Card>
          <Card title="Recent calls" padded={false}>
            {usage.data.recent.length === 0 ? (
              <EmptyState title="No calls yet" />
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead className="bg-slate-50 text-xs text-slate-500">
                    <tr>{["Time", "Task", "Model", "Prompt", "Latency", "Outcome", "Input hash"].map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}</tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {usage.data.recent.map((c) => (
                      <tr key={c.id}>
                        <td className="px-3 py-2 whitespace-nowrap">{dateTime(c.ts)}</td>
                        <td className="px-3 py-2">{c.task}</td>
                        <td className="px-3 py-2 font-mono text-xs">{c.model}</td>
                        <td className="px-3 py-2 font-mono text-xs">{c.prompt_version}</td>
                        <td className="tabular px-3 py-2">{c.latency_ms} ms</td>
                        <td className="px-3 py-2"><Badge tone={OUTCOME_TONE[c.outcome] ?? "slate"}>{c.outcome}</Badge></td>
                        <td className="px-3 py-2 font-mono text-xs text-slate-400">{c.input_hash}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </>
      )}
    </div>
  );
}
