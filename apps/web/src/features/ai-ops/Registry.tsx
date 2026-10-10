// Model and prompt registries (spec 6.6): config/models.yaml with the ids read from the environment, and every
// prompt version with its four header lines and the prompt gate's verdict.

import { useQuery } from "@tanstack/react-query";
import { Badge, Card, ErrorState, Loading } from "../../components/ui";
import { api } from "../../lib/api";
import { NONE, usd } from "./fmt";

interface ModelRow {
  name: string;
  provider: string;
  tier: string;
  model_id_env: string;
  model_id: string | null;
  approved_tasks: string[];
  risk_tiers_allowed: string[];
  max_cost_per_case: number;
  data_handling: string;
  note: string;
  agents: string[];
}

interface PromptRow {
  version: string;
  agent_id: string;
  active: boolean;
  header: Record<string, string>;
  gate_ok: boolean | null;
  gate_problem: string | null;
  ungated: boolean | null;
  file: string;
}

export function Registry() {
  const q = useQuery({ queryKey: ["aiops-registry"], queryFn: () => api<{ models: ModelRow[]; prompts: PromptRow[]; gate: { failing: number; command: string } }>("/api/aiops/registry") });
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorState error={q.error} />;
  const d = q.data!;
  return (
    <div className="space-y-4">
      <Card title="Models (config/models.yaml)" padded={false}>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm" data-testid="registry-models">
            <thead className="bg-slate-50 text-xs text-slate-500"><tr>{["Entry", "Provider · tier", "Model id (from the environment)", "Approved for", "Risk tiers", "Max cost per case", "Data", "Agents on it"].map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}</tr></thead>
            <tbody className="divide-y divide-slate-100">
              {d.models.map((m) => (
                <tr key={m.name} className="align-top" data-testid={`model-${m.name}`}>
                  <td className="px-3 py-2 font-medium">{m.name}<p className="text-xs font-normal text-slate-500">{m.note}</p></td>
                  <td className="px-3 py-2 text-xs">{m.provider} · {m.tier}</td>
                  <td className="px-3 py-2 font-mono text-xs">{m.model_id ?? NONE}<p className="text-slate-400">{m.model_id_env}</p></td>
                  <td className="px-3 py-2 text-xs">{m.approved_tasks.includes("*") ? "every registered agent" : m.approved_tasks.join(", ")}</td>
                  <td className="px-3 py-2 text-xs">{m.risk_tiers_allowed.join(", ")}</td>
                  <td className="tabular px-3 py-2 text-xs">{usd(m.max_cost_per_case)}</td>
                  <td className="px-3 py-2 text-xs">{m.data_handling.replaceAll("_", " ")}</td>
                  <td className="px-3 py-2 text-xs text-slate-500">{m.agents.join(", ") || NONE}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
      <Card title="Prompts (apps/api/prompts/<agent>/<n>.md)" actions={<Badge tone={d.gate.failing ? "red" : "green"} >{d.gate.failing ? `${d.gate.failing} failing the gate` : "prompt gate passes"}</Badge>} padded={false}>
        <p className="px-4 pt-3 text-xs text-slate-500">The registry's prompt_version is the pointer (a rollback is a one-line change); the gate (<code>{d.gate.command}</code>, also in the backend tests) needs an eval run with the same text that met the agent's threshold for the active version.</p>
        <div className="overflow-x-auto">
          <table className="mt-2 w-full text-left text-xs" data-testid="registry-prompts">
            <thead className="bg-slate-50 text-slate-500"><tr>{["Version", "In use", "Author", "Change note", "Approved by", "Eval run", "Gate"].map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}</tr></thead>
            <tbody className="divide-y divide-slate-100">
              {d.prompts.map((p) => (
                <tr key={p.version} className="align-top" data-testid={`prompt-${p.version}`}>
                  <td className="px-3 py-2 font-mono">{p.version}</td>
                  <td className="px-3 py-2">{p.active ? <Badge tone="brand">active</Badge> : <Badge>candidate</Badge>}</td>
                  <td className="px-3 py-2">{p.header.author ?? NONE}</td>
                  <td className="max-w-md px-3 py-2">{p.header.change_note ?? p.header.error}</td>
                  <td className="px-3 py-2">{p.header.approved_by ?? NONE}</td>
                  <td className="px-3 py-2 font-mono">{p.header.eval_run ?? NONE}</td>
                  <td className="px-3 py-2">{p.gate_ok ? <Badge tone={p.ungated ? "amber" : "green"}>{p.ungated ? "no eval set" : "ok"}</Badge> : <Badge tone="red">fails</Badge>}
                    {p.gate_problem && <p className="mt-0.5 text-slate-500">{p.gate_problem}</p>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
