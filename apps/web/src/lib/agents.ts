// The agent registry (spec 6.4): which AI agents exist, their risk tier and whether they passed an evaluation.
// In demo mode an agent that has not passed runs, and its output carries a "Not evaluated" flag.

import { useQuery } from "@tanstack/react-query";
import { api } from "./api";

export type RiskTier = "ops" | "documentation" | "clinical_ds";
export type SideEffectLevel = "read" | "recommend" | "action" | "privileged";

export interface AgentView {
  agent_id: string;
  version: string;
  kind: "runtime_agent" | "embedded_agent" | "module";
  owner: string;
  purpose: string;
  risk_tier: RiskTier;
  allowed_tools: string[];
  data_scope: { resources: string[]; scope: string };
  model_policy: { tier: string; max_tokens: number; temperature_max: number | null } | null;
  prompt_version: string | null;
  required_signoff_role: string[];
  cosign: boolean;
  consent_required: string[];
  eval_status: { status: "pending" | "passed" | "failed" | "not_applicable"; run_id: string | null; note: string };
  deployment_status: "dev" | "demo" | "prod_ready";
  entrypoint: string | null;
  evaluated: boolean;
  not_evaluated_flag: boolean;
  refused_reason: string | null;
}

export interface AgentRegistry {
  mode: "demo" | "prod";
  agents: AgentView[];
}

export interface ToolView {
  tool_id: string;
  description: string;
  side_effect_level: SideEffectLevel;
  domain: string;
  reads: string[];
  fhir_writes: Record<string, string[]>;
  permission_policy: { roles: string[]; approver_roles: string[] | "signoff"; target: Record<string, string> };
  idempotency: { required: boolean; window_hours: number };
  timeout_ms: number;
  retry: { max_attempts: number; backoff_ms: number };
  audit_level: string;
  internal: boolean;
}

export function useAgentRegistry() {
  return useQuery({ queryKey: ["agents"], queryFn: () => api<AgentRegistry>("/api/agents"), staleTime: 5 * 60_000 });
}

/** The registry entry of one agent (undefined while loading or when unknown). */
export function useAgent(agentId: string | undefined): AgentView | undefined {
  const registry = useAgentRegistry();
  return agentId ? registry.data?.agents.find((a) => a.agent_id === agentId) : undefined;
}

export const TIER_LABEL: Record<RiskTier, string> = { ops: "Operations", documentation: "Documentation", clinical_ds: "Clinical decision support" };

export const LEVEL_TONE: Record<SideEffectLevel, "slate" | "blue" | "amber" | "red"> = {
  read: "slate",
  recommend: "blue",
  action: "amber",
  privileged: "red",
};
