// AI Ops API shapes (/api/aiops/..., spec 6.6). Every number the page shows arrives in these.

export interface Card {
  key: string;
  label: string;
  value: number | null;
  format: "int" | "usd" | "ms" | "pct";
  estimated?: boolean;
  detail: Record<string, number | null>;
}

export interface Totals {
  requests: number;
  model_calls: number;
  tokens: number;
  cost_usd: number;
  cost_estimated: boolean;
  latency_p50_ms: number | null;
  latency_p95_ms: number | null;
  timeouts: number;
  schema_failures: number;
  tool_failures: number;
  tool_refusals: number;
  retrieval_failures: number;
  fallbacks: number;
  retries: number;
  failed_runs: number;
  failure_rate: number | null;
  schema_validity: number | null;
  decided: number;
  adoption_rate: number | null;
  human_override_rate: number | null;
  escalation_rate: number | null;
  tasks_written: number;
  task_completion_rate: number | null;
  completed_workflows?: number;
  cost_per_completed_workflow_usd?: number | null;
}

export type DimensionRow = Totals & Record<string, unknown>;

export interface AgentRow {
  agent_id: string;
  version: string;
  kind: string;
  risk_tier: string;
  deployment_status: string;
  prompt_version: string | null;
  eval_status: string;
  eval_source: string;
  eval_note: string;
  eval_run: string | null;
  threshold: number;
  pass_rate: number | null;
  eval_cases: number | null;
  override_rate: number | null;
  decided: number;
  latency_p95_ms: number | null;
  cost_usd: number | null;
  cost_estimated: boolean;
  requests: number;
  failed_runs: number;
}

export interface Failure {
  run_id: string;
  agent_id: string;
  started_at: string;
  outcome: string;
  failures: string[];
  model: string;
  unit: string;
  encounter_id: string | null;
  prompt_version: string | null;
}

export interface AlertTask {
  id: string;
  status: string;
  priority: string;
  description: string;
  authored: string;
  alert_id: string;
  scope: string;
  day: string;
  metric?: string;
  value?: string;
  threshold?: string;
  notes: string[];
}

export interface BudgetStatus {
  limit_usd: number | null;
  spent_usd: number;
  remaining_usd: number | null;
  reached: boolean;
  day: string;
  resets_at: string;
  refused_today: number | null;
}

export interface IdleStatus {
  pause_minutes: number;
  paused: boolean;
  paused_since: string | null;
  last_activity: string | null;
  last_pause: { from: string | null; to: string } | null;
}

export interface Overview {
  window: { days: number; from: string; to: string };
  totals: Totals;
  cards: Card[];
  agents: AgentRow[];
  by_agent: DimensionRow[];
  by_model: DimensionRow[];
  by_unit: DimensionRow[];
  by_day: DimensionRow[];
  failures: Failure[];
  failure_count: number;
  mode: string;
  alerts: { open: AlertTask[] };
  budget: BudgetStatus;
  idle: IdleStatus;
  background_ai: "on_demand" | "eager";
}

export interface ToolCall {
  tool_id: string;
  side_effect_level: string;
  args_hash: string;
  status: string;
  reason: string | null;
  result_ref: string[];
  latency_ms: number;
  attempts: number;
}

export interface LlmCall {
  call_id: string;
  agent_id: string;
  model: string;
  prompt_version: string;
  status: string;
  tokens_in: number;
  tokens_out: number;
  cost_usd: number;
  latency_ms: number;
  error?: string | null;
}

export interface Trace {
  run_id: string;
  agent_id: string;
  agent_version: string;
  kind: string;
  mode: string | null;
  model: string | null;
  prompt_version: string | null;
  eval_status: string;
  actor_id: string | null;
  actor_role: string | null;
  encounter_id: string | null;
  context_id: string | null;
  input_refs: string[];
  output_refs: string[];
  llm_calls: LlmCall[];
  tool_calls: ToolCall[];
  confidence: number | null;
  human_action: Record<string, string> | null;
  outcome: string;
  tokens_in: number;
  tokens_out: number;
  cost_usd: number;
  provenance_id: string | null;
  started_at: string;
  finished_at: string | null;
}

