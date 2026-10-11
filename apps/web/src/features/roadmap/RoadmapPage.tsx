// Roadmap (spec 8.1, WP10): one card per module the platform could grow, none of them implemented. Each card says
// the pain, the data it would read, its risk tier, what it reuses and how the demo handles it, and ends with the
// fixed roadmap footer. The stubs the hospital storyline walks through on preset data link to their step and show
// their registry entry. Everything comes from GET /api/roadmap (config/roadmap.yaml joined with the registries).

import clsx from "clsx";
import { ArrowRight, BookMarked, Map as MapIcon, Route as RouteIcon } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Card, ErrorState, Loading, PageHeader } from "../../components/ui";
import { ROLE_LABEL } from "../../lib/types";
import { KIND_LABEL, TIER_TONE, useRoadmap, type RiskTier, type RoadmapCard } from "./api";

type Filter = "all" | RiskTier | "storyline";

const FILTERS: { id: Filter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "storyline", label: "In the storyline" },
  { id: "ops", label: "ops" },
  { id: "documentation", label: "documentation" },
  { id: "clinical_ds", label: "clinical_ds" },
];

export function RoadmapPage() {
  const q = useRoadmap();
  const [filter, setFilter] = useState<Filter>("all");
  return (
    <div className="mx-auto max-w-6xl" data-testid="roadmap">
      <PageHeader
        title="Roadmap"
        subtitle="Modules the platform could grow next (spec 8.1). None is implemented: each card shows the registry, tool gateway, review queue and eval pieces it would reuse. The storyline walks through four of them once, on preset data."
        actions={<Link to="/safety" className="text-sm font-medium text-brand-700 hover:underline">Safety tiers →</Link>}
      />
      {q.isLoading ? <Loading /> : q.error ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : q.data && (
        <>
          <div className="mb-4 flex flex-wrap items-center gap-2 text-sm">
            {FILTERS.map((f) => {
              const n = f.id === "all" ? q.data.counts.cards
                : f.id === "storyline" ? q.data.counts.preset
                : q.data.counts.by_tier[f.id];
              return (
                <button key={f.id} onClick={() => setFilter(f.id)} data-testid={`roadmap-filter-${f.id}`}
                  className={clsx("rounded-full border px-3 py-1 text-xs font-medium",
                    filter === f.id ? "border-brand-600 bg-brand-50 text-brand-700" : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50")}>
                  {f.label} <span className="tabular text-slate-400">{n}</span>
                </button>
              );
            })}
          </div>
          <div className="grid gap-4 lg:grid-cols-2">
            {q.data.cards
              .filter((c) => filter === "all" || (filter === "storyline" ? c.demo === "preset" : c.risk_tier === filter))
              .map((card) => <RoadmapCardView key={card.id} card={card} />)}
          </div>
        </>
      )}
    </div>
  );
}

function RoadmapCardView({ card }: { card: RoadmapCard }) {
  const entry = card.registry_entry;
  return (
    <div id={card.id} data-testid={`roadmap-card-${card.id}`} className="scroll-mt-4">
      <Card
        className="h-full"
        title={<span className="flex items-center gap-2"><MapIcon className="size-4 text-slate-400" />{card.title}</span>}
        actions={<Badge tone={TIER_TONE[card.risk_tier]}>{card.risk_tier}</Badge>}
      >
        <dl className="grid grid-cols-[7.5rem_1fr] gap-x-3 gap-y-2 text-sm">
          <dt className="text-slate-500">Pain</dt>
          <dd className="text-slate-900">{card.pain}</dd>
          <dt className="text-slate-500">Data source</dt>
          <dd className="text-slate-900">{card.data_source}</dd>
          <dt className="text-slate-500">Risk tier</dt>
          <dd className="text-slate-900">{card.risk_tier}</dd>
          <dt className="text-slate-500">Reuses</dt>
          <dd className="text-slate-900">
            {card.reuses}
            {card.reuse_refs.length > 0 && (
              <ul className="mt-1 space-y-0.5 font-mono text-[11px] text-slate-500">
                {card.reuse_refs.map((p) => <li key={p}>{p}</li>)}
              </ul>
            )}
          </dd>
          <dt className="text-slate-500">In the demo</dt>
          <dd className="text-slate-900" data-testid={`roadmap-demo-${card.id}`}>
            {card.demo === "preset" && <Badge tone="brand" className="mr-1">Preset data</Badge>}
            {card.demo_note}
          </dd>
          {card.spec_ref && (
            <>
              <dt className="text-slate-500">Absorbs</dt>
              <dd className="text-slate-700">{card.spec_ref}</dd>
            </>
          )}
        </dl>

        {(card.step || entry) && (
          <div className="mt-3 space-y-2 rounded-lg border border-slate-200 bg-slate-50/60 p-3 text-xs">
            {card.step && (
              <Link to={card.step.href} className="flex items-center gap-1.5 font-medium text-brand-700 hover:underline"
                data-testid={`roadmap-step-${card.id}`}>
                <RouteIcon className="size-3.5" />Storyline step {card.step.n}: {card.step.title}<ArrowRight className="size-3.5" />
              </Link>
            )}
            {entry && (
              <div data-testid={`roadmap-registry-${card.id}`}>
                <p className="flex flex-wrap items-center gap-1.5 text-slate-700">
                  <BookMarked className="size-3.5 text-slate-400" />
                  Registry entry <code className="font-mono text-slate-900">{entry.agent_id}</code> {entry.version}
                  <Badge tone="slate">{KIND_LABEL[entry.kind]}</Badge>
                  <Badge tone={TIER_TONE[entry.risk_tier]}>{entry.risk_tier}</Badge>
                </p>
                <p className="mt-1 text-slate-600">{entry.purpose}</p>
                <p className="mt-1 text-slate-600">
                  Sign-off: {entry.required_signoff_role.map((r) => ROLE_LABEL[r] ?? r).join(", ") || "none"} · tools:{" "}
                  {entry.allowed_tools.join(", ")} · deployment {entry.deployment_status} · eval {entry.eval_status.replace("_", " ")}
                </p>
                <p className="mt-1 font-mono text-[11px] text-slate-500">{entry.path}</p>
              </div>
            )}
          </div>
        )}

        <p className="mt-4 border-t border-slate-100 pt-3 text-xs text-slate-500" data-testid="roadmap-footer">{card.footer}</p>
      </Card>
    </div>
  );
}
