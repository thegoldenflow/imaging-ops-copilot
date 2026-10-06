// Response shapes from the FastAPI backend (hand-written for the demo).

export type Role =
  | "front_desk"
  | "technologist"
  | "radiologist"
  | "operations_manager"
  | "medical_director"
  | "admin"
  | "referrer";

export const ROLE_LABEL: Record<Role, string> = {
  front_desk: "Front desk",
  technologist: "Technologist",
  radiologist: "Radiologist",
  operations_manager: "Operations manager",
  medical_director: "Medical director",
  admin: "Administrator",
  referrer: "Referring physician",
};

export interface StaffUser {
  id: string;
  name: string;
  role: Role;
  site_ids: string[];
  referrer_id: string | null;
}

export interface Site {
  id: string;
  name: string;
  address: string;
  modalities: string[];
}

export interface Meta {
  llm_mode: "mock" | "anthropic";
  version: number;
  now: string;
  sites: Site[];
}

export interface UtilRow {
  site_id: string;
  name: string;
  booked_min: number;
  open_min: number;
  idle_min: number;
  utilization: number;
  scanner_id?: string;
  modality?: string;
}

export interface Dashboard {
  today: { sites: UtilRow[]; scanners: UtilRow[] };
  week: { sites: UtilRow[]; scanners: UtilRow[] };
  heatmap: { site_id: string; hour: number; utilization: number }[];
  kpis: Record<string, number>;
  model: { auc: number; base_rate: number };
  version: number;
}

export interface Appointment {
  id: string;
  patient_id: string;
  patient_name: string;
  patient_language: string;
  site_id: string;
  site_name: string;
  scanner_id: string;
  modality: string;
  exam_code: string;
  exam_name: string;
  start: string;
  end: string;
  status: string;
  urgency: string;
  no_show_risk: number | null;
  risk_factors: string[];
  high_risk: boolean;
  reminder_confirmed: boolean;
  extra_reminder: boolean;
}

export interface Candidate {
  waitlist_id: string;
  patient_id: string;
  patient_name: string;
  language: string;
  exam_name: string;
  urgency: string;
  wait_days: number;
  acceptable_sites: string[];
  key_referrer: boolean;
  score: number;
  breakdown: Record<string, number>;
  exclusion: string | null;
}

export interface Offer {
  id: string;
  waitlist_id: string;
  patient_id: string;
  patient_name: string;
  language: string;
  message_id: string;
  status: string;
}

export interface BackfillCase {
  id: string;
  created_at: string;
  source_appointment_id: string;
  slot: { scanner_id: string; site_id: string; modality: string; start: string; end: string };
  candidates: Candidate[];
  excluded: Candidate[];
  offers: Offer[];
  status: string;
  new_appointment_id: string | null;
  filled_by_patient_id: string | null;
  compute_ms: number;
}

export interface Study {
  id: string;
  patient_id: string;
  patient_name: string;
  patient_age: number;
  patient_sex: string;
  exam_name: string;
  referrer_name: string;
  performed_at: string;
  indication: string;
  report_id: string | null;
  report_status: string;
}

export interface Section {
  key: string;
  label: string;
  ai_text: string;
  final_text: string;
  status: "pending" | "accepted" | "edited" | "deleted";
}

export interface Report {
  id: string;
  study_id: string;
  status: "draft" | "signed";
  ai_status: "ok" | "needs_human" | "unavailable";
  ai_error: string | null;
  image_quality: { adequate: boolean; notes: string } | null;
  sections: Section[];
  urgent_findings: { finding: string; reason: string; confirmed: boolean | null }[];
  uncertainties: string[];
  model: string;
  prompt_version: string;
  llm_mode: string;
  created_at: string;
  signed_by: string | null;
  signed_at: string | null;
  edit_ratio: number | null;
  sent_to_referrer_at: string | null;
  study: Study;
}

export interface Message {
  id: string;
  channel: string;
  kind: string;
  patient_id: string | null;
  patient_name: string | null;
  to: string;
  language: string;
  body: string;
  appointment_id: string | null;
  scheduled_for: string;
  status: string;
  sent_at: string | null;
}

export interface CallTurn {
  role: "caller" | "agent" | "tool";
  text: string;
  ts: string;
}

export interface CallSession {
  id: string;
  started_at: string;
  ended_at: string | null;
  agent_mode: string;
  verified_patient_id: string | null;
  transcript: CallTurn[];
  actions: { tool: string; [k: string]: unknown }[];
  outcome: string;
  summary: string | null;
  latencies_ms: number[];
  patient_name?: string | null;
}
