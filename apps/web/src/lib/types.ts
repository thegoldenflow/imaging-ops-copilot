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

export type LlmMode = "mock" | "anthropic" | "gemini";

export interface Meta {
  llm_mode: LlmMode;
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

export interface DoseEvent { sequence: number; protocol_step: string; acquisition_type: string; kvp: number; exposure_mas: number; scanning_length_mm: number; ctdivol_mgy: number; dlp_mgycm: number; phantom: string }

export interface DoseRecord {
  id: string;
  appointment_id: string;
  study_id: string | null;
  patient_id: string;
  patient_name: string;
  scanner_id: string;
  scanner_name: string;
  site_id: string;
  exam_code: string;
  exam_name: string;
  protocol_id: string;
  protocol_name: string;
  performed_at: string;
  sr_template: string;
  source: string;
  ctdivol_mgy: number;
  dlp_total_mgycm: number;
  reference: { ctdivol_mgy: number; dlp_mgycm: number };
  exceedance: { metrics: string[]; ctdivol_ratio: number; dlp_ratio: number } | null;
  review: { outcome: string; note: string; by: string; at: string } | null;
  events?: DoseEvent[];
}

export interface Trend { labels: string[]; series: { name: string; values: (number | null)[] }[] }

export interface DoseOverview {
  coverage: { ct_exams: number; with_record: number; missing: number };
  records: number;
  exceeding: number;
  open_exceptions: number;
  exceptions: DoseRecord[];
  protocols: { protocol_id: string; name: string; reference: { ctdivol_mgy: number; dlp_mgycm: number }; records: number; median_ctdivol: number | null; median_dlp: number | null; exceeding: number }[];
  scanners: { scanner_id: string; site: string; records: number; exceeding: number; recent_exceed_rate: number | null }[];
  trend_by_scanner: Trend;
  trend_by_protocol: Trend;
  review_outcomes: Record<string, string>;
}

// ---------- Phase 4 · System 15 inventory ----------

export interface InventoryLot {
  lot: string;
  expiry: string;
  quantity: number;
  received_at: string;
  status: "ok" | "expiring" | "expired" | "empty";
  days_to_expiry: number;
}

export interface PurchaseOrder {
  id: string;
  item_id: string;
  site_id: string;
  supplier: string;
  quantity: number;
  status: "draft" | "submitted" | "received" | "cancelled";
  created_at: string;
  reason: string;
  submitted_by: string | null;
  submitted_at: string | null;
  received_at: string | null;
  item_name?: string;
  site_name?: string;
}

export interface InventoryItem {
  id: string;
  site_id: string;
  site_name: string;
  product_code: string;
  name: string;
  category: "contrast" | "consumable";
  unit: string;
  uses: string;
  reorder_point: number;
  reorder_qty: number;
  quantity: number;
  usable: number;
  status: "low" | "ok";
  usage_per_day: number;
  days_left: number | null;
  open_order: PurchaseOrder | null;
  lots: InventoryLot[];
}

export interface InventoryAlert {
  kind: "low_stock" | "expiring" | "expired";
  severity: "red" | "amber";
  item_id: string;
  site_id: string;
  name: string;
  text: string;
  lot?: string;
  expiry?: string;
  order_id?: string | null;
}

export interface StockMovement {
  id: string;
  item_id: string;
  item_name: string;
  site_id: string;
  at: string;
  kind: "consumed" | "received" | "adjusted" | "discarded";
  quantity: number;
  lot: string | null;
  appointment_id: string | null;
  by: string;
  note: string;
}

export interface InventoryOverview {
  items: InventoryItem[];
  alerts: InventoryAlert[];
  orders: PurchaseOrder[];
  movements: StockMovement[];
  sites: { id: string; name: string }[];
  expiry_warning_days: number;
}

// ---------- System 16 referral analytics ----------

export interface ReferrerTrend {
  referrer_id: string;
  name: string;
  specialty: string;
  clinic: string;
  phone: string;
  is_key: boolean;
  total: number;
  last_week: number;
  series: number[];
  baseline_per_week: number;
  recent_per_week: number;
  change: number | null;
  change_label: string;
  last_referral: string;
  declining: boolean;
  visit?: { status: "planned" | "visited"; note: string; by: string; at: string } | null;
}

export interface Breakdown { key: string; name?: string; total: number; last_week: number }

export interface ReferralOverview {
  weeks: string[];
  labels: string[];
  kpis: { total: number; last_week: number; prior_week: number; week_change: number | null; week_change_label: string; week_label: string; avg_per_week: number; active_referrers: number; declining: number };
  series: { name: string; values: number[] }[];
  series_by_modality: { name: string; values: number[] }[];
  by_specialty: Breakdown[];
  by_modality: Breakdown[];
  by_site: Breakdown[];
  by_exam: Breakdown[];
  referrers: ReferrerTrend[];
  visit_list: ReferrerTrend[];
  specialties: string[];
  sites: { id: string; name: string }[];
  thresholds: { decline: number; recent_weeks: number; baseline_weeks: number; min_baseline_per_week: number };
}

export type SummarySegment = { text: string } | { fact: string; value: string; label: string; tile: string };

export interface WeeklySummary {
  week_start: string;
  headline: SummarySegment[];
  sentences: SummarySegment[][];
  status: "draft" | "approved";
  ai_status: "ok" | "needs_human" | "unavailable";
  model: string;
  prompt_version: string;
  generated_at: string;
  generated_by: string;
  approved_by: string | null;
  approved_at: string | null;
  error: string | null;
}

// ---------- System 17 referrer portal ----------

export interface PortalRequisition {
  id: string;
  patient_id: string;
  patient_name: string;
  received_at: string;
  channel: string;
  status: string;
  status_text: string;
  requested_exam: string | null;
  confirmed_priority: string | null;
}

export interface PortalPatientRow { id: string; name: string; dob: string; next_appointment: string | null; open_requisitions: number }

export interface PortalPatient {
  id: string;
  name: string;
  dob: string;
  sex: string;
  phone: string;
  preferred_language: string;
  health_card_last4: string;
  appointments: { id: string; start: string; exam_name: string; site_name: string; status: string; protocol_name: string | null }[];
  requisitions: PortalRequisition[];
  reports: { id: string; signed_at: string | null; exam_name: string; impression: string }[];
}

// ---------- System 18 billing ----------

export interface Claim {
  id: string;
  appointment_id: string;
  payer: string;
  fee_code: string;
  amount: number;
  service_date: string;
  submitted_at: string;
  status: "submitted" | "paid" | "rejected" | "voided";
  rejection_reason: string | null;
}

export interface Discrepancy {
  id: string;
  kind: string;
  kind_label: string;
  appointment_id: string;
  appointment_status: string;
  claim_ids: string[];
  claims: Claim[];
  detail: string;
  at_stake: number;
  status: "open" | "resolved";
  outcome: string | null;
  note: string;
  resolved_by: string | null;
  history: { ts: string; action: string; note: string; by: string }[];
  service_date: string;
  site_id: string;
  patient_name: string;
  exam_name: string;
}

export interface BillingOverview {
  discrepancies: Discrepancy[];
  kinds: Record<string, { label: string; open: number; resolved: number; at_stake: number }>;
  outcomes: Record<string, string>;
  claims: { total: number; paid: number; submitted: number; rejected: number; billed: number };
  fees: { exam_code: string; exam_name: string; fee_code: string; description: string; amount: number }[];
  window_days: number;
  submission_days: number;
}

// ---------- System 19 patient feedback ----------

export interface FeedbackResponse {
  id: string;
  patient_name: string;
  site_id: string;
  site_name: string;
  exam_name: string;
  language: string;
  rating: number;
  comment: string;
  submitted_at: string;
  ai_status: "ok" | "needs_human" | "unavailable" | "seeded" | null;
  ai_sentiment: string | null;
  ai_themes: string[];
  ai_summary: string | null;
  model: string | null;
  sentiment: string | null;
  themes: string[];
  confirmed_by: string | null;
}

export interface FeedbackAlert {
  id: string;
  response_id: string;
  site_id: string;
  reason: string;
  created_at: string;
  notified: string;
  status: "open" | "followed_up";
  follow_up: string | null;
  followed_up_by: string | null;
  response: FeedbackResponse;
}

export interface FeedbackOverview {
  kpis: { responses_30d: number; avg_rating_30d: number | null; open_alerts: number; to_confirm: number };
  by_site: { site_id: string; name: string; responses: number; responses_30d: number; avg_rating_30d: number | null; negative_share_30d: number | null; top_complaint: string | null }[];
  labels: string[];
  rating_trend: { name: string; values: (number | null)[] }[];
  theme_trend: { name: string; values: number[] }[];
  theme_counts: { theme: string; label: string; positive: number; neutral: number; negative: number }[];
  responses: FeedbackResponse[];
  alerts: FeedbackAlert[];
  themes: Record<string, string>;
  sites: { id: string; name: string }[];
}

export interface SurveyRow { id: string; token: string; link: string; patient_name: string; exam_name: string; site_id: string; language: string; sent_at: string; status: string }

// ---------- System 20 PHIPA access monitoring ----------

export interface Investigation {
  alert_id: string;
  status: "new" | "investigating" | "closed";
  assignee: string | null;
  outcome: string | null;
  opened_at: string | null;
  closed_at: string | null;
  trail: { ts: string; by: string; action: string; text: string; note: string }[];
}

export interface PhipaAlert {
  id: string;
  rule: string;
  rule_label: string;
  user_id: string;
  user_name: string;
  role: string;
  user_sites: string[];
  patient_id: string | null;
  patient_name: string | null;
  patients: string[];
  first_at: string;
  last_at: string;
  why: string;
  risk: number;
  evidence: { seq: number; ts: string; action: string; resource_type: string; resource_id: string | null; outcome: string; patient_id: string | null; source_ip: string | null }[];
  evidence_count?: number;
  investigation: Investigation;
}

export interface PhipaReport {
  period_days: number;
  since: string;
  generated_at: string;
  alerts: number;
  open: number;
  by_rule: Record<string, { label: string; alerts: number; closed: number }>;
  outcomes: Record<string, number>;
  median_hours_to_close: number | null;
  audit_chain: { intact: boolean; broken_at_seq: number | null };
  events_in_log: number;
}

// ---------- System 21 inspection readiness ----------

export interface DocSection { id: string; heading: string; text: string }
export interface DocVersion { version: string; effective: string; change_note: string; uploaded_by: string; sections: DocSection[] }

export interface InspectionDoc {
  id: string;
  kind: "policy" | "equipment" | "credential" | "qc_record";
  title: string;
  category: string;
  owner: string;
  site_id: string | null;
  site_name: string | null;
  scanner_id: string | null;
  staff_id: string | null;
  performed: string | null;
  due: string | null;
  result: string | null;
  source: string;
  status: "ok" | "upcoming" | "due_soon" | "overdue" | "none";
  days_to_due: number | null;
  version: string | null;
  effective: string | null;
  versions_count: number;
  versions?: DocVersion[];
}

export interface ChecklistItem { key: string; label: string; ok: boolean; detail: string; evidence: string[] }
export interface InspectionReminder { id: string; doc_id: string; title: string; due: string; stage: number; text: string; to: string; sent_at: string; acknowledged_by: string | null }

export interface InspectionOverview {
  checklist: ChecklistItem[];
  due: InspectionDoc[];
  reminders: InspectionReminder[];
  counts: Record<string, number>;
  stages: number[];
}

export interface QaCitation { chunk_id: string; quote: string; doc_id: string; title: string; heading: string; version: string }

export interface QaEntry {
  id: string;
  question: string;
  answer: string;
  found: boolean;
  citations: QaCitation[];
  suggested?: { chunk_id: string; doc_id: string; title: string; heading: string }[];
  ai_status: "ok" | "needs_human" | "unavailable" | "no_match";
  model: string | null;
  prompt_version: string | null;
  asked_by: string;
  asked_at: string;
}
