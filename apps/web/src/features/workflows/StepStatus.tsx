// How a workflow step's status reads on screen (spec 6.5 UI: done / waiting for sign-off / failed and retrying):
// always an icon and a word, never colour alone.

import clsx from "clsx";
import { AlarmClock, CheckCircle2, Circle, Hourglass, Loader2, RotateCw, SkipForward, XCircle } from "lucide-react";
import type { Step, StepStatus } from "./api";

const LOOK: Record<StepStatus, { label: string; icon: typeof Circle; tone: string }> = {
  done: { label: "Done", icon: CheckCircle2, tone: "text-emerald-700 bg-emerald-50 ring-emerald-200" },
  waiting: { label: "Waiting for sign-off", icon: Hourglass, tone: "text-amber-800 bg-amber-50 ring-amber-200" },
  running: { label: "Running", icon: Loader2, tone: "text-sky-700 bg-sky-50 ring-sky-200" },
  retrying: { label: "Failed, retrying", icon: RotateCw, tone: "text-amber-800 bg-amber-50 ring-amber-300" },
  failed: { label: "Failed: needs a person", icon: XCircle, tone: "text-rose-700 bg-rose-50 ring-rose-200" },
  skipped: { label: "Skipped", icon: SkipForward, tone: "text-slate-600 bg-slate-100 ring-slate-200" },
  pending: { label: "Not started", icon: Circle, tone: "text-slate-500 bg-white ring-slate-200" },
};

export function stepLabel(step: Pick<Step, "status" | "kind" | "detail">): string {
  if (step.status === "waiting" && step.kind === "timer") return "Waiting (timer)";
  if (step.status === "waiting" && step.kind === "wait") return "Waiting (until discharge)";
  if (step.status === "skipped" && step.detail?.startsWith("Not applicable")) return "Not applicable";
  return LOOK[step.status].label;
}

export function StepStatusPill({ step, className }: { step: Pick<Step, "status" | "kind" | "detail">; className?: string }) {
  const look = LOOK[step.status];
  const Icon = step.status === "waiting" && step.kind === "timer" ? AlarmClock : look.icon;
  return (
    <span className={clsx("inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-xs font-medium ring-1 ring-inset", look.tone, className)} data-status={step.status}>
      <Icon className={clsx("size-3.5", step.status === "running" && "animate-spin")} />
      {stepLabel(step)}
    </span>
  );
}

export function StepIcon({ status }: { status: StepStatus }) {
  const look = LOOK[status];
  const Icon = look.icon;
  return (
    <span className={clsx("grid size-7 shrink-0 place-items-center rounded-full ring-1 ring-inset", look.tone)} aria-hidden>
      <Icon className={clsx("size-4", status === "running" && "animate-spin")} />
    </span>
  );
}
