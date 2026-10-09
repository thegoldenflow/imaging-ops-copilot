import { ArrowRight, CheckCircle2 } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { Badge, Card, PageHeader } from "../../components/ui";
import { useAuth } from "../../lib/auth";
import { ROLE_LABEL, type Role } from "../../lib/types";

interface Step {
  title: string;
  detail: string;
  role: Role;
  userId: string;
  to: string;
  system: string;
}

const STORY: Step[] = [
  {
    title: "Draft the chest X-ray report",
    detail: "Open Mei Chen's chest X-ray. The AI draft flags a possible right upper lobe nodule. Review each section, confirm the urgent finding and sign.",
    role: "radiologist", userId: "U-RAD", to: "/reading", system: "Report Generator",
  },
  {
    title: "Referring physician sees the report and acknowledges the finding",
    detail: "Dr. Helen Park can read the report only after it is signed (drafts return 403). The confirmed nodule opened a critical-result case; she acknowledges it in the portal.",
    role: "referrer", userId: "U-REF", to: "/my-reports", system: "Report Generator",
  },
  {
    title: "A patient calls to cancel",
    detail: "Use the phone agent as Robert Taylor (DOB April 12, 1968): verify identity, then cancel tomorrow's CT. Speak or type.",
    role: "front_desk", userId: "U-FD", to: "/front-desk", system: "Front Desk Automation",
  },
  {
    title: "Backfill the freed CT slot",
    detail: "The cancellation opened a backfill case. Mei Chen ranks first (P2). Send her the offer, in Chinese, and simulate her YES reply.",
    role: "operations_manager", userId: "U-OPS", to: "/scheduling?tab=backfill", system: "Scheduling Command Center",
  },
  {
    title: "Confirmation, prep and reminders go out",
    detail: "The outbox shows the Chinese booking confirmation with a pre-registration link, contrast prep instructions and scheduled reminders. Open the link as the patient.",
    role: "front_desk", userId: "U-FD", to: "/front-desk?tab=outbox", system: "Front Desk Automation",
  },
  {
    title: "Check the audit trail and AI usage",
    detail: "Every PHI access and every denied request is in a hash-chained audit log. AI calls are logged with model, prompt version, latency and cost.",
    role: "admin", userId: "U-ADMIN", to: "/audit", system: "Backend Infrastructure",
  },
];

const PIPELINE: Step[] = [
  {
    title: "Requisitions arrive and the AI pipeline runs",
    detail: "Three faxed requisitions were just received. Each is extracted once, triaged and given a protocol suggestion within seconds. Submit another from the queue.",
    role: "front_desk", userId: "U-FD", to: "/requisitions", system: "Requisition intake",
  },
  {
    title: "Radiologist reviews the MRI with a cochlear implant",
    detail: "Open the MRI brain requisition: highlighted source text, AI priority with red flags, protocol options. Confirm the priority and approve the protocol.",
    role: "radiologist", userId: "U-RAD", to: "/requisitions", system: "Triage · Protocol",
  },
  {
    title: "Book it; the safety questionnaire goes out in Punjabi",
    detail: "Book next available. The patient gets the MRI questionnaire link in Punjabi; prep falls back to English because that translation is not approved yet.",
    role: "front_desk", userId: "U-FD", to: "/requisitions", system: "MRI safety · Prep",
  },
  {
    title: "Patient answers, technologist reviews",
    detail: "Answer the questionnaire from the link. The flag blocks confirmation until a technologist clears it on the MRI safety page.",
    role: "technologist", userId: "U-TECH", to: "/mri-safety", system: "MRI safety",
  },
  {
    title: "Medical director: thresholds and translations",
    detail: "Change the eGFR threshold and watch every contrast check recompute. Approve the Punjabi MRI translation so future messages use it.",
    role: "medical_director", userId: "U-MD", to: "/contrast", system: "Contrast · Prep",
  },
  {
    title: "Prior imaging retrieval with retries",
    detail: "Book the CT abdomen requisition; its outside ultrasound is requested automatically. Switch the archive to “Outage” to watch retries and a final failure.",
    role: "front_desk", userId: "U-FD", to: "/priors", system: "Prior imaging",
  },
];

