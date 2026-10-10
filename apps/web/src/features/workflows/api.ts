// Types and hooks of the workflow API (spec 6.5): timelines, retry and skip, the demo's deliberate failures,
// the review queue and low-confidence corrections.

import { useQuery } from "@tanstack/react-query";
import { api } from "../../lib/api";

export type StepStatus = "pending" | "running" | "retrying" | "waiting" | "failed" | "done" | "skipped";
export type WorkflowType = "InpatientJourneyWorkflow" | "CapacityExceptionWorkflow" | "LowConfidenceReviewWorkflow";

export const WORKFLOW_LABEL: Record<WorkflowType, string> = {
  InpatientJourneyWorkflow: "Patient journey",
  CapacityExceptionWorkflow: "Capacity exception",
  LowConfidenceReviewWorkflow: "Low-confidence review",
};

export interface TemporalStatus {
  configured: boolean;
  online: boolean;
  workers: number;
  address: string | null;
  namespace: string;
  task_queue: string;
  error: string | null;
  outbox: Record<string, number>;
  can_control?: boolean;
  timers?: Record<string, number>;
  faults?: Record<string, number>;
}

export interface WorkflowSummary {
  id: string;
  workflow_type: WorkflowType;
  status: "running" | "completed" | "failed";
  current_step: string | null;
  current_label: string | null;
  current_status: StepStatus | null;
  encounter_id: string | null;
  unit_id: string | null;
  exception_id: string | null;
  run_id: string | null;
  started_at: string | null;
  updated_at: string | null;
  done: number;
  total: number;
  attention: boolean;
}

export interface Step {
  key: string;
  label: string;
  owner: string;
  kind: "auto" | "signoff" | "privileged" | "wait" | "timer";
  stub: string;
  status: StepStatus;
  started_at: string | null;
  finished_at: string | null;
  attempts: number;
  detail: string | null;
  refs: string[];
  waiting_for: { refs: string[]; role: string } | null;
  escalation: number;
  tasks: { kind: string; task_id: string; role: string; at: string }[];
  duration_s: number | null;
}

export interface WorkflowDetail {
  id: string;
  workflow_type: WorkflowType;
  status: "running" | "completed" | "failed";
  current_step: string | null;
  next_step: string | null;
  encounter_id: string | null;
  unit_id: string | null;
  exception_id: string | null;
  run_id: string | null;
  meta: Record<string, unknown>;
  steps: Step[];
  notes: { at: string; text: string }[];
  started_at: string | null;
  updated_at: string | null;
  finished_at: string | null;
  version: number;
  can_control: boolean;
  temporal: { status?: string | null; history_length?: number; pending_activities?: { activity: string; attempt: number; last_failure: string | null }[]; error?: string } | null;
}

export interface QueueTask {
  task_id: string;
  kind: "approval-request" | "ai-review" | "workflow-signoff";
  status: string;
  description: string | null;
  authored_on: string | null;
  encounter_id: string | null;
  focus: string | null;
  tool_id: string | null;
  agent_id: string | null;
  run_id: string | null;
  recommendation: string | null;
  priority: string | null;
  workflow_id: string | null;
  step: string | null;
  review_id: string | null;
}

export interface AgentReview {
  id: string;
  agent_id: string;
  run_id: string;
  status: "open" | "reviewed" | "dismissed" | "learned";
  task_id: string | null;
  workflow_id: string | null;
  encounter_id: string | null;
  confidence: number;
  threshold: number;
  output: Record<string, unknown>;
  correction: Record<string, string> | null;
  case_id: string | null;
  regression: { cases: number; passed: number; accuracy: number; gate: number; status: string } | null;
  correctable: Record<string, string[]>;
  input_excerpt: string;
  note: string;
}

export function useWorkflowStatus() {
  return useQuery({ queryKey: ["wf-status"], queryFn: () => api<TemporalStatus>("/api/workflows/status"), refetchInterval: 3000 });
}

export function useWorkflows(type: WorkflowType | "all") {
  return useQuery({
    queryKey: ["wf-list", type],
    queryFn: () => api<{ status: TemporalStatus; workflows: WorkflowSummary[]; counts: Record<string, Record<string, number>> }>(
      `/api/workflows${type === "all" ? "" : `?type=${type}`}`,
    ),
    refetchInterval: 2000,
  });
}

export function useWorkflow(id: string) {
  return useQuery({ queryKey: ["wf", id], queryFn: () => api<WorkflowDetail>(`/api/workflows/${encodeURIComponent(id)}`), refetchInterval: 1500 });
}

/** "4 min", "2 h 5 min", "48 h" */
export function duration(seconds: number | null | undefined): string {
  if (seconds == null) return "–";
  if (seconds < 60) return `${seconds} s`;
  const m = Math.round(seconds / 60);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  return m % 60 ? `${h} h ${m % 60} min` : `${h} h`;
}

export const OWNER_LABEL: Record<string, string> = {
  nurse: "Nurse", physician: "Physician", pharmacist: "Pharmacist", clerk: "Clerk", operations_manager: "Bed manager / ops", system: "System",
};
