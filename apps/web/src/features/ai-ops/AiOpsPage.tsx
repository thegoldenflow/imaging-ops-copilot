// AI Ops (spec 6.6): one page for observability and cost, the Eval Center, the comparison view, the model and
// prompt registries and the alerts. The former "AI usage" and "AI evaluations" pages are its "Model calls" and
// "Imaging reports" tabs (their old routes open them).

import { useSearchParams } from "react-router-dom";
import { PageHeader, Tabs } from "../../components/ui";
import { useAuth } from "../../lib/auth";
import { AiUsagePanel } from "../admin/AiUsagePanel";
import { ImagingEvalResults } from "../evals/ImagingEvalResults";
import { Alerts } from "./Alerts";
import { Compare } from "./Compare";
import { EvalCenter } from "./EvalCenter";
import { Overview } from "./Overview";
import { Registry } from "./Registry";

type Tab = "overview" | "evals" | "compare" | "registry" | "alerts" | "calls" | "imaging";

const OPS_ROLES = ["admin", "operations_manager", "medical_director"];
const TABS: { id: Tab; label: string; ops: boolean }[] = [
  { id: "overview", label: "Overview", ops: true },
  { id: "evals", label: "Eval Center", ops: false },
  { id: "compare", label: "Compare", ops: false },
  { id: "registry", label: "Registries", ops: false },
  { id: "alerts", label: "Alerts", ops: true },
  { id: "calls", label: "Model calls", ops: true },
  { id: "imaging", label: "Imaging reports", ops: false },
];

export function AiOpsPage({ defaultTab }: { defaultTab?: Tab }) {
  const { user } = useAuth();
  const [params, setParams] = useSearchParams();
  const isOps = !!user && OPS_ROLES.includes(user.role);
  const tabs = TABS.filter((t) => isOps || !t.ops);
  const requested = (params.get("tab") as Tab | null) ?? defaultTab ?? tabs[0].id;
  const tab = tabs.some((t) => t.id === requested) ? requested : tabs[0].id;
  const set = (patch: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) {
      if (v == null) next.delete(k);
      else next.set(k, v);
    }
    setParams(next);
  };
  return (
    <div>
      <PageHeader title="AI Ops" subtitle="Every agent's evals, traces, cost and alerts in one place. Numbers come from the trace store and the eval run files." />
      <Tabs value={tab} onChange={(t) => set({ tab: t, trace: null, agent: null })} tabs={tabs.map((t) => ({ id: t.id, label: t.label }))} />
      {tab === "overview" && <Overview trace={params.get("trace")} onTrace={(id) => set({ trace: id })} />}
      {tab === "evals" && <EvalCenter agent={params.get("agent")} onAgent={(a) => set({ agent: a })} />}
      {tab === "compare" && <Compare initialAgent={params.get("agent")} />}
      {tab === "registry" && <Registry />}
      {tab === "alerts" && <Alerts />}
      {tab === "calls" && <AiUsagePanel />}
      {tab === "imaging" && <ImagingEvalResults />}
    </div>
  );
}