const OPERATIONS: Step[] = [
  {
    title: "An exam finishes and lands in the reading backlog",
    detail: "Mark one of today's or tomorrow's exams done. A study is created and assigned to a credentialed radiologist; CT exams also get a dose record from the scanner.",
    role: "technologist", userId: "U-TECH", to: "/scheduling?tab=appointments", system: "Backlog · CT dose",
  },
  {
    title: "Balance the reading backlog",
    detail: "Dr. Liu is off shift and Dr. Webb's MRI queue is long. Review turnaround against target and apply the suggested reassignments; each one is audited.",
    role: "operations_manager", userId: "U-OPS", to: "/backlog", system: "Reporting backlog",
  },
  {
    title: "Read and sign from your queue",
    detail: "Open a study from your queue, edit the normal template and sign. Add a finding that needs communication to open a critical-result case.",
    role: "radiologist", userId: "U-RAD", to: "/backlog", system: "Reporting backlog",
  },
  {
    title: "A critical result nobody acknowledges",
    detail: "A pulmonary embolism case opened when the demo started. Nobody answers, so it is re-notified and escalated to you within a minute. Record who acknowledged, how and when, then close it.",
    role: "medical_director", userId: "U-MD", to: "/critical", system: "Critical results",
  },
  {
    title: "Blind peer review",
    detail: "Sampled reports from colleagues wait for your second read. The original reader is hidden and your own reports are never assigned to you.",
    role: "radiologist", userId: "U-RAD", to: "/peer-review", system: "Peer review",
  },
  {
    title: "QA report and CT dose",
    detail: "As QA lead, see discrepancy rates by radiologist and exam type and export them. Then open CT dose: EVW-CT1 has drifted above its usual level over the last three weeks.",
    role: "medical_director", userId: "U-MD", to: "/peer-review", system: "QA · CT dose",
  },
];

const BUSINESS: Step[] = [
  {
    title: "A contrast CT finishes and stock goes down",
    detail: "Mark a contrast CT done at Lakeshore. Iohexol and the injector kit are deducted from the first-expiring lot; iohexol reaches its reorder point and a purchase order is drafted.",
    role: "technologist", userId: "U-TECH", to: "/inventory", system: "Inventory",
  },
  {
    title: "The patient rates the visit on her phone",
    detail: "Every completed exam sends a survey in the patient's language. Open one from “Surveys sent”, give one star: the site manager is alerted at once and AI labels the comment's sentiment and themes for staff to confirm.",
    role: "operations_manager", userId: "U-OPS", to: "/feedback", system: "Patient feedback",
  },
  {
    title: "Dr. Park orders online and only sees her own patients",
    detail: "Submit a requisition in the portal; it is triaged and given a protocol within seconds. Try opening patient PT-00001: refused and written to the audit log.",
    role: "referrer", userId: "U-REF", to: "/portal/new", system: "Referrer portal",
  },
  {
    title: "Referral trends and an AI weekly summary",
    detail: "Filter by specialty, modality or site. Six referrers dropped sharply and form the visit list. Draft the weekly summary: every number is filled in from the queries; click one to see its tile.",
    role: "operations_manager", userId: "U-OPS", to: "/referrals", system: "Referral analytics",
  },
  {
    title: "Billing QA work queue",
    detail: "Completed exams reconciled against claims: not submitted, duplicates, wrong codes, wrong amounts, rejections and claims for exams never performed. Work an item and export the list.",
    role: "admin", userId: "U-ADMIN", to: "/billing", system: "Billing QA",
  },
  {
    title: "Privacy officer reviews unusual access",
    detail: "Rules over the audit log caught a bulk lookup, after-hours access, a possible relative, a self-lookup, cross-site access and repeated refusals. Investigate one; every step is recorded.",
    role: "admin", userId: "U-ADMIN", to: "/phipa", system: "PHIPA monitoring",
  },
  {
    title: "Get ready for an inspection",
    detail: "The checklist shows what is out of date (an overdue scanner service, an expired BLS). Ask the policies a question: the answer quotes the policy with a link to the exact section, or says it is not covered.",
    role: "operations_manager", userId: "U-OPS", to: "/inspection", system: "Inspection hub",
  },
];

