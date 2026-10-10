// Types and requests of the medication safety API (spec 7.2, apps/api/app/modules/med_safety/router.py).
// Times are hospital-clock local times without a zone ("2026-10-09T07:42:00").

import { useQuery } from "@tanstack/react-query";
import { api, post } from "../../lib/api";

export type Severity = "blocking" | "major" | "moderate" | "minor" | "info" | "none";
export type QueueShow = "open" | "all" | "done";

export interface QueueItem {
  kind: "order_review" | "medrec";
  id: string;
  severity: Severity;
  blocking: boolean;
  status: "pending" | "clear" | "resolved" | "cosign";
  title: string;
  detail: string | null;
  patient_id: string;
  mrn?: string | null;
  name?: string | null;
  encounter_id: string | null;
  unit_id: string | null;
  order_ref?: string;
  order_kind?: "medication" | "service";
  medrec_status?: "open" | "pharmacist_signed" | "final";
  since: string | null;
  wait_minutes: number | null;
  sources: string[];
  commentary_status: string;
  evaluated: boolean;
}

export interface Queue {
  now: string;
  role: string;
  items: QueueItem[];
  counts: { blocking: number; pending: number; cosign: number };
}

export interface Finding {
  rule: string;
  rule_name: string;
  severity: Severity;
  blocking: boolean;
  title: string;
  detail: string;
  recommendation: string;
  evidence_refs: string[];
  entry: string;
  source: string;
}

export interface Concern {
  type: string;
  severity: "major" | "moderate" | "minor";
  explanation: string;
  evidence_refs: string[];
  confidence: number;
}

export interface Commentary {
  concerns: Concern[];
  alternatives: { suggestion: string; rationale: string }[];
  guideline_refs: string[];
  guidelines: { id: string; title: string; text: string }[] | null;
  unresolved_refs: string[] | null;
  dropped_refs: string[] | null;
  problems: string[] | null;
  reason: string | null;
  model_status: string | null;
  run_id: string | null;
  evaluated: boolean | null;
  attempts: number | null;
}

export interface PatientHeader {
  patient_id: string;
  mrn: string | null;
  name: string | null;
  gender: string | null;
  age: number | null;
  encounter_id?: string;
  unit_id?: string | null;
  bed?: string | null;
  reason?: string | null;
  admitted?: string | null;
  encounter_status?: string | null;
  egfr: { value: number; unit: string | null; at: string | null; ref: string } | null;
  weight: { value: number; unit: string | null; at: string | null; ref: string } | null;
  inr: { value: number; unit: string | null; at: string | null; ref: string } | null;
  allergies: { ref: string; code: string; display: string; drug_class: string; criticality: string | null; reaction: string | null }[];
  conditions: { ref: string; display: string; category: string }[];
}

export interface ReviewDetail {
  id: string;
  order_ref: string;
  kind: "medication" | "service";
  patient_id: string;
  encounter_id: string | null;
  unit_id: string | null;
  order: Record<string, string | number | boolean | null>;
  authored_at: string | null;
  reviewed_at: string;
  reviewer_roles: string[];
  findings: Finding[];
  severity: Severity;
  blocking: boolean;
  status: "pending" | "clear" | "resolved";
  commentary_status: string;
  commentary: Commentary | null;
  review_task_id: string | null;
  decision: { decision: string; reason: string; by: string; name: string; role: string; at: string; change_task_id: string | null } | null;
  can_decide: boolean;
  why_not: string | null;
  patient: PatientHeader;
  medrec: { id: string; kind: "admission" | "discharge"; status: string }[];
}

export interface MedLine {
  ref: string;
  rxcui: string;
  name: string;
  drug_class: string;
  text: string;
  dose: number | null;
  unit: string | null;
  per_day: number | null;
  route: string | null;
  prn: boolean;
}

export type ItemType = "match" | "omission" | "dose_change" | "duplicate_therapy" | "new" | "new_without_indication";

export interface RecItem {
  id: string;
  type: ItemType;
  severity: Severity;
  home: MedLine | null;
  inpatient: MedLine | null;
  order: MedLine | null;
  others: MedLine[];
  source: "rule" | "ai";
  confidence: number | null;
  explanation: string;
  ai_note: string | null;
  evidence_refs: string[];
  hospital_use: boolean;
  indication: "rule" | "pending" | "ai" | "none" | null;
  indication_ref: string | null;
  action: "accept" | "modify" | "reject" | null;
  reason: string | null;
  change: string | null;
  by: string | null;
  by_name: string | null;
  at: string | null;
  discrepancy: boolean;
  handled: boolean;
}

export interface DischargeLine {
  rxcui: string;
  name: string;
  category: "new" | "changed" | "stopped" | "continued";
  dose_text: string | null;
  previous_text: string | null;
  change_note: string | null;
  reason: string | null;
  source_refs: string[];
}

