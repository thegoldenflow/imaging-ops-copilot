// Patient voice services (spec 7.4): types and hooks for the call centre, the follow-up calls and the nurse queue.
// Calls are text-simulated: the browser turns speech into text (Web Speech) and reads the replies aloud; the server
// only sees text. The server decides what a person may do; the UI shows it.

import { useQuery } from "@tanstack/react-query";
import { api, ApiError, post } from "../../lib/api";

export type CallLang = "en" | "zh-Hans" | "zh-Hant";

export interface Turn {
  role: "caller" | "agent";
  text: string;
  at: string;
  lang: string;
  intent?: string | null;
  source?: string | null;
  question?: string | null;
  gloss?: string | null; // an agent turn in Chinese: the same hand-written reply in English (the subtitle)
}

export interface CallAction {
  action: string;
  status: string;
  refs: string[];
  approval_task_id?: string | null;
  summary: string;
  at: string;
  section?: string;
  appointment?: string;
  proposed?: string;
}

export interface HospitalCall {
  id: string;
  started_at: string;
  ended_at: string | null;
  language: CallLang;
  patient_id: string | null;
  mrn?: string | null;
  caller_relation: string | null;
  identity_failures: number;
  unrecognised: number;
  transcript?: Turn[];
  intents: { intent: string; source: string; confidence: number; topic?: string | null }[];
  actions: CallAction[];
  outcome: "in_progress" | "resolved" | "requested" | "transferred" | "abandoned";
  transfer_reason: string | null;
  summary: string | null;
  model_status: string | null;
  pending: string | null;
  agent: string;
}

export interface TurnResponse {
  reply: string;
  ended: boolean;
  language: CallLang;
  call: HospitalCall;
}

export interface ApptView {
  appointment_id: string;
  service: string;
  service_display: string;
  service_name?: string;
  place?: string;
  start: string;
  minutes: number;
  location: string;
  status: string;
}

export interface CallRequest {
  task_id: string;
  kind: "reschedule" | "cancel" | "registration";
  description: string;
  authored_on: string;
  mrn?: string | null;
  appointment?: ApptView;
  proposed?: ApptView;
  run_id: string;
  // registration (WP9): the EHR registers the ED arrival when the clerk confirms
  recommendation?: string | null;
  args?: { complaint?: string; mode?: string; call?: string };
}

export interface RedFlag {
  code: string;
  label: string;
  label_zh: string;
  emergency: boolean;
  how: string;
  words: string;
  question?: string | null;
  temperature_c?: number | null;
  source: string;
}

export interface NurseAction {
  kind: "callback" | "close" | "refer";
  note: string;
  name: string;
  role: string;
  at: string;
  communication_id: string;
  task_id?: string;
}

export interface FollowupItem {
  id: string;
  encounter_id: string;
  patient_id: string;
  unit_id: string | null;
  mrn?: string | null;
  patient_name?: string;
  status: "manual" | "scheduled" | "in_progress" | "completed" | "no_answer" | "not_confirmed";
  review_status: "none" | "open" | "closed" | "referred";
  priority: "stat" | "urgent" | "asap" | "routine" | null;
  escalated: boolean;
  emergency: boolean;
  escalated_by: string | null;
  missing: string[];
  reason: string | null;
  language: CallLang;
  discharged_at: string | null;
  due_at: string | null;
  source: string;
  workflow_id: string | null;
  summary: string | null;
  red_flags: RedFlag[];
  classifier: { status?: string; escalate?: boolean; reason?: string; confidence?: number; evaluated?: boolean } | null;
  patient_words: string[];
  task: { id: string; status: string; priority: string; description: string } | null;
  communication_id: string | null;
  observation_ids: string[];
  nurse_actions: NurseAction[];
  started_at: string | null;
  ended_at: string | null;
  questions: number;
  transcript?: Turn[];
  script?: { key: string; kind: string; text: Record<string, string> }[];
  seen_by_model?: string | null;
  restricted?: boolean; // outside the user's 72-hour scope: no patient details in the list
}

export interface DemoCases {
  followups?: Record<string, { stay: string; patient: string; language: string; unit: string; missing?: string;
    call: { id: string; status: string; review_status: string } | null; birth_date_line?: { en: string; zh: string } }>;
  callers?: Record<string, { patient: string; language: string; appointments: string[]; services?: string[]; mrn?: string;
    identity_line?: string; identity_line_zh?: string; label?: string }>;
}

