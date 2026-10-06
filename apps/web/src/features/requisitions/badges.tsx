import { AlertTriangle, CheckCircle2, Clock, ShieldAlert, ShieldCheck } from "lucide-react";
import { Badge } from "../../components/ui";

const CONTRAST: Record<string, { tone: "green" | "amber" | "red" | "blue"; label: string }> = {
  pass: { tone: "green", label: "Contrast: pass" },
  needs_egfr: { tone: "amber", label: "Needs eGFR" },
  needs_premedication: { tone: "blue", label: "Needs premedication" },
  needs_review: { tone: "red", label: "Contrast: needs review" },
};

export function ContrastBadge({ status }: { status: string | null }) {
  if (!status) return null;
  const s = CONTRAST[status] ?? { tone: "amber" as const, label: status };
  const Icon = status === "pass" ? CheckCircle2 : AlertTriangle;
  return <Badge tone={s.tone}><Icon className="size-3" />{s.label}</Badge>;
}

const MRI: Record<string, { tone: "green" | "amber" | "red" | "slate"; label: string }> = {
  sent: { tone: "slate", label: "MRI form sent" },
  flagged: { tone: "red", label: "MRI: needs review" },
  no_flags: { tone: "green", label: "MRI: no flags" },
  cleared: { tone: "green", label: "MRI: cleared" },
  not_cleared: { tone: "red", label: "MRI: not cleared" },
};

export function MriBadge({ status }: { status: string | null }) {
  if (!status) return null;
  const s = MRI[status] ?? { tone: "slate" as const, label: status };
  const Icon = status === "flagged" || status === "not_cleared" ? ShieldAlert : status === "sent" ? Clock : ShieldCheck;
  return <Badge tone={s.tone}><Icon className="size-3" />{s.label}</Badge>;
}

const REQ_STATUS: Record<string, { tone: "slate" | "amber" | "blue" | "green" | "red" | "ai"; label: string }> = {
  received: { tone: "slate", label: "Received" },
  processing: { tone: "ai", label: "AI processing" },
  ready: { tone: "amber", label: "Awaiting radiologist" },
  approved: { tone: "blue", label: "Ready to book" },
  waitlisted: { tone: "blue", label: "On waitlist" },
  booked: { tone: "green", label: "Booked" },
  failed: { tone: "red", label: "Failed" },
};

export function RequisitionStatus({ status }: { status: string }) {
  const s = REQ_STATUS[status] ?? { tone: "slate" as const, label: status };
  return <Badge tone={s.tone}>{s.label}</Badge>;
}

export function DaysLeft({ days }: { days: number | null }) {
  if (days == null) return <span className="text-slate-400">–</span>;
  const fmt = (d: number) => (d < 1 ? "<1" : d.toFixed(0));
  if (days < 0) return <span className="tabular font-semibold text-rose-600">{fmt(Math.abs(days))} d overdue</span>;
  return <span className={days < 2 ? "tabular font-medium text-amber-700" : "tabular text-slate-700"}>{fmt(days)} d left</span>;
}