export type DischargeTable = Record<"new" | "changed" | "stopped" | "continued", DischargeLine[]>;

export interface SessionDetail {
  id: string;
  encounter_id: string;
  patient_id: string;
  kind: "admission" | "discharge";
  unit_id: string | null;
  status: "open" | "pharmacist_signed" | "final";
  created_at: string;
  updated_at: string;
  severity: Severity;
  ai_status: string;
  ai_detail: { run_id?: string; reason?: string | null; evaluated?: boolean; problems?: string[] } | null;
  document_id: string | null;
  signatures: { role: string; by: string; name: string; at: string }[];
  change_tasks: string[];
  discharge_table: DischargeTable | null;
  items: RecItem[];
  order_flags: Record<string, { review_id: string; severity: Severity; blocking: boolean; status: string; titles: string[] }>;
  unhandled: string[];
  can_sign: boolean;
  can_handle: boolean;
  document: { id: string; doc_status: string; signatures: { role: string; by: string; name: string; at: string }[]; authenticator: string | null } | null;
  patient: PatientHeader;
  discharge_preview: DischargeTable | null;
}

export interface EncounterOrders {
  encounter_id: string;
  orders: ({ ref: string; name: string; text: string; status: string; intent: string; authored: string | null; review: { id: string; severity: Severity; status: string; blocking: boolean } | null })[];
  medrec: { id: string; kind: "admission" | "discharge"; status: string }[];
  patient: PatientHeader;
}

export interface EvidenceEntry {
  ref: string;
  kind: "fhir" | "guideline" | "denied" | "missing";
  label: string;
  text?: string;
  source?: string;
  document?: string;
}

export function useQueue(show: QueueShow) {
  return useQuery({ queryKey: ["ms-queue", show], queryFn: () => api<Queue>(`/api/med-safety/queue?show=${show}`), refetchInterval: 5000 });
}

export function useReview(id: string | null) {
  return useQuery({ queryKey: ["ms-review", id], queryFn: () => api<ReviewDetail>(`/api/med-safety/reviews/${id}`), enabled: Boolean(id) });
}

export function useSession(id: string | null) {
  return useQuery({ queryKey: ["ms-session", id], queryFn: () => api<SessionDetail>(`/api/med-safety/medrec/${id}`), enabled: Boolean(id) });
}

export function useEncounter(id: string | null) {
  return useQuery({ queryKey: ["ms-encounter", id], queryFn: () => api<EncounterOrders>(`/api/med-safety/encounters/${id}`), enabled: Boolean(id) });
}

export function useEvidence(refs: string[] | null) {
  const query = (refs ?? []).map((r) => `ref=${encodeURIComponent(r)}`).join("&");
  return useQuery({
    queryKey: ["ms-evidence", query],
    queryFn: () => api<{ evidence: EvidenceEntry[] }>(`/api/med-safety/evidence?${query}`),
    enabled: Boolean(refs && refs.length),
  });
}

export const runCommentary = (id: string) => post<ReviewDetail>(`/api/med-safety/reviews/${id}/commentary`);
export const decideReview = (id: string, decision: "accept_order" | "request_change", reason: string) =>
  post<ReviewDetail>(`/api/med-safety/reviews/${id}/decision`, { decision, reason });
export const handleItem = (session: string, item: string, action: "accept" | "modify" | "reject", reason: string, change?: string) =>
  post<SessionDetail>(`/api/med-safety/medrec/${session}/items/${item}`, { action, reason, change: change || null });
export const acceptHospitalUse = (session: string, reason: string) =>
  post<SessionDetail>(`/api/med-safety/medrec/${session}/accept-hospital-use`, { reason });
export const signSession = (session: string) => post<SessionDetail>(`/api/med-safety/medrec/${session}/sign`);
export const startMedrec = (encounter: string, kind: "admission" | "discharge") =>
  post<SessionDetail>(`/api/med-safety/encounters/${encounter}/medrec`, { kind });

export const SEVERITY_RANK: Record<Severity, number> = { none: -1, info: 0, minor: 1, moderate: 2, major: 3, blocking: 4 };

export const TYPE_LABEL: Record<ItemType, string> = {
  match: "No discrepancy",
  omission: "Omission",
  dose_change: "Dose or frequency change",
  duplicate_therapy: "Duplicate therapy",
  new: "New (indicated)",
  new_without_indication: "New without indication",
};

export function waitText(minutes: number | null): string {
  if (minutes == null) return "–";
  if (minutes < 60) return `${minutes}m`;
  const h = Math.floor(minutes / 60);
  return h < 48 ? `${h}h ${String(minutes % 60).padStart(2, "0")}m` : `${Math.floor(h / 24)}d ${h % 24}h`;
}

export function hhmm(value: string | null | undefined): string {
  return value ? value.slice(11, 16) : "–";
}
