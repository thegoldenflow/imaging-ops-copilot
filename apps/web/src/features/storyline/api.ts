// The hospital storyline's API (spec 8.2, WP9): GET /api/storyline (the twelve steps with their live trail),
// POST /api/storyline/reset (demo reset + preset, with the patient's language: en, Margaret Ellis, the default, or
// zh, Wang Xiuying), POST /api/storyline/advance (the clock to the step's beat), and the
// demo's automation: POST /api/storyline/steps/{key}/autoplay (do the current step) and .../jump (do every step before
// that one), a background job whose progress GET shows as `autoplay`.

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

/** A step the demo's automation did: who started it (the presenter's session) and the person it acted as. */
export interface Automated {
  by: string;
  as: string;
  at: string;
  job: string;
}

/** The demo's automation ("Do this step for me", "Jump to step N"): the walk through the API as the cast. */
export interface AutoplayJob {
  id: string;
  status: "running" | "done" | "failed";
  mode: "step" | "jump";
  keys: string[];
  steps: number[];
  target: number | null;
  done: string[];
  current: string | null;
  current_n: number | null;
  current_title: string | null;
  current_cast: string | null;
  results: { n: number; key: string; ok: boolean; seconds: number; result: string }[];
  started_by: { id: string; name: string; role: string };
  started_at: string;
  finished_at: string | null;
  error: string | null;
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
  automated?: Automated | null;
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
    name_native: string | null; // her name in Chinese characters (the Mandarin-speaking patient)
    birth_date: string;
    age: number;
    language: string; // her record's language (BCP 47)
    story_language: string; // the reset's choice: zh or en
    call_language: string; // her calls' and instructions' language: zh-Hans or en
    call_lines: string[];
    followup_lines: string[];
    followup_closing: string; // her answer to the follow-up's remaining questions ("nothing else")
    translations: Record<string, string>; // each scripted line not in English -> its English translation (fixed text)
    imaging_record: string;
    mpi: { hospital: string; imaging: string; matched_on: string; note: string } | null;
  } | null;
  steps: StoryStep[];
  current: number | null;
  beats: Record<string, string | null>;
  autoplay: AutoplayJob | null;
  capacity?: { surgery_ready?: string[]; warnings?: string[] };
  languages: { code: string; label: string }[]; // the reset's choice of patient
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
      return d?.job?.status === "running" || d?.autoplay?.status === "running" || aiBusy ? 1000 : 3000;
    },
  });
}

export const resetStoryline = (language = "en") => post<{ ok: boolean; prepared_at: string; language: string }>("/api/storyline/reset", { language });
/** The reset's choice when the storyline does not say (the English story is the default). */
export const LANGUAGES = [
  { code: "en", label: "English-speaking: Margaret Ellis" },
  { code: "zh", label: "Mandarin-speaking: Wang Xiuying (王秀英)" },
];
export const advanceStoryline = () => post<{ job: SimJob; beat: string; target: string }>("/api/storyline/advance");
export const askStepAi = (key: string) => post<{ step: number; items: string[][] }>(`/api/storyline/steps/${key}/ai`);
export const autoplayStep = (key: string) => post<AutoplayJob>(`/api/storyline/steps/${key}/autoplay`);
export const jumpToStep = (key: string) => post<AutoplayJob>(`/api/storyline/steps/${key}/jump`);
/** The note the demo's automation writes on what it signs. */
export const isAutomationNote = (note: string | null | undefined) => !!note?.startsWith("Demo automation on behalf of");
/** The page a step's route opens, without its link to an item (`/follow-up?call=…` is on `/follow-up`). */
export const routePath = (route: string) => route.split("?")[0];

/** Hospital-clock strings carry their own offset: show them as written (no time-zone conversion). */
export const clock = (iso: string | null | undefined) => (iso ? `${iso.slice(0, 10)} ${iso.slice(11, 16)}` : "–");
export const hhmm = (iso: string | null | undefined) => (iso ? iso.slice(11, 16) : "–");
