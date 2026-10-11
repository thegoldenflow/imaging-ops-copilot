// The architecture pages' API (spec 8.1, 8.3, WP10): GET /api/roadmap (one card per 8.1 row with its registry entry
// and storyline step) and GET /api/roadmap/safety (the tiers, the tool side-effect levels and the guardrails, built
// from the agent and tool registries and the latest eval runs).

import { useQuery } from "@tanstack/react-query";
import { api } from "../../lib/api";
import type { Role } from "../../lib/types";

export type RiskTier = "ops" | "documentation" | "clinical_ds";
export type SideEffectLevel = "read" | "recommend" | "action" | "privileged";

export interface RegistryEntry {
  agent_id: string;
  version: string;
  kind: "runtime_agent" | "embedded_agent" | "module";
  owner: string;
  purpose: string;
  risk_tier: RiskTier;
  allowed_tools: string[];
  required_signoff_role: Role[];
  cosign: boolean;
  consent_required: string[];
  deployment_status: string;
  eval_status: string;
  path: string;
}

export interface StepLink {
  n: number;
  key: string;
  title: string;
  stub: boolean;
  href: string;
}

export interface RoadmapCard {
  id: string;
  title: string;
  pain: string;
  data_source: string;
  risk_tier: RiskTier;
  reuses: string;
  reuse_refs: string[];
  demo: "card" | "preset";
  demo_note: string;
  registry: string | null;
  storyline: string | null;
  spec_ref: string | null;
  registry_entry: RegistryEntry | null;
  step: StepLink | null;
  footer: string;
}

export interface Roadmap {
  footer: string;
  cards: RoadmapCard[];
  counts: { cards: number; preset: number; by_tier: Record<RiskTier, number> };
}

export interface TierView {
  id: RiskTier;
  name: string;
  definition: string;
  spec_examples: string;
  allowed_ai_actions: string[];
  never: string[];
  spec_signoff: string;
  regulatory: string;
  entries: RegistryEntry[];
  signoff_roles: Role[];
  counts: Record<RegistryEntry["kind"], number>;
  reach: Record<SideEffectLevel, string[]>;
}

export interface ToolView {
  tool_id: string;
  description: string;
  internal: boolean;
  idempotent: boolean;
  max_attempts: number;
  audit_level: string;
  approver_roles: string[];
}

export interface LevelView {
  id: SideEffectLevel;
  meaning: string;
  precondition: string;
  tools: ToolView[];
}

export interface Gate {
  value: number | string | boolean;
  threshold: number | string | boolean;
  passed: boolean;
}

export interface GuardrailLive {
  run_id: string;
  mode: string | null;
  started_at: string | null;
  gates?: Record<string, Gate>;
  passed?: boolean;
  cases?: number;
  cases_passed?: number;
  unauthorised_tool_calls?: number;
}

export interface Guardrail {
  id: string;
  title: string;
  rule: string;
  spec: string;
  code: string[];
  evidence: string;
  live: GuardrailLive | null;
}

export interface RunFacts {
  runs: number;
  live_runs: number;
  latest: string | null;
  latest_live: string | null;
}

export interface Safety {
  tiers: TierView[];
  side_effect_levels: LevelView[];
  level_mapping: { level: string; tiers: RiskTier[]; tools: SideEffectLevel[]; note: string | null }[];
  guardrails: Guardrail[];
  honest_boundaries: { id: string; text: string; facts: RunFacts | null }[];
  positioning: string[];
  definition_of_done: string;
  platform: {
    entries: number;
    by_kind: Record<RegistryEntry["kind"], number>;
    tools: number;
    by_level: Record<SideEffectLevel, number>;
    runs: RunFacts;
  };
}

export const TIER_LABEL: Record<RiskTier, string> = {
  ops: "ops",
  documentation: "documentation",
  clinical_ds: "clinical_ds",
};

export const TIER_TONE: Record<RiskTier, "slate" | "blue" | "amber"> = {
  ops: "slate",
  documentation: "blue",
  clinical_ds: "amber",
};

export const KIND_LABEL: Record<RegistryEntry["kind"], string> = {
  runtime_agent: "runtime agent",
  embedded_agent: "embedded agent",
  module: "module (no model)",
};

export function useRoadmap() {
  return useQuery({ queryKey: ["roadmap"], queryFn: () => api<Roadmap>("/api/roadmap"), staleTime: 60_000 });
}

export function useSafety() {
  return useQuery({ queryKey: ["roadmap", "safety"], queryFn: () => api<Safety>("/api/roadmap/safety"), staleTime: 60_000 });
}
