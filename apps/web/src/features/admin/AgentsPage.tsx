import { useQuery } from "@tanstack/react-query";
import { Bot, Wrench } from "lucide-react";
import { useState } from "react";
import { Badge, Card, EmptyState, ErrorState, Loading, PageHeader, Tabs } from "../../components/ui";
import { api } from "../../lib/api";
import { LEVEL_TONE, TIER_LABEL, type ToolView, useAgentRegistry } from "../../lib/agents";
import { dateTime } from "../../lib/format";

interface RunRow {
  run_id: string;
  agent_id: string;
  agent_version: string;
  kind: string;
  outcome: string;
  model: string | null;
  prompt_version: string | null;
  evaluated: boolean;
  tool_calls: number;
  refused: number;
  started_at: string;
  cost_usd: number;
  actor_role: string | null;
}

interface ToolCall {
  tool_id: string;
  side_effect_level: string;
  args_hash: string;
  status: string;
  reason: string | null;
  result_ref: string[];
  latency_ms: number;
  attempts: number;
  approval_task_id: string | null;
}

interface RunDetail {
  trace: {
    run_id: string;
    agent_id: string;
    agent_version: string;
    model: string | null;
    prompt_version: string | null;
    mode: string | null;
    eval_status: string;
    actor_id: string | null;
    actor_role: string | null;
    encounter_id: string | null;
    context_id: string | null;
    input_refs: string[];
    output_refs: string[];
    tool_calls: ToolCall[];
    llm_calls: { call_id: string; agent_id: string; model: string; prompt_version: string; status: string }[];
    confidence: number | null;
    human_action: { role: string; user_id: string; decision: string; at: string } | null;
    outcome: string;
    tokens_in: number;
    tokens_out: number;
    cost_usd: number;
    provenance_id: string | null;
  };
  provenance: { id: string; target: { reference: string }[] } | null;
}

const STATUS_TONE: Record<string, "green" | "amber" | "red" | "slate" | "blue"> = {
  ok: "green", replayed: "blue", fallback: "amber", approval_required: "amber", refused: "red", failed: "red",
  completed: "green", needs_human: "amber", unavailable: "red", running: "slate",
};

/** The agent and tool registries of spec 6.4 and the traces of recent runs (admin). */
export function AgentsPage() {
  const [tab, setTab] = useState<"agents" | "tools" | "runs">("agents");
  const registry = useAgentRegistry();
  return (
    <div>
      <PageHeader
        title="AI agents"
        subtitle="Every AI function is a registered agent calling registered tools; the tool gateway checks each call."
        actions={registry.data && <Badge tone={registry.data.mode === "prod" ? "green" : "amber"}>Mode: {registry.data.mode}</Badge>}
      />
      <Tabs value={tab} onChange={setTab} tabs={[
        { id: "agents", label: "Agents" }, { id: "tools", label: "Tools" }, { id: "runs", label: "Recent runs" }]} />
      {tab === "agents" && <AgentsTable />}
      {tab === "tools" && <ToolsTable />}
      {tab === "runs" && <Runs />}
    </div>
  );
}

