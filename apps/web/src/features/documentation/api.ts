// Documentation (spec 7.3): types and hooks for the discharge summary, the bilingual patient instructions and the
// SBAR handoff. The server decides what a person may do (can_edit, can_sign, why_not); the UI only shows it.

import { useQuery } from "@tanstack/react-query";
import { api, post, put } from "../../lib/api";

export type DocStatus = "none" | "draft" | "blocked" | "final" | "n/a";
export type Lang = "en" | "zh-Hans" | "zh-Hant";

export interface WorkItem {
  encounter_id: string;
  patient_id: string;
  mrn: string | null;
  name: string | null;
  bed: string | null;
  status: "in-progress" | "finished";
  unit: string;
  discharged?: string;
  language: string;
  discharge_planned: boolean;
  discharge_table: "final" | "awaiting_cosign" | "in_review" | "none";
  summary: DocStatus;
  instructions: DocStatus;
  handoff: DocStatus;
}

export interface DocRef {
  id: string;
  status: DocStatus;
  shift?: string;
}

export interface EncounterView {
  encounter_id: string;
  patient_id: string;
  patient_name: string | null;
  mrn: string | null;
  language: string;
  status: string;
  admitted: string | null;
  discharged: string | null;
  unit: string | null;
  reason: string;
  ai_mode: "draft" | "template_only";
  ai_mode_reason: string | null;
  discharge_table: "final" | "awaiting_cosign" | "in_review" | "none";
  summary: DocRef | null;
  instructions: DocRef | null;
  handoff: DocRef | null;
  shift: string;
}

export interface Issue {
  path: string;
  severity: "red" | "amber";
  code: string;
  message: string;
  value: string;
}

export interface Validation {
  validator: string;
  checked: number;
  traceable: number;
  issues: Issue[];
}

export interface Source {
  ref: string;
  type: string;
  kind: string;
  name: string;
  code: string;
  date: string;
  detail: string;
  category: string;
}

export interface Fact {
  text: string;
  source_refs: string[];
  [key: string]: unknown;
}

export interface SummaryContent {
  reason_for_admission: Fact;
  hospital_course: (Fact & { date: string })[];
  discharge_diagnoses: (Fact & { name: string; code: string })[];
  procedures: (Fact & { name: string; code: string; date: string })[];
  medications: Record<"new" | "changed" | "stopped" | "continued", (Fact & { name: string; rxcui: string; dose: string; note: string })[]>;
  pending_results: (Fact & { test: string })[];
  follow_up: (Fact & { code: string; when: string; department: string; location: string })[];
  red_flags: Fact[];
}

export interface HandoffContent {
  situation: Fact;
  background: Fact[];
  assessment: Fact[];
  recommendation: Fact[];
  not_recorded?: string[]; // topics with nothing in the 12-hour input (intake_output, pain, ...)
}

export interface InstrMedicine {
  item: string;
  category: "new" | "changed" | "stopped" | "continued";
  name: string;
  rxcui: string;
  dose: string;
  purpose: string;
  times: string[];
  cautions: string[];
  source_refs: string[];
}

export interface InstructionsContent {
  schema: string;
  summary: string;
  reason: { item: string; kind: string; code: string; name: string; source_refs: string[] }[];
  medicines: InstrMedicine[];
  follow_ups: { item: string; department: string; when: string; location: string; source_refs: string[] }[];
  red_flags: { code: string; because: string[] }[];
  daily_care: { code: string; because: string[] }[];
  unit: string;
}

export interface RenderedBlock {
  key: string;
  title: string;
  lines?: { key: string; text: string }[];
  columns?: string[];
  rows?: { key: string; category: string; cells: string[] }[];
  groups?: { action?: string; group?: string; title?: string; text?: string; items: { key: string; text: string }[] }[];
}

export interface Rendering {
  lang: Lang;
  title: string;
  blocks: RenderedBlock[];
  text: string;
  terms: { key: string; name: string; explanation: string | null; source: string; explained: boolean }[];
  phone: string;
  unit: string;
  ai_note: string;
  readability: {
    grade?: number;
    band?: "below" | "within" | "above";
    in_target?: boolean;
    abbreviations?: string[];
    no_abbreviations?: boolean;
    terms?: number;
    unexplained_terms?: string[];
    terms_explained?: boolean;
    from_knowledge_graph?: number;
  };
}

