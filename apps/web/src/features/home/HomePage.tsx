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
    title: "Referring physician sees the signed report",
    detail: "Dr. Helen Park can read the report only after it is signed. Drafts return 403.",
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

export function HomePage() {
  const { user, login } = useAuth();
  const navigate = useNavigate();
  if (!user) return null;

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
      <Card title="Demo storyline · one patient through three systems">
        <ol className="space-y-3">
          {STORY.map((step, i) => (
            <li key={step.title} className="flex gap-3 rounded-lg border border-slate-100 p-3 hover:border-slate-200">
              <span className="grid size-7 shrink-0 place-items-center rounded-full bg-brand-50 text-sm font-semibold text-brand-700">
                {i + 1}
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="text-sm font-semibold text-slate-900">{step.title}</p>
                  <Badge tone="brand">{step.system}</Badge>
                </div>
                <p className="mt-1 text-sm text-slate-600">{step.detail}</p>
              </div>
              <button
                onClick={() => go(step)}
                className="flex shrink-0 items-center gap-1 self-center rounded-lg px-2 py-1 text-xs font-medium text-brand-700 hover:bg-brand-50"
              >
                {user.id === step.userId ? "Open" : `As ${ROLE_LABEL[step.role].toLowerCase()}`}
                <ArrowRight className="size-3.5" />
              </button>
            </li>
          ))}
        </ol>
        <p className="mt-4 flex items-center gap-1.5 text-xs text-slate-500">
          <CheckCircle2 className="size-3.5" /> Use “Reset demo” in the header to start over.
        </p>
      </Card>
    </div>
  );
}
