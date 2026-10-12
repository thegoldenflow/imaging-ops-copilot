// Safety tiers and architecture (spec 8.3, 6.7, WP10). The three risk tiers with what the AI may do, who signs and
// the regulatory note; the second axis, tools by side effect; the retired Level 0-4 scale mapped onto both; the
// guardrails with their latest eval results; the honest boundaries; the positioning sentences. The words come from
// config/safety.yaml, everything countable from the agent and tool registries and the eval runs
// (GET /api/roadmap/safety), so the page cannot drift from what the gateway enforces. The audience talk tracks are
// in docs/interview-narrative.md, not on screen.

import { CheckCircle2, Layers, Quote, ShieldAlert, XCircle } from "lucide-react";
import { Link } from "react-router-dom";
import { Badge, Card, ErrorState, Loading, PageHeader, Stat } from "../../components/ui";
import { ROLE_LABEL, type Role } from "../../lib/types";
import {
  KIND_LABEL, TIER_TONE, useSafety, type Guardrail, type LevelView, type RunFacts, type Safety, type SideEffectLevel,
  type TierView,
} from "./api";

const LEVEL_TONE: Record<SideEffectLevel, "slate" | "blue" | "amber" | "red"> = {
  read: "slate", recommend: "blue", action: "amber", privileged: "red",
};

function roles(list: string[]) {
  return list.map((r) => ROLE_LABEL[r as Role] ?? r).join(", ");
}

export function SafetyPage() {
  const q = useSafety();
  return (
    <div className="mx-auto max-w-6xl" data-testid="safety">
      <PageHeader
        title="Safety tiers"
        subtitle="Every function is registered with a risk tier and a required signer; the tool gateway enforces it, not the UI (spec 8.3). Built from the agent and tool registries and the latest eval runs."
        actions={<Link to="/roadmap" className="text-sm font-medium text-brand-700 hover:underline">Roadmap →</Link>}
      />
      {q.isLoading ? <Loading /> : q.error ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : q.data && <SafetyView data={q.data} />}
    </div>
  );
}

