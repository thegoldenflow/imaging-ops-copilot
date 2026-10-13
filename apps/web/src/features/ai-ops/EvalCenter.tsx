// Eval Center (spec 6.6): every agent's eval set in the common format, the latest run's six metrics against the
// agent's threshold, the effective eval_status (release gate), the runs, and each failing case with the trace
// captured during the run.

import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { Fragment, useState } from "react";
import { Badge, Card, EmptyState, ErrorState, Loading, Tabs } from "../../components/ui";
import { api } from "../../lib/api";
import { dateTime } from "../../lib/format";
import { metric, METRIC_LABEL, ms, NONE, rate, short, STATUS_TONE, usd } from "./fmt";
import { TraceView } from "./TracePanel";
import type { CaseResult, EvalAgent, EvalCenter as EvalCenterT, Pipeline, RunAgent, RunRow, SixKey } from "./types";

const SIX: SixKey[] = ["schema_validity", "grounding_rate", "task_success", "human_override_rate", "latency_p50_ms",
  "latency_p95_ms", "cost_per_case_usd"];

export function EvalCenter({ agent, onAgent }: { agent: string | null; onAgent: (a: string | null) => void }) {
  const [view, setView] = useState<"agents" | "runs" | "platform">("agents");
  const q = useQuery({ queryKey: ["aiops-evals"], queryFn: () => api<EvalCenterT>("/api/aiops/evals") });
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const d = q.data!;
  if (agent) {
    const row = d.agents.find((a) => a.agent_id === agent);
    return <AgentEvals row={row} agent={agent} onBack={() => onAgent(null)} />;
  }
  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
        One runner for every agent: <code className="rounded bg-white px-1">{d.command}</code> (or <code className="rounded bg-white px-1">make eval</code>)
        writes <code className="rounded bg-white px-1">evals/runs/&lt;run&gt;.json</code>; each agent's eval status is derived from its latest run.
        Runs with the mock model are <b>baselines</b>: they show the pipeline, not a model's quality, and never pass the release gate on their own.
      </div>
      <Tabs value={view} onChange={setView} tabs={[{ id: "agents", label: "Agents" }, { id: "runs", label: "Runs" }, { id: "platform", label: "Platform suites" }]} />
      {view === "agents" && <AgentList rows={d.agents} onAgent={onAgent} />}
      {view === "runs" && <Runs />}
      {view === "platform" && <Platform data={d} />}
    </div>
  );
}

