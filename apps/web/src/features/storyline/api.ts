// The hospital storyline's API (spec 8.2, WP9): GET /api/storyline (the twelve steps with their live trail),
// POST /api/storyline/reset (demo reset + preset), POST /api/storyline/advance (the clock to the step's beat).

import { useQuery } from "@tanstack/react-query";
import type { AiBlock } from "../../lib/ai";
import { api, post } from "../../lib/api";
import type { Role } from "../../lib/types";

export interface CastMember {
  id: string;
  name: string;
  role: Role;
  units: string[];
}

export interface Proposal {
  label: string;
  ref: string;
  at?: string | null;
  agent?: string | null;
  run_id?: string | null;
  summary?: string | null;
  recommendation?: string | null;
  model?: string | null;
  mode?: string | null;
  decision?: string | null;
  generated_at?: string | null; // when the AI wrote it (wall clock) and how long it took
  duration_ms?: number | null;
}

/** What the AI does for a step whose items get AI output when its button is pressed (the exception's narrative; the
 * naproxen order's review and the admission reconciliation). */
export interface StepAi {
  items: { kind: string; id: string; label: string; ai: AiBlock }[];
  text: string | null;
  requested: boolean;
  requested_by: string | null;
}

export interface Signed {
  by?: string | null;
  decision?: string | null;
  note?: string | null;
  at?: string | null;
  ref?: string | null;
  status?: string | null;
  signatures?: number;
  tasks?: string[];
}

export interface AuditEntry {
  event_type: string;
  action: string;
  resource: string;
  user: string;
  role: string;
  at: string;
  outcome: string;
}

export interface StoryStep {
  n: number;
  key: string;
  title: string;
  module: string;
  route: string;
  click_path: string[];
  say: string;
  expect: string[];
  ai: string;
  signoff: string;
  writes: string;
  seconds: number;
  stub: boolean;
  beat: string | null;
  beat_hint: string | null;
  beat_at?: string | null;
  permission: string;
  cast: CastMember | null;
  cast_key: string;
  status: "done" | "active" | "waiting" | "pending";
  done: boolean;
  proposal: Proposal | null;
  signed: Signed | null;
  written: string[];
  audit: AuditEntry[];
  detail: string | null;
  step_ai?: StepAi;
  advance_ai?: string | null; // what pressing this step's Advance makes the AI do
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

export interface Storyline {
  prepared: boolean;
  now: string;
  day_start: string;
  horizon: string;
  running: boolean;
  job: SimJob | null;
  total_seconds: number;
  you: CastMember;
  temporal: { configured: boolean; status: { configured: boolean; online: boolean; workers: number; namespace?: string | null; error?: string | null } };
  journey: { id: string; status: string; current_step: string | null; current_label: string | null; current_status: string | null; done: number; total: number; attention: boolean } | null;
  patient: {
    id: string;
    mrn: string;
    name: string;
    name_zh: string;
    birth_date: string;
    age: number;
    language: string;
    call_lines: string[];
    followup_lines: string[];
    imaging_record: string;
    mpi: { hospital: string; imaging: string; matched_on: string; note: string } | null;
  } | null;
  steps: StoryStep[];
  current: number | null;
  beats: Record<string, string | null>;
  capacity?: { surgery_ready?: string[]; warnings?: string[] };
}

export const storylineKey = ["storyline"];

export function useStoryline(enabled = true) {
  return useQuery({
    queryKey: storylineKey,
    enabled,
    queryFn: () => api<Storyline>("/api/storyline"),
    refetchInterval: (q) => {
      const d = q.state.data;
      const aiBusy = d?.steps.some((s) => s.step_ai?.items.some((i) => i.ai.state === "running" || i.ai.state === "queued"));
      return d?.job?.status === "running" || aiBusy ? 1000 : 3000;
    },
  });
}

export const resetStoryline = () => post<{ ok: boolean; prepared_at: string }>("/api/storyline/reset");
export const advanceStoryline = () => post<{ job: SimJob; beat: string; target: string }>("/api/storyline/advance");
export const askStepAi = (key: string) => post<{ step: number; items: string[][] }>(`/api/storyline/steps/${key}/ai`);

/** Hospital-clock strings carry their own offset: show them as written (no time-zone conversion). */
export const clock = (iso: string | null | undefined) => (iso ? `${iso.slice(0, 10)} ${iso.slice(11, 16)}` : "–");
export const hhmm = (iso: string | null | undefined) => (iso ? iso.slice(11, 16) : "–");