function SafetyView({ data }: { data: Safety }) {
  const p = data.platform;
  return (
    <div className="space-y-5">
      <section className="grid gap-3 md:grid-cols-[2fr_1fr_1fr_1fr]" data-testid="safety-positioning">
        <div className="space-y-1.5 rounded-xl border border-brand-100 bg-brand-50/50 px-4 py-3">
          {data.positioning.map((s) => (
            <p key={s} className="flex gap-2 text-sm font-medium text-slate-900"><Quote className="mt-0.5 size-4 shrink-0 text-brand-500" />{s}</p>
          ))}
        </div>
        <Stat label="Registered agents and modules" value={p.entries}
          hint={`${p.by_kind.runtime_agent} runtime agents · ${p.by_kind.embedded_agent} embedded · ${p.by_kind.module} modules`} />
        <Stat label="Registered tools" value={p.tools}
          hint={`${p.by_level.read} read · ${p.by_level.recommend} recommend · ${p.by_level.action} action · ${p.by_level.privileged} privileged`} />
        <Stat label="Eval runs committed" value={p.runs.runs} hint={`${p.runs.live_runs} on a real model`}
          tone={p.runs.live_runs ? undefined : "amber"} />
      </section>

      <Card title="Three risk tiers" padded={false}>
        <div className="grid divide-y divide-slate-100 lg:grid-cols-3 lg:divide-x lg:divide-y-0">
          {data.tiers.map((t) => <TierColumn key={t.id} tier={t} />)}
        </div>
      </Card>

      <Card title="Second axis: tools by side effect" padded={false}>
        <p className="px-4 pt-3 text-sm text-slate-600">
          Modules are tiered by risk, tools by side effect. The axes are independent: an <code>ops</code> module can call a
          privileged tool (a bed move, an OR booking), and that call still needs a recorded human approval.
        </p>
        <div className="overflow-x-auto p-4">
          <table className="w-full text-left text-sm" data-testid="safety-levels">
            <thead className="text-xs text-slate-500">
              <tr><th className="py-1.5 pr-3">Level</th><th className="pr-3">Meaning</th><th className="pr-3">Runs only when</th><th>Registered tools</th></tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {data.side_effect_levels.map((l) => <LevelRow key={l.id} level={l} />)}
            </tbody>
          </table>
        </div>
        <ReachMatrix data={data} />
      </Card>

      <Card title="The retired Level 0–4 scale on the two axes">
        <table className="w-full text-left text-sm" data-testid="safety-mapping">
          <thead className="text-xs text-slate-500"><tr><th className="py-1.5 pr-3">Level</th><th className="pr-3">Module tier</th><th className="pr-3">Tool level</th><th /></tr></thead>
          <tbody className="divide-y divide-slate-100">
            {data.level_mapping.map((m) => (
              <tr key={m.level}>
                <td className="py-1.5 pr-3 font-medium text-slate-900">Level {m.level}</td>
                <td className="pr-3">{m.tiers.map((t) => <Badge key={t} tone={TIER_TONE[t]} className="mr-1">{t}</Badge>)}</td>
                <td className="pr-3">{m.tools.map((l) => <Badge key={l} tone={LEVEL_TONE[l]} className="mr-1">{l}</Badge>)}</td>
                <td className="text-xs text-slate-500">{m.note}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      <Card title="Guardrails" padded={false}>
        <ul className="divide-y divide-slate-100" data-testid="safety-guardrails">
          {data.guardrails.map((g) => <GuardrailRow key={g.id} g={g} />)}
        </ul>
      </Card>

      <Card title={<span className="flex items-center gap-2"><ShieldAlert className="size-4 text-amber-500" />Honest boundaries</span>}>
        <ul className="space-y-2 text-sm text-slate-800" data-testid="safety-boundaries">
          {data.honest_boundaries.map((b) => (
            <li key={b.id} className="flex gap-2" data-testid={`boundary-${b.id}`}>
              <span className="mt-1.5 size-1.5 shrink-0 rounded-full bg-amber-400" />
              <span>{b.text}{b.facts && <RunFactsNote facts={b.facts} />}</span>
            </li>
          ))}
        </ul>
        <p className="mt-4 border-t border-slate-100 pt-3 text-xs text-slate-500">{data.definition_of_done}</p>
      </Card>
    </div>
  );
}

function TierColumn({ tier }: { tier: TierView }) {
  return (
    <div className="space-y-3 p-4 text-sm" data-testid={`tier-${tier.id}`}>
      <div className="flex items-center gap-2">
        <Layers className="size-4 text-slate-400" />
        <h3 className="font-semibold text-slate-900">{tier.name}</h3>
        <Badge tone={TIER_TONE[tier.id]}>{tier.id}</Badge>
      </div>
      <p className="text-slate-700">{tier.definition}</p>
      <p className="text-xs text-slate-500">Examples: {tier.spec_examples}</p>
      <div>
        <p className="text-xs font-medium text-slate-500">AI may</p>
        <div className="mt-1 flex flex-wrap gap-1">
          {tier.allowed_ai_actions.map((a) => <Badge key={a} tone="green"><CheckCircle2 className="size-3" />{a}</Badge>)}
          {tier.never.map((a) => <Badge key={a} tone="red"><XCircle className="size-3" />never {a}</Badge>)}
        </div>
      </div>
      <div>
        <p className="text-xs font-medium text-slate-500">Signs or approves (registry)</p>
        <p className="text-slate-800" data-testid={`tier-signoff-${tier.id}`}>{roles(tier.signoff_roles)}</p>
      </div>
      <div>
        <p className="text-xs font-medium text-slate-500">Regulation and validation</p>
        <p className="text-slate-700">{tier.regulatory}</p>
      </div>
      <details className="rounded-lg border border-slate-200 p-2" data-testid={`tier-entries-${tier.id}`}>
        <summary className="cursor-pointer text-xs font-medium text-slate-700">
          Registered here: {tier.entries.length} ({tier.counts.runtime_agent} runtime agents, {tier.counts.embedded_agent} embedded, {tier.counts.module} modules)
        </summary>
        <ul className="mt-2 space-y-1.5 text-xs">
          {tier.entries.map((e) => (
            <li key={e.agent_id}>
              <code className="font-mono text-slate-900">{e.agent_id}</code> <span className="text-slate-400">{KIND_LABEL[e.kind]}</span>
              <span className="block text-slate-500">{e.purpose}</span>
              {e.required_signoff_role.length > 0 && <span className="block text-slate-500">Signer: {roles(e.required_signoff_role)}{e.cosign ? " (all co-sign)" : ""}</span>}
            </li>
          ))}
        </ul>
      </details>
    </div>
  );
}

function LevelRow({ level }: { level: LevelView }) {
  return (
    <tr className="align-top" data-testid={`level-${level.id}`}>
      <td className="py-2 pr-3"><Badge tone={LEVEL_TONE[level.id]}>{level.id}</Badge></td>
      <td className="pr-3 text-slate-800">{level.meaning}</td>
      <td className="pr-3 text-slate-700">{level.precondition}</td>
      <td>
        <details>
          <summary className="cursor-pointer text-xs font-medium text-slate-700">{level.tools.length} tools</summary>
          <ul className="mt-1 space-y-0.5 text-xs text-slate-600">
            {level.tools.map((t) => (
              <li key={t.tool_id} title={t.description}>
                <code className="font-mono text-slate-900">{t.tool_id}</code>
                {level.id === "privileged" && <span className="text-slate-500"> · approved by {roles(t.approver_roles)} · {t.max_attempts} attempt</span>}
              </li>
            ))}
          </ul>
        </details>
      </td>
    </tr>
  );
}

function ReachMatrix({ data }: { data: Safety }) {
  const levels = data.side_effect_levels.map((l) => l.id);
  return (
    <div className="overflow-x-auto border-t border-slate-100 p-4">
      <p className="mb-2 text-xs font-medium text-slate-500">Registered entries per tier that hold at least one tool of each level</p>
      <table className="text-sm" data-testid="safety-reach">
        <thead className="text-xs text-slate-500">
          <tr><th className="py-1 pr-4 text-left">Tier</th>{levels.map((l) => <th key={l} className="px-3">{l}</th>)}</tr>
        </thead>
        <tbody>
          {data.tiers.map((t) => (
            <tr key={t.id}>
              <td className="py-1 pr-4"><Badge tone={TIER_TONE[t.id]}>{t.id}</Badge></td>
              {levels.map((l) => (
                <td key={l} className="tabular px-3 text-center text-slate-800" title={t.reach[l].join(", ")}>{t.reach[l].length}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function GuardrailRow({ g }: { g: Guardrail }) {
  return (
    <li className="grid gap-2 p-4 text-sm md:grid-cols-[1fr_1fr]" data-testid={`guardrail-${g.id}`}>
      <div>
        <p className="font-medium text-slate-900">{g.title} <span className="text-xs font-normal text-slate-400">spec {g.spec}</span></p>
        <p className="mt-0.5 text-slate-700">{g.rule}</p>
        <p className="mt-1 font-mono text-[11px] text-slate-500">{g.code.join(" · ")}</p>
      </div>
      <div className="text-xs text-slate-600">
        <p>Evidence: {g.evidence}</p>
        {g.live && (
          <p className="mt-1 flex flex-wrap items-center gap-1.5" data-testid={`guardrail-live-${g.id}`}>
            <span className="text-slate-500">Latest run {g.live.run_id} ({g.live.mode === "mock" ? "mock baseline" : g.live.mode}):</span>
            {g.live.gates && Object.entries(g.live.gates).map(([name, gate]) => (
              <Badge key={name} tone={gate.passed ? "green" : "red"}>
                {typeof gate.value === "boolean" ? `${name} ${gate.passed ? "met" : "not met"}` : `${name} ${gate.value} (bar ${gate.threshold})`}
              </Badge>
            ))}
            {g.live.cases !== undefined && (
              <Badge tone={g.live.passed ? "green" : "red"}>{g.live.cases_passed}/{g.live.cases} cases · {g.live.unauthorised_tool_calls} attack calls executed</Badge>
            )}
          </p>
        )}
      </div>
    </li>
  );
}

function RunFactsNote({ facts }: { facts: RunFacts }) {
  return (
    <span className="ml-1 text-xs text-slate-500" data-testid="boundary-run-facts">
      ({facts.runs} runs committed, {facts.live_runs} on a real model{facts.latest ? `; latest ${facts.latest}` : ""}; see evals/README.md)
    </span>
  );
}