// Hospital platform (spec 6.3): roles, unit scope, break-glass, consent and signing. The hospital
// staff are generated (app/ehr/seed/platform.py); their user ids follow the practitioner ids.
const HOSPITAL: Step[] = [
  {
    title: "A Medicine A physician sees only their unit's patients",
    detail: "Open the census and a patient's chart: encounter, consent, medication orders, observations and documents. Sign the discharge summary draft: only a physician can, and only through the signing service.",
    role: "physician", userId: "U-DOC-09", to: "/hospital/patients", system: "Access · signing",
  },
  {
    title: "Break-glass for an ICU patient",
    detail: "Type the MRN of an ICU patient (the operations manager's ICU census lists them). The record is closed; open it with a reason of at least 10 characters. A red banner stays for 4 hours.",
    role: "physician", userId: "U-DOC-09", to: "/hospital/patients", system: "Break-glass",
  },
  {
    title: "The pharmacist co-signs the medication reconciliation",
    detail: "Pharmacists see medication-related records hospital-wide, no clinical notes. The med rec draft needs the pharmacist and a physician; one signature leaves it a draft.",
    role: "pharmacist", userId: "U-PHAR-01", to: "/hospital/patients", system: "Co-signature",
  },
  {
    title: "Registration records a consent change",
    detail: "The clerk sees demographics and visits without clinical content. Record a withdrawal of SMS consent: it is audited and published as consent.revoked on the event bus.",
    role: "clerk", userId: "U-CLER-01", to: "/hospital/patients", system: "Consent",
  },
  {
    title: "The administrator reviews the emergency access",
    detail: "The break-glass queue shows who opened which patient, why and what they read. Mark it justified or not; the decision goes to the hash-chained audit log, filterable by event type and patient.",
    role: "admin", userId: "U-ADMIN", to: "/break-glass-review", system: "Audit",
  },
];

function StoryCard({ title, steps, onGo, userId }: { title: string; steps: Step[]; onGo: (s: Step) => void; userId: string }) {
  return (
    <Card title={title} className="mb-4">
      <ol className="space-y-3">
        {steps.map((step, i) => (
          <li key={step.title} className="flex gap-3 rounded-lg border border-slate-100 p-3 hover:border-slate-200">
            <span className="grid size-7 shrink-0 place-items-center rounded-full bg-brand-50 text-sm font-semibold text-brand-700">{i + 1}</span>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <p className="text-sm font-semibold text-slate-900">{step.title}</p>
                <Badge tone="brand">{step.system}</Badge>
              </div>
              <p className="mt-1 text-sm text-slate-600">{step.detail}</p>
            </div>
            <button onClick={() => onGo(step)} className="flex shrink-0 items-center gap-1 self-center rounded-lg px-2 py-1 text-xs font-medium text-brand-700 hover:bg-brand-50">
              {userId === step.userId ? "Open" : `As ${ROLE_LABEL[step.role].toLowerCase()}`}
              <ArrowRight className="size-3.5" />
            </button>
          </li>
        ))}
      </ol>
    </Card>
  );
}

export function HomePage() {
  const { user, login } = useAuth();
  const navigate = useNavigate();
  if (!user) return null;

  const hospitalFirst = ["physician", "nurse", "pharmacist", "clerk"].includes(user.role);

  const go = async (step: Step) => {
    if (user.id !== step.userId) await login(step.userId);
    navigate(step.to);
  };

  return (
    <div className="mx-auto max-w-4xl">
      <PageHeader
        title={`Welcome, ${user.name.split(" ").slice(-2).join(" ")}`}
        subtitle="Follow the demo storyline below, or use the menu to explore your role's tools."
      />
      {hospitalFirst && <StoryCard title="Storyline 5 · hospital platform: roles, break-glass, consent, signing" steps={HOSPITAL} onGo={go} userId={user.id} />}
      <StoryCard title="Storyline 1 · one patient through scheduling, reporting and the front desk" steps={STORY} onGo={go} userId={user.id} />
      <StoryCard title="Storyline 2 · the requisition intake pipeline" steps={PIPELINE} onGo={go} userId={user.id} />
      <StoryCard title="Storyline 3 · radiology operations" steps={OPERATIONS} onGo={go} userId={user.id} />
      <StoryCard title="Storyline 4 · business and compliance" steps={BUSINESS} onGo={go} userId={user.id} />
      {!hospitalFirst && <StoryCard title="Storyline 5 · hospital platform: roles, break-glass, consent, signing" steps={HOSPITAL} onGo={go} userId={user.id} />}
      <p className="flex items-center gap-1.5 text-xs text-slate-500">
        <CheckCircle2 className="size-3.5" /> Use “Reset demo” in the header to start over.
      </p>
    </div>
  );
}