export interface TraceDetail {
  trace: Trace;
  record: { failures: string[]; cost_usd: number; estimated: boolean; unit: string; latency_ms: number | null };
  prompt_header: Record<string, string> | null;
  model_entry: string | null;
  duration_ms: number | null;
}

export type SixKey = "schema_validity" | "grounding_rate" | "task_success" | "human_override_rate" | "latency_p50_ms" |
  "latency_p95_ms" | "cost_per_case_usd";

export interface Gate {
  value: unknown;
  threshold: unknown;
  passed: boolean;
  reason?: string;
}

export interface EvalAgent {
  agent_id: string;
  version: string;
  prompt_version: string | null;
  suite: string | null;
  suite_title: string | null;
  suite_kind: string | null;
  source: string | null;
  cases_by_source: Record<string, number>;
  threshold: number;
  eval_status: string;
  eval_note: string;
  latest_run: string | null;
  latest_mode: string | null;
  metrics: Record<SixKey, number | null> | null;
  cost_estimated: boolean | null;
  status: string | null;
  gates: Record<string, Gate> | null;
  extra: Record<string, number> | null;
  pipeline?: Pipeline | null;
}

export interface PlatformSuite {
  id: string;
  title: string;
  source: string;
  passed: boolean | null;
  run_at: string | null;
  summary: Record<string, string | number | boolean>;
}

/** The whole case across every agent a suite runs, when it also calls agents that are not its own (their calls are
 * in no agent's metrics). */
export interface Pipeline {
  other_agents: Record<string, { model_calls: number; failed_calls: number; cost_usd: number; tokens_in: number;
    tokens_out: number; cases: number }>;
  model_calls: number;
  cases: number;
  cost_per_case_usd: number | null;
  cost_estimated: boolean;
  latency_p50_ms: number | null;
  latency_p95_ms: number | null;
}

export interface EvalCenter {
  agents: EvalAgent[];
  platform: Record<string, PlatformSuite | null>;
  command: string;
}

export interface RunRow {
  run_id: string;
  started_at: string;
  mode: string;
  variant: Record<string, string> | null;
  agents: number;
  met: number;
  below: number;
  errors: number;
  no_eval_set: number;
}

export interface CaseResult {
  id: string;
  source: string;
  passed: boolean;
  schema_valid: boolean | null;
  grounded: boolean | null;
  latency_ms: number | null;
  cost_usd: number | null;
  pipeline_cost_usd?: number | null;
  pipeline_latency_ms?: number | null;
  expected: unknown;
  got: unknown;
  run_ids: string[];
  related_run_ids?: string[];
  call_errors?: { outcome: string; error: string | null }[];
  has_trace?: boolean;
  traces?: Trace[];
  traces_dropped?: number;
}

export interface RunAgent {
  run_id: string;
  started_at: string;
  mode: string;
  variant: Record<string, string> | null;
  agent_id: string;
  suite: string;
  suite_title: string;
  prompt_version: string | null;
  model: string | null;
  threshold: number;
  status: string;
  metrics: Record<string, number | boolean | null>;
  gates: Record<string, Gate>;
  extra: Record<string, number>;
  notes: string;
  case_results: CaseResult[];
}

export interface CompareSide {
  run_id: string;
  started_at: string;
  mode: string;
  variant: Record<string, string> | null;
  prompt_version: string | null;
  model: string | null;
  model_key: string | null;
  metrics: Record<SixKey, number | null>;
  cost_estimated: boolean;
  cases: number;
  cases_passed: number;
  status: string;
  threshold: number;
  gates: Record<string, Gate>;
}

export interface Difference {
  id: string;
  a: { passed: boolean; got: unknown; expected: unknown } | null;
  b: { passed: boolean; got: unknown; expected: unknown } | null;
  change: "fixed" | "regressed" | "changed";
}

export interface Comparison {
  agent_id: string;
  a: CompareSide;
  b: CompareSide;
  differences: Difference[];
  same_cases: number;
  metrics_order: SixKey[];
}

export interface CompareOption {
  run_id: string;
  started_at: string;
  mode: string;
  prompt_version: string | null;
  model: string | null;
  variant: Record<string, string> | null;
  task_success: number | null;
}
