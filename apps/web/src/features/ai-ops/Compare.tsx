// Comparison view (spec 6.6): two runs of one agent side by side — two prompt versions or two models — with the
// six metrics and every case whose result differs.

import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Badge, Card, EmptyState, ErrorState, Loading } from "../../components/ui";
import { api } from "../../lib/api";
import { dateTime } from "../../lib/format";
import { metric, METRIC_LABEL, NONE, rate, short, STATUS_TONE } from "./fmt";
import type { CompareOption, Comparison, EvalCenter } from "./types";

function label(o: CompareOption): string {
  const what = [o.prompt_version, o.model].filter(Boolean).join(" · ");
  return `${o.run_id} — ${what}${o.variant ? " (candidate)" : ""} — ${rate(o.task_success)}`;
}

export function Compare({ initialAgent }: { initialAgent: string | null }) {
  const agents = useQuery({ queryKey: ["aiops-evals"], queryFn: () => api<EvalCenter>("/api/aiops/evals") });
  const [agent, setAgent] = useState<string>(initialAgent ?? "patient_message_triage");
  const options = useQuery({ queryKey: ["aiops-compare-options", agent],
    queryFn: () => api<{ runs: CompareOption[] }>(`/api/aiops/compare/options/${agent}`) });
  const [a, setA] = useState<string>("");
  const [b, setB] = useState<string>("");
  useEffect(() => {
    // default: the newest run of the version in use (A) against the newest other prompt version or model (B)
    const runs = options.data?.runs ?? [];
    const base = runs.find((r) => !r.variant) ?? runs[runs.length - 1];
    const other = runs.find((r) => r !== base && (r.prompt_version !== base?.prompt_version || r.model !== base?.model))
      ?? runs.find((r) => r !== base);
    setA(base?.run_id ?? "");
    setB(other?.run_id ?? "");
  }, [options.data]);
  const cmp = useQuery({ queryKey: ["aiops-compare", agent, a, b], enabled: !!a && !!b && a !== b,
    queryFn: () => api<Comparison>(`/api/aiops/compare?agent=${agent}&a=${a}&b=${b}`) });

  return (
    <div className="space-y-4">
      <Card>
        <div className="grid gap-3 md:grid-cols-3">
          <label className="text-xs text-slate-600">Agent
            <select className="mt-1 block w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm" value={agent}
              onChange={(e) => setAgent(e.target.value)} data-testid="compare-agent">
              {(agents.data?.agents ?? []).filter((x) => x.latest_run).map((x) => <option key={x.agent_id} value={x.agent_id}>{x.agent_id}</option>)}
            </select>
          </label>
          <label className="text-xs text-slate-600">Run A
            <select className="mt-1 block w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm" value={a} onChange={(e) => setA(e.target.value)} data-testid="compare-a">
              {(options.data?.runs ?? []).map((o) => <option key={o.run_id} value={o.run_id}>{label(o)}</option>)}
            </select>
          </label>
          <label className="text-xs text-slate-600">Run B
            <select className="mt-1 block w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm" value={b} onChange={(e) => setB(e.target.value)} data-testid="compare-b">
              {(options.data?.runs ?? []).map((o) => <option key={o.run_id} value={o.run_id}>{label(o)}</option>)}
            </select>
          </label>
        </div>
        <p className="mt-2 text-xs text-slate-500">A candidate is evaluated with <code>uv run python -m app.aiops.evalrun --prompt &lt;agent&gt;@&lt;n&gt;</code> or <code>--model &lt;registered model&gt;</code>; it does not change the release status of the version in use.</p>
      </Card>
      {(options.data?.runs.length ?? 0) < 2 && <Card><EmptyState title="This agent needs two runs to compare" /></Card>}
      {cmp.isLoading && <Loading />}
      {cmp.error && <ErrorState error={cmp.error} />}
      {cmp.data && <ComparisonView data={cmp.data} />}
    </div>
  );
}

function ComparisonView({ data }: { data: Comparison }) {
  const sides = [data.a, data.b];
  return (
    <>
      <Card title="Six metrics side by side" padded={false}>
        <table className="w-full text-left text-sm" data-testid="compare-metrics">
          <thead className="bg-slate-50 text-xs text-slate-500">
            <tr><th className="px-3 py-2">Metric</th>{sides.map((s, i) => (
              <th key={s.run_id} className="px-3 py-2 font-medium" data-testid={`compare-head-${i ? "b" : "a"}`}>
                {i ? "B" : "A"}: {s.prompt_version ?? NONE} · {s.model ?? NONE}
                <p className="font-mono font-normal text-slate-400">{s.run_id} · {dateTime(s.started_at)} · {s.mode}</p>
              </th>))}</tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {data.metrics_order.map((k) => (
              <tr key={k}>
                <td className="px-3 py-2 text-xs text-slate-600">{METRIC_LABEL[k]}</td>
                {sides.map((s) => <td key={s.run_id} className="tabular px-3 py-2">{metric(k, s.metrics[k], s.cost_estimated)}</td>)}
              </tr>
            ))}
            <tr>
              <td className="px-3 py-2 text-xs text-slate-600">Cases passed / verdict</td>
              {sides.map((s) => <td key={s.run_id} className="px-3 py-2 text-sm">{s.cases_passed} of {s.cases} <Badge tone={STATUS_TONE[s.status] ?? "slate"}>{s.status}</Badge> <span className="text-xs text-slate-500">bar {rate(s.threshold, 0)}</span></td>)}
            </tr>
          </tbody>
        </table>
      </Card>
      <Card title={`Per-case differences (${data.differences.length} of ${data.same_cases} shared cases)`} padded={false}>
        {data.differences.length === 0 ? <EmptyState title="Every case has the same result in both runs" /> : (
          <table className="w-full text-left text-xs" data-testid="compare-differences">
            <thead className="bg-slate-50 text-slate-500"><tr><th className="px-3 py-2">Case</th><th className="px-3 py-2">Change</th><th className="px-3 py-2">Expected</th><th className="px-3 py-2">A</th><th className="px-3 py-2">B</th></tr></thead>
            <tbody className="divide-y divide-slate-100">
              {data.differences.map((d) => (
                <tr key={d.id} data-testid={`compare-diff-${d.id}`}>
                  <td className="px-3 py-2 font-mono">{d.id}</td>
                  <td className="px-3 py-2"><Badge tone={d.change === "regressed" ? "red" : d.change === "fixed" ? "green" : "amber"}>{d.change}</Badge></td>
                  <td className="max-w-xs break-all px-3 py-2">{short((d.a ?? d.b)?.expected)}</td>
                  <td className="max-w-xs break-all px-3 py-2">{d.a ? <><Badge tone={d.a.passed ? "green" : "red"}>{d.a.passed ? "pass" : "fail"}</Badge> {short(d.a.got)}</> : NONE}</td>
                  <td className="max-w-xs break-all px-3 py-2">{d.b ? <><Badge tone={d.b.passed ? "green" : "red"}>{d.b.passed ? "pass" : "fail"}</Badge> {short(d.b.got)}</> : NONE}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </>
  );
}
