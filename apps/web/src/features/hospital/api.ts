// Types of the hospital platform API (spec 6.3): census, chart, break-glass, consent, registry.

export interface Unit {
  id: string;
  name: string;
}

export interface UnitsResponse {
  units: Unit[];
  mine: string[];
  scope: "unit" | "hospital" | "none";
}

export interface CensusEntry {
  encounter_id: string;
  encounter_class: string | null;
  patient_id: string;
  mrn: string | null;
  name: string | null;
  bed_id: string | null;
  since: string | null;
}

export type ConsentCategory = "ai_processing" | "followup_call" | "sms";
export type ConsentState = "permit" | "deny" | "missing";

export interface ChartDocument {
  id: string;
  type: string | null;
  doc_status: "preliminary" | "final" | string;
  date: string | null;
  description: string | null;
  module: string | null;
  tier: "ops" | "documentation" | "clinical_ds" | null;
  required_signoff_role: string[];
  cosign: boolean;
  signatures: { role: string; by: string | null; name: string | null; at: string | null }[];
  can_sign: boolean;
  text: string | null;
}

export interface Chart {
  patient: {
    id: string;
    mrn: string;
    name: string | null;
    name_other: string | null;
    gender: string | null;
    birth_date: string | null;
    language: string | null;
  };
  role: string;
  break_glass: { id: string; expires_at: string } | null;
  sections: string[];
  encounter?: { id: string; class: string | null; status: string; start: string | null; end: string | null; location: string | null; reason: string | null };
  consents?: Record<ConsentCategory, ConsentState>;
  can_change_consent?: boolean;
  medications?: { id: string; name: string | null; status: string; intent: string }[];
  observations?: { id: string; label: string | null; value: string | null; at: string | null; category: string | null }[];
  documents?: ChartDocument[];
}

export interface ActiveGrant {
  id: string;
  mrn: string;
  granted_at: string;
  expires_at: string;
  minutes_left: number;
}

export interface ReviewItem {
  id: string;
  user_id: string;
  user_name: string;
  role: string;
  patient_id: string;
  reason: string;
  granted_at: string;
  expires_at: string;
  review_due: string;
  review_status: "pending" | "justified" | "not_justified";
  reviewed_by_name: string | null;
  reviewed_at: string | null;
  review_note: string;
  active: boolean;
  overdue: boolean;
  accessed: Record<string, number>;
  accessed_count: number;
}

export const CONSENT_LABEL: Record<ConsentCategory, string> = {
  ai_processing: "AI processing",
  followup_call: "Follow-up calls",
  sms: "SMS messages",
};

/** What a module does without the consent (6.3: degrade, never fail). */
export const CONSENT_FALLBACK: Record<ConsentCategory, string> = {
  ai_processing: "Documentation offers the template only; no AI draft is generated.",
  followup_call: "No automated follow-up call; a nurse gets a manual follow-up task.",
  sms: "No SMS is sent; the registration desk gets a task to reach the patient another way.",
};

export const TIER_LABEL: Record<string, string> = { ops: "Operations", documentation: "Documentation", clinical_ds: "Clinical decision support" };

export const MIN_REASON = 10;

/** The reason as the server counts it: runs of whitespace collapse to one space. */
export const reasonLength = (text: string) => text.split(/\s+/).filter(Boolean).join(" ").length;