export const voiceKeys = {
  calls: ["voice", "calls"] as const,
  requests: ["voice", "requests"] as const,
  followups: (which: string) => ["voice", "followups", which] as const,
  followup: (id: string) => ["voice", "followup", id] as const,
  summary: ["voice", "summary"] as const,
  demo: ["voice", "demo"] as const,
};

export function useCalls() {
  return useQuery({ queryKey: voiceKeys.calls, queryFn: () => api<{ calls: HospitalCall[] }>("/api/voice/calls") });
}

export function useRequests() {
  return useQuery({ queryKey: voiceKeys.requests, refetchInterval: 5000,
    queryFn: () => api<{ requests: CallRequest[] }>("/api/voice/requests") });
}

export function useFollowups(which: "open" | "calls" | "all") {
  return useQuery({ queryKey: voiceKeys.followups(which), refetchInterval: 5000,
    queryFn: () => api<{ items: FollowupItem[] }>(`/api/voice/followups?which=${which}`) });
}

/** A call that is not there yet: linked before it was scheduled (the storyline's step 11 opens hers while its Advance
 * moves the clock 48 hours on). */
export const notScheduled = (error: unknown) => error instanceof ApiError && error.status === 404;

export function useFollowup(id: string | null) {
  return useQuery({ queryKey: voiceKeys.followup(id ?? ""), enabled: !!id,
    queryFn: () => api<FollowupItem>(`/api/voice/followups/${id}`),
    // asked again until it is scheduled: a 404 left alone stayed on screen after the call existed
    refetchInterval: (q) => (notScheduled(q.state.error) ? 3000 : false) });
}

export function useDemo() {
  return useQuery({ queryKey: voiceKeys.demo, queryFn: () => api<DemoCases>("/api/voice/demo"), staleTime: 60_000 });
}

export function useEscalationSummary(enabled: boolean) {
  return useQuery({ queryKey: voiceKeys.summary, enabled, refetchInterval: 10_000,
    queryFn: () => api<{ open: number; escalated: number; stat: number; urgent: number; latest: string | null }>(
      "/api/voice/followups/summary") });
}

export const startCall = () => post<HospitalCall>("/api/voice/calls");
export const sendTurn = (id: string, text: string) => post<TurnResponse>(`/api/voice/calls/${id}/turn`, { text });
export const endCall = (id: string) => post<HospitalCall>(`/api/voice/calls/${id}/end`);
export const decide = (taskId: string, decision: "approve" | "reject", note = "") =>
  post(`/api/agents/tasks/${taskId}/decision`, { decision, note });
export const startFollowup = (id: string) => post<{ reply: string; item: FollowupItem }>(`/api/voice/followups/${id}/start`);
export const answerFollowup = (id: string, text: string) =>
  post<{ reply: string; ended: boolean; item: FollowupItem }>(`/api/voice/followups/${id}/answer`, { text });
export const noAnswer = (id: string) => post<FollowupItem>(`/api/voice/followups/${id}/no-answer`);
export const nurseAction = (id: string, kind: "callback" | "close" | "refer", note: string) =>
  post<FollowupItem>(`/api/voice/followups/${id}/actions`, { kind, note });
export const sweep = () => post<{ scheduled: string[] }>("/api/voice/followups/sweep");

export const PRIORITY_TONE: Record<string, "red" | "amber" | "blue" | "slate"> = {
  stat: "red", urgent: "amber", asap: "blue", routine: "slate",
};
export const PRIORITY_LABEL: Record<string, string> = {
  stat: "Emergency", urgent: "Urgent", asap: "Soon", routine: "Routine",
};
export const MISSING_LABEL: Record<string, string> = {
  discharge: "no discharge time", consent: "no follow-up call consent", phone: "no phone number",
  instructions: "instructions not signed",
};
export const OUTCOME_TONE: Record<string, "green" | "amber" | "blue" | "slate" | "red"> = {
  resolved: "green", requested: "blue", transferred: "amber", abandoned: "slate", in_progress: "slate",
};

export function speechLang(lang: CallLang | string): string {
  return lang === "zh-Hant" ? "zh-TW" : lang === "zh-Hans" ? "zh-CN" : "en-CA";
}
