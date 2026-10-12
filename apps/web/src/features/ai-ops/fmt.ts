// Formatting for the AI Ops page. Values come from the API; these only render them.

export const NONE = "–";

export function rate(v: number | null | undefined, digits = 1): string {
  return v == null ? NONE : `${(v * 100).toFixed(digits)}%`;
}

export function usd(v: number | null | undefined, estimated = false): string {
  if (v == null) return NONE;
  return `${estimated ? "≈" : ""}$${v.toFixed(4)}`;
}

export function ms(v: number | null | undefined): string {
  if (v == null) return NONE;
  return v >= 1000 ? `${(v / 1000).toFixed(2)} s` : `${v} ms`;
}

export function count(v: number | null | undefined): string {
  return v == null ? NONE : v.toLocaleString();
}

export function metric(key: string, v: number | null | undefined, estimated = false): string {
  if (key.endsWith("_ms")) return ms(v);
  if (key.includes("cost") || key.endsWith("_usd")) return usd(v, estimated);
  if (key.includes("rate") || key === "schema_validity" || key === "task_success" || key === "pass_rate") return rate(v);
  return count(v);
}

export function short(value: unknown): string {
  if (value == null) return NONE;
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

export const METRIC_LABEL: Record<string, string> = {
  schema_validity: "Schema validity",
  grounding_rate: "Grounding",
  task_success: "Task success",
  human_override_rate: "Human override",
  latency_p50_ms: "Latency p50",
  latency_p95_ms: "Latency p95",
  cost_per_case_usd: "Cost per case",
};

export const STATUS_TONE: Record<string, "green" | "amber" | "red" | "slate" | "blue"> = {
  passed: "green",
  met: "green",
  completed: "green",
  ok: "green",
  replayed: "blue",
  pending: "amber",
  gates_only: "amber",
  below: "red",
  failed: "red",
  error: "red",
  refused: "red",
  needs_human: "amber",
  unavailable: "red",
  fallback: "amber",
  approval_required: "blue",
  no_eval_set: "slate",
};