export interface DocView {
  id: string;
  kind: "discharge_summary" | "patient_instructions" | "handoff";
  encounter_id: string;
  patient_id: string;
  patient_name: string | null;
  mrn: string | null;
  patient_language: string;
  doc_status: "preliminary" | "final";
  date: string | null;
  mode: "ai" | "template";
  content: SummaryContent | HandoffContent | InstructionsContent;
  validation: Validation;
  raw_validation: Validation | null;
  blocking: boolean;
  sign_block: string | null;
  can_edit: boolean;
  can_sign: boolean;
  why_not: string | null;
  signatures: { role: string; by: string | null; name: string | null; at: string | null }[];
  authenticator: string | null;
  edits: { by: string; name: string; role: string; at: string; issues_red: number }[];
  run_id: string | null;
  prompt_version: string | null;
  notes: string[];
  problems: string[];
  agent_id: string;
  evaluated: boolean;
  sources?: Source[];
  inputs?: { admitted: string | null; discharged: string | null; unit: string | null; chunks: { name: string; title: string; refs: string[] }[] };
  table_status?: string;
  renderings?: Record<Lang, Rendering>;
  default_language?: Lang;
  summary?: string;
  summary_items?: Record<string, Fact & { name?: string; dose?: string; when?: string }>;
  deliveries?: unknown[];
  consent_sms?: "permit" | "deny" | "missing";
  menus?: Record<"purposes" | "times" | "cautions" | "departments" | "red_flags" | "daily_care", Record<string, string>>;
  shift?: string;
  draft_seconds?: number;
  model_calls?: number;
}

export interface Insufficient {
  status: "insufficient";
  missing: string[];
  mode?: string;
}

export type DraftOutcome = DocView | Insufficient;

export function isInsufficient(x: DraftOutcome): x is Insufficient {
  return (x as Insufficient).status === "insufficient";
}

export function useWorklist() {
  return useQuery({ queryKey: ["doc-worklist"], queryFn: () => api<{ items: WorkItem[] }>("/api/documentation/worklist"), refetchInterval: 5000 });
}

export function useEncounter(id: string | null) {
  return useQuery({
    queryKey: ["doc-encounter", id],
    queryFn: () => api<EncounterView>(`/api/documentation/encounters/${id}`),
    enabled: Boolean(id),
    refetchInterval: 4000,
  });
}

export function useDocument(id: string | null | undefined) {
  return useQuery({ queryKey: ["doc", id], queryFn: () => api<DocView>(`/api/documentation/documents/${id}`), enabled: Boolean(id) });
}

export const draftSummary = (encounter: string, force = false) =>
  post<DraftOutcome>(`/api/documentation/encounters/${encounter}/discharge-summary`, { force });
export const draftInstructions = (encounter: string, force = false) =>
  post<DraftOutcome>(`/api/documentation/encounters/${encounter}/patient-instructions`, { force });
export const draftHandoff = (encounter: string, force = false) =>
  post<DocView>(`/api/documentation/encounters/${encounter}/handoff`, { force });
export const saveDocument = (id: string, content: unknown) => put<DocView>(`/api/documentation/documents/${id}`, { content });
export const signDocument = (id: string) => post<DocView>(`/api/documentation/documents/${id}/sign`);
export const deliver = (id: string, channel: "print" | "sms" | "portal") =>
  post<{ channel: string; sent: boolean; reason?: string; url?: string; task_id?: string | null; language: string }>(
    `/api/documentation/documents/${id}/deliver`, { channel });

export const LANG_LABEL: Record<Lang, string> = { en: "English", "zh-Hans": "简体中文", "zh-Hant": "繁體中文" };

export function issuesAt(validation: Validation | undefined, path: string): Issue[] {
  return (validation?.issues ?? []).filter((i) => i.path === path);
}
