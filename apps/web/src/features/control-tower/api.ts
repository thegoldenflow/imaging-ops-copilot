// Types and requests of the Control Tower API (spec 7.1, apps/api/app/modules/control_tower/router.py).
// Times are hospital-clock local times without a zone ("2026-10-09T07:42:00").

import { useQuery } from "@tanstack/react-query";
import { api, post } from "../../lib/api";

export type Severity = "low" | "med" | "high";
export type OwnerRole = "physician" | "nurse" | "operations_manager";

export interface Explained {
  value: number;
  sentence: string;
}

export interface EdRow {
  encounter_id: string;
  mrn: string | null;
  identified: boolean;
  location: string | null;
  status: string;
  arrival: string;
  ctas: number | null;
  minutes_in_ed: number;
  waiting_to_be_seen: boolean;
  wait_minutes: number | null;
  admit: Explained | null;
  orders_total: number;
  orders_open: number;
  bed_requested_at: string | null;
  boarding_minutes: number | null;
  target_unit: string | null;
  target_unit_source: "bed request" | "likely" | null;
  lwbs_risk: boolean;
}

export interface UnitRow {
  id: string;
  name: string;
  kind: string;
  beds: number;
  occupied: number;
  free: number;
  cleaning: number;
  closed: number;
  occupancy: number;
  alc: number;
  expected_discharges: number;
  discharge_ready: number;
  ed_boarders: number;
  expected_admissions: number;
  electives_24h: number;
  net_gap: number;
}

export interface OrCase {
  appointment_id: string;
  room: string;
  procedure: string | null;
  surgeon: string | null;
  mrn: string | null;
  identified: boolean;
  encounter_id: string | null;
  status: "booked" | "arrived" | "fulfilled" | "cancelled" | string;
  urgency: string | null;
  asa: number | null;
  booked_start: string;
  booked_end: string;
  booked_minutes: number;
  actual_start: string | null;
  actual_end: string | null;
  predicted: Explained | null;
  predicted_minutes: number | null;
  predicted_start: string | null;
  predicted_end: string | null;
  overrun_minutes: number | null;
  overrun_risk: Severity | null;
  preop: Record<string, "done" | "open">;
  preop_open: string[];
  needs_bed: boolean;
  post_op_unit: string | null;
  cancellation_risk: boolean;
  cancellation_reasons: string[];
}

export interface OrRoom {
  id: string;
  block: boolean;
  surgeon_id: string | null;
  booked_minutes: number;
  predicted_minutes: number;
  utilization: number | null;
  predicted_end: string | null;
}

export interface Kpis {
  ed_census: number;
  ed_waiting: number;
  ed_boarders: number;
  ed_boarders_over_2h: number;
  ed_predicted_wait: Explained | null;
  lwbs_risk: number;
  occupancy: number;
  occupied: number;
  beds: number;
  free: number;
  cleaning: number;
  expected_discharges: number;
  expected_admissions: number;
  net_gap: number;
  or_cases: number;
  or_done: number;
  or_in_progress: number;
  or_utilization: number | null;
  or_booked_utilization: number | null;
  or_cancellation_risk: number;
  or_predicted_end: string | null;
}

export type ExceptionStatus = "open" | "deferred" | "approved" | "rejected" | "cleared";

export interface Decision {
  decision: "approved" | "rejected" | "deferred";
  by: string;
  name: string;
  role: string;
  at: string;
  note?: string;
  remind_at?: string;
  actions?: { action_id: string; label: string; owner_role: OwnerRole; task_id: string | null; status: string; detail: string | null }[];
  execution_run?: string | null;
  executed_by?: "workflow" | "request";
  workflow_id?: string | null;
}

/** A durable workflow linked from the Control Tower (spec 6.5): the encounter's journey, the exception's loop. */
export interface WorkflowLink {
  id: string;
  status: string;
  current_step: string | null;
  current_label: string | null;
  current_status: string | null;
  done: number;
  total: number;
}

export interface ExceptionItem {
  id: string;
  key: string;
  rule: "unit_occupancy" | "ed_boarding" | "or_overrun" | "preop_gap";
  severity: Severity;
  status: ExceptionStatus;
  title: string;
  summary: string;
  unit_id: string;
  subject_ref: string;
  detected_at: string;
  changed_at: string;
  cleared_at: string | null;
  remind_at: string | null;
  reminders: number;
  narration_status: "pending" | "done";
  decision: Decision | null;
  review_task_id: string | null;
  narrative: string | null;
  outcome?: { decision: string; resolved: boolean | null; verified: { occupancy_before_pct: number | null; occupancy_after_pct: number | null; condition_present: boolean } | null } | null;
}

export interface MenuItem {
  action_id: string;
  label: string;
  owner_role: OwnerRole;
  why: string;
  effect: string;
  encounter_id: string | null;
}

export interface RecommendedAction {
  action_id: string;
  action: string;
  rationale: string;
  owner_role: OwnerRole;
  expected_effect: string;
}

export interface ExceptionDetail extends ExceptionItem {
  workflow?: WorkflowLink | null;
  facts: Record<string, unknown>;
  menu: MenuItem[];
  engine_evidence: string[];
  narration: {
    narrative: string;
    recommended_actions: RecommendedAction[];
    evidence_refs: string[];
    unresolved_refs: string[];
    source: "model" | "template";
    ai_status: string;
    attempts: number;
    problems: string[];
    run_id: string | null;
    evaluated: boolean;
    narrated_at: string;
    facts_as_of: string;
    severity: Severity;
  } | null;
  can_decide: boolean;
}