function AgentsTable() {
  const registry = useAgentRegistry();
  if (registry.isLoading) return <Loading />;
  if (registry.error) return <ErrorState error={registry.error} />;
  return (
    <Card padded={false}>
      <table className="w-full text-left text-sm">
        <thead className="border-b border-slate-200 bg-slate-50 text-xs text-slate-500">
          <tr><th className="px-3 py-2">Agent</th><th className="px-3 py-2">Kind</th><th className="px-3 py-2">Risk tier</th><th className="px-3 py-2">Evaluation</th><th className="px-3 py-2">Deployment</th><th className="px-3 py-2">Tools</th><th className="px-3 py-2">Signed by</th></tr>
        </thead>
        <tbody>
          {registry.data!.agents.map((a) => (
            <tr key={a.agent_id} className="border-b border-slate-100 align-top" data-testid={`agent-${a.agent_id}`}>
              <td className="px-3 py-2">
                <p className="font-medium text-slate-900"><Bot className="mr-1 inline size-3.5 text-slate-400" />{a.agent_id} <span className="text-xs font-normal text-slate-500">{a.version}</span></p>
                <p className="max-w-md text-xs text-slate-500">{a.purpose}</p>
              </td>
              <td className="px-3 py-2 text-xs">{a.kind.replace("_", " ")}</td>
              <td className="px-3 py-2 text-xs">{TIER_LABEL[a.risk_tier]}</td>
              <td className="px-3 py-2">
                <Badge tone={a.eval_status.status === "passed" ? "green" : a.eval_status.status === "failed" ? "red" : a.eval_status.status === "pending" ? "amber" : "slate"}>
                  {a.eval_status.status === "not_applicable" ? "no model" : a.eval_status.status}
                </Badge>
                {a.not_evaluated_flag && <span className="ml-1 text-xs text-amber-700">output flagged</span>}
                {a.refused_reason && <p className="text-xs text-rose-600">{a.refused_reason}</p>}
              </td>
              <td className="px-3 py-2 text-xs">{a.deployment_status}</td>
              <td className="px-3 py-2 text-xs text-slate-600">{a.allowed_tools.join(", ") || "–"}</td>
              <td className="px-3 py-2 text-xs">{a.required_signoff_role.join(a.cosign ? " + " : " / ") || "–"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

function ToolsTable() {
  const tools = useQuery({ queryKey: ["agent-tools"], queryFn: () => api<{ tools: ToolView[] }>("/api/agents/tools") });
  if (tools.isLoading) return <Loading />;
  if (tools.error) return <ErrorState error={tools.error} />;
  return (
    <Card padded={false}>
      <table className="w-full text-left text-sm">
        <thead className="border-b border-slate-200 bg-slate-50 text-xs text-slate-500">
          <tr><th className="px-3 py-2">Tool</th><th className="px-3 py-2">Side effect</th><th className="px-3 py-2">Caller roles</th><th className="px-3 py-2">Approved by</th><th className="px-3 py-2">Idempotent</th><th className="px-3 py-2">Attempts</th><th className="px-3 py-2">Audit</th></tr>
        </thead>
        <tbody>
          {tools.data!.tools.map((t) => (
            <tr key={t.tool_id} className="border-b border-slate-100 align-top" data-testid={`tool-${t.tool_id}`}>
              <td className="px-3 py-2">
                <p className="font-medium text-slate-900"><Wrench className="mr-1 inline size-3.5 text-slate-400" />{t.tool_id}</p>
                <p className="max-w-md text-xs text-slate-500">{t.description}</p>
              </td>
              <td className="px-3 py-2"><Badge tone={LEVEL_TONE[t.side_effect_level]}>{t.side_effect_level}</Badge></td>
              <td className="px-3 py-2 text-xs">{t.permission_policy.roles.join(", ")}</td>
              <td className="px-3 py-2 text-xs">{t.permission_policy.approver_roles === "signoff" ? "the agent's signers" : t.permission_policy.approver_roles.join(", ") || "–"}</td>
              <td className="px-3 py-2 text-xs">{t.idempotency.required ? `${t.idempotency.window_hours} h` : "–"}</td>
              <td className="px-3 py-2 text-xs">{t.retry.max_attempts}</td>
              <td className="px-3 py-2 text-xs">{t.audit_level}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

function Runs() {
  const [open, setOpen] = useState<string | null>(null);
  const runs = useQuery({ queryKey: ["agent-runs"], queryFn: () => api<{ runs: RunRow[] }>("/api/agents/runs?limit=40") });
  if (runs.isLoading) return <Loading />;
  if (runs.error) return <ErrorState error={runs.error} />;
  if (runs.data!.runs.length === 0) return <EmptyState title="No agent runs yet" />;
  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
      <Card padded={false}>
        <ul className="divide-y divide-slate-100">
          {runs.data!.runs.map((r) => (
            <li key={r.run_id}>
              <button className={`w-full px-3 py-2 text-left text-sm hover:bg-slate-50 ${open === r.run_id ? "bg-brand-50" : ""}`} onClick={() => setOpen(r.run_id)} data-testid={`run-${r.run_id}`}>
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium text-slate-900">{r.agent_id}</span>
                  <Badge tone={STATUS_TONE[r.outcome] ?? "slate"}>{r.outcome}</Badge>
                  {r.refused > 0 && <Badge tone="red">{r.refused} refused</Badge>}
                  <span className="text-xs text-slate-500">{r.run_id} · {dateTime(r.started_at)}</span>
                </div>
                <p className="text-xs text-slate-500">{r.kind === "call" ? "model call" : `${r.tool_calls} tool calls`} · {r.prompt_version ?? "no prompt"} · {r.actor_role}</p>
              </button>
            </li>
          ))}
        </ul>
      </Card>
      {open ? <RunTrace runId={open} /> : <Card><EmptyState title="Pick a run to see its trace" /></Card>}
    </div>
  );
}

function RunTrace({ runId }: { runId: string }) {
  const detail = useQuery({ queryKey: ["agent-run", runId], queryFn: () => api<RunDetail>(`/api/agents/runs/${runId}`) });
  if (detail.isLoading) return <Card><Loading /></Card>;
  if (detail.error) return <Card><ErrorState error={detail.error} /></Card>;
  const t = detail.data!.trace;
  return (
    <Card title={`Trace ${t.run_id}`} actions={<Badge tone={STATUS_TONE[t.outcome] ?? "slate"}>{t.outcome}</Badge>}>
      <dl className="grid grid-cols-[8rem_1fr] gap-x-3 gap-y-1 text-xs" data-testid="trace">
        <dt className="text-slate-500">Agent</dt><dd>{t.agent_id} {t.agent_version} (eval {t.eval_status})</dd>
        <dt className="text-slate-500">Model</dt><dd>{t.model ?? "–"} ({t.mode}) · {t.prompt_version ?? "–"}</dd>
        <dt className="text-slate-500">On behalf of</dt><dd>{t.actor_id} ({t.actor_role})</dd>
        <dt className="text-slate-500">Encounter</dt><dd>{t.encounter_id ?? t.context_id ?? "–"}</dd>
        <dt className="text-slate-500">Inputs</dt><dd className="break-all">{t.input_refs.join(", ") || "–"}</dd>
        <dt className="text-slate-500">Outputs</dt><dd className="break-all">{t.output_refs.join(", ") || "–"}</dd>
        <dt className="text-slate-500">Provenance</dt><dd>{t.provenance_id ?? "–"}</dd>
        <dt className="text-slate-500">Human action</dt><dd>{t.human_action ? `${t.human_action.decision} by ${t.human_action.user_id} (${t.human_action.role})` : "–"}</dd>
        <dt className="text-slate-500">Tokens, cost</dt><dd>{t.tokens_in} in / {t.tokens_out} out · ${t.cost_usd.toFixed(4)}</dd>
        <dt className="text-slate-500">Confidence</dt><dd>{t.confidence ?? "–"}</dd>
      </dl>
      <h3 className="mt-4 text-xs font-semibold text-slate-700">Tool calls</h3>
      {t.tool_calls.length === 0 ? <p className="text-xs text-slate-500">None</p> : (
        <table className="mt-1 w-full text-left text-xs">
          <thead className="text-slate-500"><tr><th className="py-1">Tool</th><th>Level</th><th>Status</th><th>Args hash</th><th>ms</th><th>Result</th></tr></thead>
          <tbody>
            {t.tool_calls.map((c, i) => (
              <tr key={i} className="border-t border-slate-100" data-testid="trace-tool-call">
                <td className="py-1 font-mono">{c.tool_id}</td>
                <td>{c.side_effect_level}</td>
                <td><Badge tone={STATUS_TONE[c.status] ?? "slate"}>{c.status}</Badge>{c.reason && <span className="ml-1 text-slate-500">{c.reason}</span>}</td>
                <td className="font-mono">{c.args_hash.slice(0, 10)}</td>
                <td>{c.latency_ms}</td>
                <td className="break-all">{c.result_ref.join(", ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <h3 className="mt-4 text-xs font-semibold text-slate-700">Model calls</h3>
      <ul className="mt-1 space-y-0.5 text-xs text-slate-600">
        {t.llm_calls.map((c) => <li key={c.call_id}>{c.call_id} · {c.agent_id} · {c.model} · {c.prompt_version} · {c.status}</li>)}
      </ul>
    </Card>
  );
}
