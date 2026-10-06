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
  source: "ai_draft" | "dictated";
  ai_status: "ok" | "needs_human" | "unavailable" | "not_used";
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

// ---------- Phase 2: requisition pipeline ----------

export interface Sourced {
  value: string;
  source_quote: string;
  confidence: number;
  corrected_by?: string;
}

export type ExtractionFields = Record<string, Sourced | Sourced[]>;

export interface RequisitionSummary {
  id: string;
  patient_id: string;
  patient_name: string;
  patient_language: string;
  referrer_name: string;
  received_at: string;
  channel: string;
  status: string;
  requested_exam: string | null;
  low_confidence: string[];
  ai_priority: string | null;
  priority: string | null;
  triage_reviewed: boolean;
  red_flags: string[];
  days_left: number | null;
  protocol_id: string | null;
  protocol_name: string | null;
  protocol_approved: boolean;
  modality: string | null;
  contrast_status: string | null;
  mri_status: string | null;
  appointment_id: string | null;
  waitlist_id: string | null;
}

export interface Protocol {
  id: string;
  name: string;
  modality: string;
  exam_code: string;
  contrast: boolean;
  minutes: number;
  indications: string[];
}

export interface TriageRecord {
  ai_priority: string | null;
  ai_rationale: string;
  ai_red_flags: string[];
  ai_status: string;
  model: string;
  final_priority: string;
  review_action: string | null;
  override_reason: string | null;
  reviewed_by: string | null;
  reviewed_at: string | null;
}

export interface ContrastCheck {
  status: string;
  basis: string[];
  egfr: number | null;
  egfr_date: string | null;
  risk_factors: string[];
  reactions: string[];
}

export interface MriScreening {
  id: string;
  token?: string;
  requisition_id: string;
  patient_id: string;
  patient_name?: string;
  appointment_id: string | null;
  appointment_start?: string | null;
  language: string;
  status: string;
  answers: Record<string, boolean>;
  free_text: string;
  devices: { patient_words: string; device_name: string; category: string; list_match: string | null; mr_status: string }[];
  flags: string[];
  ai_status: string | null;
  submitted_at: string | null;
  reviewed_by: string | null;
  review_decision: string | null;
  review_note: string | null;
  reviewed_at: string | null;
}

export interface RetrievalTask {
  id: string;
  appointment_id: string;
  patient_id: string;
  patient_name?: string;
  exam_name?: string | null;
  appointment_start?: string | null;
  facility: string;
  reason: string;
  status: string;
  attempts: number;
  max_attempts: number;
  next_attempt_at: string;
  created_at: string;
  completed_at: string | null;
  events: { ts: string; text: string }[];
  imported_study_ids: string[];
}

export interface RequisitionDetail {
  summary: RequisitionSummary;
  text: string;
  patient: { id: string; name: string; age: number; sex: string; language: string };
  extraction: {
    fields: ExtractionFields;
    ai_status: string;
    model: string;
    prompt_version: string;
    low_confidence: string[];
    corrections: { field: string; by: string; at: string }[];
  } | null;
  field_labels: Record<string, string>;
  low_confidence_threshold: number;
  triage: TriageRecord | null;
  targets: Record<string, number>;
  protocol: {
    primary_id: string;
    alternative_ids: string[];
    rationale: string;
    contrast_required: boolean;
    ai_status: string;
    model: string;
    approved_id: string | null;
    approved_by: string | null;
    change_reason: string | null;
    candidates: { id: string; name: string; score: number }[];
    primary: Protocol;
    alternatives: Protocol[];
    approved: Protocol | null;
    library: Protocol[];
  } | null;
  contrast: ContrastCheck | null;
  mri: MriScreening | null;
  appointment: Appointment | null;
  waitlist: { id: string; urgency: string; duration_minutes: number | null } | null;
  priors: RetrievalTask[];
  prep: { key: string; language: string; text: string; note: string | null } | null;
}

// ---------- Phase 3: radiology operations ----------

export type BacklogState = "on_track" | "at_risk" | "overdue";

export interface BacklogStudy {
  id: string;
  patient_id: string;
  patient_name: string;
  exam_code: string;
  exam_name: string;
  modality: string;
  site_id: string | null;
  site_name: string | null;
  priority: string;
  performed_at: string;
  minutes: number;
  age_h: number;
  target_h: number;
  due_at: string;
  remaining_h: number;
  state: BacklogState;
  age_bucket: string;
  assigned_to: { id: string; name: string } | null;
  has_image: boolean;
  draft_report_id: string | null;
}