export interface SimJob {
  id: string;
  status: "running" | "done" | "failed";
  start: string;
  target: string;
  now: string;
  progress: number;
  events: number;
  error: string | null;
}

export interface Board {
  now: string;
  built_at: string;
  build_ms: number;
  models: Record<string, boolean>;
  role: string;
  can_decide: boolean;
  can_control: boolean;
  kpis: Kpis;
  ed: EdRow[];
  units: UnitRow[];
  or_cases: OrCase[];
  or_rooms: OrRoom[];
  exceptions: ExceptionItem[];
  clock: { now: string; rate: number; running: boolean; day_start: string; horizon: string; stopped: string | null };
  job: SimJob | null;
  scenarios: Record<string, string>;
  events: { seq: number; type: string; at: string; encounter: string | null; attrs: Record<string, unknown> }[];
}

export interface BedCell {
  id: string;
  room: string;
  status: "O" | "U" | "K" | "C";
  encounter_id: string | null;
  mrn: string | null;
  identified: boolean;
  since: string | null;
  admitted_at: string | null;
  expected_discharge: string | null;
  days_in: number | null;
  alc: boolean;
  fall_risk: boolean | null;
  discharge: Explained | null;
}

export interface UnitDetail {
  unit: UnitRow;
  beds: BedCell[];
  now: string;
}

export interface PatientCard {
  encounter_id: string;
  class: string | null;
  status: string;
  mrn: string | null;
  bed: string | null;
  admitted_at: string | null;
  expected_discharge: string | null;
  role: string;
  hidden: string[];
  name?: string | null;
  gender?: string | null;
  age?: number | null;
  reason?: string | null;
  attending?: string | null;
  flags?: string[];
  vitals?: Record<string, number | null> | null;
  vitals_at?: string | null;
  open_orders?: number;
  discharge?: Explained | null;
  journey?: WorkflowLink | null;
}

export function useBoard(fast: boolean) {
  return useQuery({
    queryKey: ["ct-board"],
    queryFn: () => api<Board>("/api/control-tower/board"),
    refetchInterval: fast ? 1000 : 3000,
    refetchIntervalInBackground: false,
  });
}

export const useException = (id: string | null) =>
  useQuery({
    queryKey: ["ct-exception", id],
    queryFn: () => api<ExceptionDetail>(`/api/control-tower/exceptions/${id}`),
    enabled: Boolean(id),
    // while the exception's workflow executes the approved actions, follow it until the Tasks exist
    refetchInterval: (query) =>
      query.state.data?.decision?.actions?.some((a) => a.status === "queued" && !a.task_id) ? 1000 : false,
  });

export const useUnit = (id: string | null, fast: boolean) =>
  useQuery({
    queryKey: ["ct-unit", id],
    queryFn: () => api<UnitDetail>(`/api/control-tower/units/${id}`),
    enabled: Boolean(id),
    refetchInterval: fast ? 2000 : false,
  });

export const usePatientCard = (encounterId: string | null) =>
  useQuery({
    queryKey: ["ct-patient", encounterId],
    queryFn: () => api<PatientCard>(`/api/control-tower/patients/${encounterId}`),
    enabled: Boolean(encounterId),
    retry: false,
  });

export const approveException = (id: string, action_ids: string[], note: string) =>
  post<ExceptionDetail>(`/api/control-tower/exceptions/${id}/approve`, { action_ids, note });
export const rejectException = (id: string, reason: string) =>
  post<ExceptionDetail>(`/api/control-tower/exceptions/${id}/reject`, { reason });
export const deferException = (id: string, minutes: number) =>
  post<ExceptionDetail>(`/api/control-tower/exceptions/${id}/defer`, { minutes });

// --- the simulator (WP3 API) ---
export const simRun = (rate: number) => post("/api/hospital/simulator/run", { rate });
export const simPause = () => post("/api/hospital/simulator/pause");
export const simAdvance = (minutes: number) => post("/api/hospital/simulator/advance", { minutes });
export const simFastForward = () => post<SimJob>("/api/hospital/simulator/fast-forward/start", { hour: 8 });
export const simInject = (scenario: string, count?: number) =>
  post<{ correlation_id: string }>("/api/hospital/simulator/inject", count ? { scenario, count } : { scenario });

// --- formatting (hospital-clock strings: no time-zone conversion) ---
export const hhmm = (iso: string | null | undefined) => (iso ? iso.slice(11, 16) : "–");
export const dayTime = (iso: string) => {
  const d = new Date(iso.slice(0, 19));
  return `${d.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" })} ${iso.slice(11, 16)}`;
};
export const mins = (m: number | null | undefined) =>
  m == null ? "–" : m >= 120 ? `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")}` : `${m} min`;
export const pct = (v: number | null | undefined) => (v == null ? "–" : `${Math.round(v * 100)}%`);

export const ROLE_SHORT: Record<OwnerRole, string> = { physician: "Physician", nurse: "Charge nurse", operations_manager: "Bed manager" };
export const RULE_LABEL: Record<ExceptionItem["rule"], string> = {
  unit_occupancy: "Unit occupancy",
  ed_boarding: "ED boarders",
  or_overrun: "OR overrun",
  preop_gap: "Pre-op gap",
};
export const PREOP_LABEL: Record<string, string> = {
  "preop-consent": "Consent",
  "preop-npo": "Fasting",
  "preop-labs": "Labs",
  "preop-blood-type": "Blood group",
};