function AgentList({ rows, onAgent }: { rows: EvalAgent[]; onAgent: (a: string) => void }) {
  return (
    <Card padded={false}>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm" data-testid="eval-center-agents">
          <thead className="bg-slate-50 text-xs text-slate-500">
            <tr><th className="px-3 py-2">Agent</th><th className="px-3 py-2">Eval set</th><th className="px-3 py-2">Cases</th><th className="px-3 py-2">Status</th>
              {SIX.map((k) => <th key={k} className="px-2 py-2 font-medium">{METRIC_LABEL[k]}</th>)}</tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {rows.map((r) => (
              <tr key={r.agent_id} className="cursor-pointer hover:bg-slate-50" onClick={() => onAgent(r.agent_id)} data-testid={`eval-agent-${r.agent_id}`}>
                <td className="px-3 py-2"><p className="font-medium text-brand-700">{r.agent_id}</p><p className="text-xs text-slate-500">{r.prompt_version ?? "no prompt"}</p></td>
                <td className="px-3 py-2 text-xs">{r.suite ? <><p>{r.suite_title}</p><p className="font-mono text-slate-400">{r.source}</p></> : <span className="text-slate-400">no eval set</span>}</td>
                <td className="px-3 py-2 text-xs">{Object.entries(r.cases_by_source).map(([s, n]) => `${n} ${s}`).join(", ") || NONE}</td>
                <td className="px-3 py-2" title={r.eval_note}>
                  <Badge tone={STATUS_TONE[r.eval_status] ?? "slate"}>{r.eval_status}</Badge>
                  {r.status && <p className="mt-0.5 text-xs text-slate-500">{r.status} · bar {rate(r.threshold, 0)}</p>}
                </td>
                {SIX.map((k) => <td key={k} className="tabular px-2 py-2 text-xs">{r.metrics ? metric(k, r.metrics[k], !!r.cost_estimated) : NONE}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function Runs() {
  const q = useQuery({ queryKey: ["aiops-runs"], queryFn: () => api<{ runs: RunRow[]; command: string }>("/api/aiops/runs") });
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorState error={q.error} />;
  if (q.data!.runs.length === 0) return <Card><EmptyState title="No eval runs yet" hint={q.data!.command} /></Card>;
  return (
    <Card padded={false}>
      <table className="w-full text-left text-sm" data-testid="eval-runs">
        <thead className="bg-slate-50 text-xs text-slate-500"><tr>{["Run", "Started", "Mode", "Variant", "Agents", "Met", "Below", "No eval set", "Errors"].map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}</tr></thead>
        <tbody className="divide-y divide-slate-100">
          {q.data!.runs.map((r) => (
            <tr key={r.run_id}>
              <td className="px-3 py-2 font-mono text-xs">{r.run_id}</td>
              <td className="px-3 py-2 text-xs">{dateTime(r.started_at)}</td>
              <td className="px-3 py-2"><Badge tone={r.mode === "mock" ? "slate" : "ai"}>{r.mode === "mock" ? "mock baseline" : r.mode}</Badge></td>
              <td className="px-3 py-2 text-xs">{r.variant ? Object.entries(r.variant).map(([k, v]) => `${k} ${v}`).join(", ") : NONE}</td>
              <td className="tabular px-3 py-2">{r.agents}</td><td className="tabular px-3 py-2 text-emerald-700">{r.met}</td>
              <td className="tabular px-3 py-2 text-rose-700">{r.below}</td><td className="tabular px-3 py-2">{r.no_eval_set}</td><td className="tabular px-3 py-2">{r.errors}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

function Platform({ data }: { data: EvalCenterT }) {
  return (
    <div className="grid gap-3 md:grid-cols-3">
      {Object.entries(data.platform).map(([key, p]) => (
        <Card key={key} title={p?.title ?? key} actions={p && <Badge tone={p.passed ? "green" : "red"}>{p.passed ? "passed" : "failed"}</Badge>}>
          {!p ? <EmptyState title="No report yet" /> : (
            <div className="text-xs" data-testid={`platform-${key}`}>
              <p className="font-mono text-slate-400">{p.source}{p.run_at ? ` · ${dateTime(p.run_at)}` : ""}</p>
              <dl className="mt-2 grid max-h-48 grid-cols-2 gap-1 overflow-auto">
                {Object.entries(p.summary).map(([k, v]) => <Fragment key={k}><dt className="text-slate-500">{k.replaceAll("_", " ")}</dt><dd className="break-all">{String(v)}</dd></Fragment>)}
              </dl>
            </div>
          )}
        </Card>
      ))}
    </div>
  );
}

function AgentEvals({ row, agent, onBack }: { row?: EvalAgent; agent: string; onBack: () => void }) {
  const [tab, setTab] = useState<"results" | "cases">("results");
  const [open, setOpen] = useState<string | null>(null);
  const run = row?.latest_run;
  const q = useQuery({ queryKey: ["aiops-run-agent", run, agent], enabled: !!run,
    queryFn: () => api<RunAgent>(`/api/aiops/runs/${run}/agents/${agent}`) });
  const cases = useQuery({ queryKey: ["aiops-cases", agent], enabled: tab === "cases",
    queryFn: () => api<{ cases: Record<string, unknown>[]; source?: string }>(`/api/aiops/evals/${agent}/cases`) });
  return (
    <div className="space-y-4">
      <button onClick={onBack} className="text-sm text-brand-700 hover:underline">← All agents</button>
      <Card title={<span data-testid="eval-agent-title">{agent}</span>} actions={row && <Badge tone={STATUS_TONE[row.eval_status] ?? "slate"}>{row.eval_status}</Badge>}>
        {!row?.suite ? <EmptyState title="No eval set" hint="Add evals/<agent>/cases.jsonl in the common format, or point the registry's eval_status.run_id at the module's report." /> : (
          <div className="space-y-2 text-sm">
            <p>{row.suite_title} · <span className="font-mono text-xs text-slate-500">{row.source}</span></p>
            <p className="text-xs text-slate-600">{row.eval_note}</p>
            {row.metrics && (
              <div className="grid grid-cols-2 gap-3 md:grid-cols-4 lg:grid-cols-7" data-testid="eval-agent-metrics">
                {SIX.map((k) => <div key={k}><p className="text-xs text-slate-500">{METRIC_LABEL[k]}</p><p className="tabular font-semibold">{metric(k, row.metrics![k], !!row.cost_estimated)}</p></div>)}
              </div>
            )}
            {row.gates && Object.keys(row.gates).length > 0 && (
              <div className="flex flex-wrap gap-1">{Object.entries(row.gates).map(([k, g]) => <Badge key={k} tone={g.passed ? "green" : "red"}>{k}: {short(g.value)} / {short(g.threshold)}</Badge>)}</div>
            )}
            {row.extra && Object.keys(row.extra).length > 0 && (
              <p className="text-xs text-slate-500">Module metrics: {Object.entries(row.extra).map(([k, v]) => `${k.replaceAll("_", " ")} ${short(v)}`).join(" · ")}</p>
            )}
            {row.pipeline && <PipelineLine p={row.pipeline} />}
          </div>
        )}
      </Card>
      {row?.suite && (
        <>
          <Tabs value={tab} onChange={setTab} tabs={[{ id: "results", label: "Latest run, per case" }, { id: "cases", label: "Cases (common format)" }]} />
          {tab === "results" && (!run ? <Card><EmptyState title="Not run yet" /></Card> : q.isLoading ? <Loading /> : q.error ? <ErrorState error={q.error} /> : (
            <div className={clsx("grid gap-4", open && "lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]")}>
              <Card title={`${q.data!.run_id} · ${q.data!.mode === "mock" ? "mock baseline" : q.data!.mode} · ${q.data!.metrics.cases_passed} of ${q.data!.metrics.cases} passed`} padded={false}>
                <CaseTable rows={q.data!.case_results} open={open} onOpen={setOpen} />
              </Card>
              {open && <CaseDetail run={run} agent={agent} caseId={open} onClose={() => setOpen(null)} />}
            </div>
          ))}
          {tab === "cases" && (cases.isLoading ? <Loading /> : cases.error ? <ErrorState error={cases.error} /> : (
            <Card padded={false}>
              <table className="w-full text-left text-xs" data-testid="eval-cases">
                <thead className="bg-slate-50 text-slate-500"><tr><th className="px-3 py-2">Case</th><th className="px-3 py-2">Source</th><th className="px-3 py-2">input_refs</th><th className="px-3 py-2">expected</th></tr></thead>
                <tbody className="divide-y divide-slate-100">
                  {cases.data!.cases.map((c) => (
                    <tr key={String(c.id)}>
                      <td className="px-3 py-1.5 font-mono">{String(c.id)}</td>
                      <td className="px-3 py-1.5"><Badge tone={c.source === "human_review" ? "ai" : "slate"}>{String(c.source)}</Badge></td>
                      <td className="max-w-xs break-all px-3 py-1.5 text-slate-500">{(c.input_refs as string[] | undefined)?.join(", ") || NONE}{c.materialized ? " (written by the case)" : ""}</td>
                      <td className="max-w-md break-all px-3 py-1.5">{short(c.expected)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
          ))}
        </>
      )}
    </div>
  );
}

// The suite also runs agents that are not this one on each case: their calls are outside the metrics above.
function PipelineLine({ p }: { p: Pipeline }) {
  const others = Object.entries(p.other_agents).map(([a, o]) =>
    `${a} ${o.model_calls} calls${o.failed_calls ? ` (${o.failed_calls} failed)` : ""}, ${usd(o.cost_usd, p.cost_estimated)}`);
  return (
    <p className="text-xs text-slate-500" data-testid="eval-agent-pipeline">
      Whole case, every agent the suite runs: {p.model_calls} model calls, {usd(p.cost_per_case_usd, p.cost_estimated)} per
      case, latency p50 {ms(p.latency_p50_ms)} / p95 {ms(p.latency_p95_ms)}. Not in this agent's metrics: {others.join(" · ")}.
    </p>
  );
}

function CaseTable({ rows, open, onOpen }: { rows: CaseResult[]; open: string | null; onOpen: (id: string) => void }) {
  const sorted = [...rows].sort((a, b) => Number(a.passed) - Number(b.passed));
  return (
    <ul className="max-h-[36rem] divide-y divide-slate-100 overflow-auto" data-testid="eval-case-results">
      {sorted.map((c) => (
        <li key={c.id}>
          <button onClick={() => onOpen(c.id)} data-testid={`eval-case-${c.id}`}
            className={clsx("w-full px-3 py-2 text-left text-xs hover:bg-slate-50", open === c.id && "bg-brand-50")}>
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={c.passed ? "green" : "red"}>{c.passed ? "pass" : "fail"}</Badge>
              <span className="font-mono">{c.id}</span>
              <span className="text-slate-500">{c.source}</span>
              {c.has_trace && <Badge tone="blue">trace</Badge>}
            </div>
            {!c.passed && <p className="mt-0.5 break-all text-slate-600">expected {short(c.expected)} · got {short(c.got)}</p>}
          </button>
        </li>
      ))}
    </ul>
  );
}

function CaseDetail({ run, agent, caseId, onClose }: { run: string; agent: string; caseId: string; onClose: () => void }) {
  const q = useQuery({ queryKey: ["aiops-case", run, agent, caseId],
    queryFn: () => api<CaseResult & { mode: string; prompt_version: string; model: string }>(`/api/aiops/runs/${run}/agents/${agent}/cases/${encodeURIComponent(caseId)}`) });
  if (q.isLoading) return <Card><Loading /></Card>;
  if (q.error) return <Card><ErrorState error={q.error} /></Card>;
  const c = q.data!;
  return (
    <div className="space-y-3">
      <Card title={`Case ${c.id}`} actions={<><Badge tone={c.passed ? "green" : "red"}>{c.passed ? "pass" : "fail"}</Badge><button onClick={onClose} className="text-xs text-slate-500">close</button></>}>
        <dl className="grid grid-cols-[7rem_1fr] gap-x-3 gap-y-1 text-xs" data-testid="eval-case-detail">
          <dt className="text-slate-500">Expected</dt><dd className="break-all">{short(c.expected)}</dd>
          <dt className="text-slate-500">Got</dt><dd className="break-all">{short(c.got)}</dd>
          <dt className="text-slate-500">Prompt, model</dt><dd>{c.prompt_version ?? NONE} · {c.model ?? NONE} ({c.mode})</dd>
          {c.pipeline_cost_usd !== undefined && <><dt className="text-slate-500">Whole case</dt>
            <dd data-testid="eval-case-pipeline">{usd(c.pipeline_cost_usd, c.mode === "mock")} · {ms(c.pipeline_latency_ms)} model time, every agent (this agent {usd(c.cost_usd, c.mode === "mock")} · {ms(c.latency_ms)})</dd></>}
          <dt className="text-slate-500">Runs</dt><dd>{c.run_ids.join(", ") || NONE}</dd>
        </dl>
      </Card>
      {(c.traces ?? []).length === 0 ? <Card><EmptyState title="No trace captured for this case" hint="Traces are kept for failing cases of suites that run in the runner's transaction." /></Card> :
        (c.traces ?? []).map((t) => <TraceView key={t.run_id} trace={t} captured />)}
    </div>
  );
}
