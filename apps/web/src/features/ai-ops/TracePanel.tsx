// One agent run's trace (spec 6.4): model, prompt version, inputs and outputs (references only), tool calls and
// model calls. Live traces come from /api/aiops/traces/:id; eval traces were captured in the run file before the
// eval's transaction was rolled back.

import { useQuery } from "@tanstack/react-query";
import { X } from "lucide-react";
import { Fragment } from "react";
import { Badge, Card, ErrorState, Loading } from "../../components/ui";
import { api } from "../../lib/api";
import { dateTime } from "../../lib/format";
import { ms, NONE, STATUS_TONE, usd } from "./fmt";
import type { Trace, TraceDetail } from "./types";

export function LiveTrace({ runId, onClose }: { runId: string; onClose?: () => void }) {
  const q = useQuery({ queryKey: ["aiops-trace", runId], queryFn: () => api<TraceDetail>(`/api/aiops/traces/${runId}`) });
  if (q.isLoading) return <Card><Loading /></Card>;
  if (q.error) return <Card><ErrorState error={q.error} /></Card>;
  const d = q.data!;
  return <TraceView trace={d.trace} failures={d.record.failures} cost={d.record.cost_usd} estimated={d.record.estimated}
    header={d.prompt_header} modelEntry={d.model_entry} durationMs={d.duration_ms} onClose={onClose} />;
}

export function TraceView({ trace: t, failures, cost, estimated, header, modelEntry, durationMs, captured, onClose }: {
  trace: Trace;
  failures?: string[];
  cost?: number;
  estimated?: boolean;
  header?: Record<string, string> | null;
  modelEntry?: string | null;
  durationMs?: number | null;
  captured?: boolean;
  onClose?: () => void;
}) {
  return (
    <Card
      title={<span data-testid="aiops-trace-title">Trace {t.run_id}</span>}
      actions={<>
        {captured && <Badge tone="blue">captured during the eval (rolled back)</Badge>}
        <Badge tone={STATUS_TONE[t.outcome] ?? "slate"}>{t.outcome}</Badge>
        {onClose && <button onClick={onClose} aria-label="Close trace" className="text-slate-400 hover:text-slate-700"><X className="size-4" /></button>}
      </>}
    >
      <dl className="grid grid-cols-[9rem_1fr] gap-x-3 gap-y-1 text-xs" data-testid="aiops-trace">
        <dt className="text-slate-500">Agent</dt><dd>{t.agent_id} {t.agent_version} (eval {t.eval_status})</dd>
        <dt className="text-slate-500">Model</dt><dd>{t.model ?? NONE} ({t.mode ?? NONE}){modelEntry ? ` · registry ${modelEntry}` : ""}</dd>
        <dt className="text-slate-500">Prompt version</dt><dd>{t.prompt_version ?? NONE}</dd>
        {header && <><dt className="text-slate-500">Prompt header</dt><dd>{header.change_note} · approved by {header.approved_by} · eval run {header.eval_run}</dd></>}
        <dt className="text-slate-500">Started</dt><dd>{dateTime(t.started_at)}{durationMs != null ? ` · ${ms(durationMs)}` : ""}</dd>
        <dt className="text-slate-500">On behalf of</dt><dd>{t.actor_id ?? NONE} ({t.actor_role ?? NONE})</dd>
        <dt className="text-slate-500">Encounter</dt><dd>{t.encounter_id ?? t.context_id ?? NONE}</dd>
        <dt className="text-slate-500">Inputs</dt><dd className="break-all">{t.input_refs.join(", ") || NONE}</dd>
        <dt className="text-slate-500">Outputs</dt><dd className="break-all">{t.output_refs.join(", ") || NONE}</dd>
        <dt className="text-slate-500">Provenance</dt><dd>{t.provenance_id ?? NONE}</dd>
        <dt className="text-slate-500">Human action</dt><dd>{t.human_action ? `${t.human_action.decision} by ${t.human_action.user_id ?? NONE} (${t.human_action.role})` : NONE}</dd>
        <dt className="text-slate-500">Tokens, cost</dt><dd>{t.tokens_in} in / {t.tokens_out} out · {usd(cost ?? t.cost_usd, estimated)}{estimated ? " (list-price estimate, mock)" : ""}</dd>
        <dt className="text-slate-500">Confidence</dt><dd>{t.confidence ?? NONE}</dd>
        {failures && failures.length > 0 && <><dt className="text-slate-500">Failures</dt><dd className="text-rose-700" data-testid="aiops-trace-failures">{failures.join("; ")}</dd></>}
      </dl>
      <h3 className="mt-4 text-xs font-semibold text-slate-700">Tool calls</h3>
      {t.tool_calls.length === 0 ? <p className="text-xs text-slate-500">None</p> : (
        <table className="mt-1 w-full text-left text-xs">
          <thead className="text-slate-500"><tr><th className="py-1">Tool</th><th>Level</th><th>Status</th><th>Attempts</th><th>Latency</th><th>Result</th></tr></thead>
          <tbody>
            {t.tool_calls.map((c, i) => (
              <tr key={i} className="border-t border-slate-100" data-testid="aiops-trace-tool-call">
                <td className="py-1 font-mono">{c.tool_id}</td>
                <td>{c.side_effect_level}</td>
                <td><Badge tone={STATUS_TONE[c.status] ?? "slate"}>{c.status}</Badge>{c.reason && <span className="ml-1 text-slate-500">{c.reason}</span>}</td>
                <td>{c.attempts}</td>
                <td>{ms(c.latency_ms)}</td>
                <td className="break-all">{c.result_ref.join(", ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <h3 className="mt-4 text-xs font-semibold text-slate-700">Model calls</h3>
      {t.llm_calls.length === 0 ? <p className="text-xs text-slate-500">None</p> : (
        <table className="mt-1 w-full text-left text-xs">
          <thead className="text-slate-500"><tr><th className="py-1">Call</th><th>Agent</th><th>Model</th><th>Prompt</th><th>Status</th><th>Tokens</th><th>Latency</th></tr></thead>
          <tbody>
            {t.llm_calls.map((c) => (
              <Fragment key={c.call_id}>
                <tr className="border-t border-slate-100">
                  <td className="py-1 font-mono">{c.call_id}</td><td>{c.agent_id}</td><td className="font-mono">{c.model}</td>
                  <td className="font-mono">{c.prompt_version}</td><td><Badge tone={STATUS_TONE[c.status] ?? "slate"}>{c.status}</Badge></td>
                  <td>{c.tokens_in} / {c.tokens_out}</td><td>{ms(c.latency_ms)}</td>
                </tr>
                {c.error && <tr><td colSpan={7} className="break-all pb-1 text-red-700">{c.error}</td></tr>}
              </Fragment>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}