export interface BacklogGroup { key: string; label: string; count: number; overdue: number; at_risk: number }

export interface Reader {
  id: string;
  name: string;
  modalities: string[];
  on_shift: boolean;
  queue_count: number;
  queue_minutes: number;
  overdue: number;
  signed_today: number;
}

export interface Suggestion { study_id: string; from_id: string; from_name: string; to_id: string; to_name: string; minutes: number; reason: string }

export interface TatSummary { count: number; median_h: number | null; p90_h: number | null; within_target: number | null }

export interface BacklogBoard {
  version: number;
  kpis: { unread: number; overdue: number; at_risk: number; oldest_h: number; median_tat_h: number | null; within_target: number | null };
  studies: BacklogStudy[];
  groups: Record<"site" | "modality" | "priority" | "age", BacklogGroup[]>;
  radiologists: Reader[];
  suggestions: Suggestion[];
  turnaround: {
    window_days: number;
    overall: TatSummary;
    by_priority: (TatSummary & { priority: string; target_h: number })[];
    daily: (TatSummary & { date: string })[];
  };
  targets: { hours: Record<string, number>; at_risk_fraction: number };
}

export interface BacklogStudyDetail extends BacklogStudy {
  patient_age: number;
  patient_sex: string;
  indication: string;
  referrer_name: string;
  report_status: string;
  template: { findings: string; impression: string };
  assignment_history: { ts: string; to: string; from: string | null; by: string; reason: string }[];
}

export interface CaseEvent { ts: string; kind: "opened" | "notify" | "renotify" | "escalate" | "acknowledged" | "closed"; text: string; actor: string }

export interface CriticalCase {
  id: string;
  report_id: string;
  study_id: string | null;
  patient_id: string;
  patient_name: string;
  referrer_id: string;
  referrer_name: string;
  referrer_phone: string;
  exam_name: string | null;
  finding: string;
  level: "critical" | "urgent" | "significant";
  level_label: string;
  created_at: string;
  opened_by: string;
  status: "open" | "escalated" | "acknowledged" | "closed";
  step: number;
  next_action_at: string | null;
  ack_due_at: string;
  seconds_to_ack_due: number;
  overdue: boolean;
  acknowledgement: { by_name: string; by_role: string; method: string; at: string; recorded_by: string } | null;
  closed_at: string | null;
  closed_by: string | null;
  close_note: string | null;
  events: CaseEvent[];
}

export interface LevelPolicy { label: string; real_world: string; renotify_after_s: number; escalate_after_s: number }
export interface CriticalPolicy { levels: Record<string, LevelPolicy>; escalate_to_user_id: string; updated_by: string | null; updated_at: string | null }

export interface PeerReviewItem {
  id: string;
  run_id: string;
  report_id: string;
  study_id: string;
  original_reader_id: string | null;
  original_reader_name?: string;
  reviewer_id: string | null;
  reviewer_name?: string | null;
  assigned_at: string;
  status: "assigned" | "completed" | "unassigned";
  score: "concur" | "minor" | "significant" | null;
  discrepancy_type: string | null;
  comment: string;
  completed_at: string | null;
  exam_name: string;
  modality: string;
  performed_at: string;
  patient_age: number;
  patient_sex: string;
  indication: string;
  report_sections: { label: string; text: string }[];
  report_signed_on: string | null;
}

export interface QaSummary { reviews: number; concur: number; minor: number; significant: number; concur_rate: number | null; significant_rate: number | null }

export interface QaData {
  config: { sample_rate: number; run_hour: number; enabled: boolean; updated_by: string | null; updated_at: string | null };
  report: {
    overall: QaSummary;
    by_radiologist: (QaSummary & { radiologist_id: string; name: string; by_modality: Record<string, QaSummary> })[];
    by_exam: (QaSummary & { exam_code: string; exam_name: string; modality: string })[];
    discrepancy_types: Record<string, number>;
  };
  runs: { id: string; ts: string; trigger: string; by: string; candidates: number; sampled: number; assigned: number; unassigned: number }[];
  open: number;
  unassigned: number;
  recent: PeerReviewItem[];
  scores: Record<string, string>;
  discrepancy_types: Record<string, string>;
}
