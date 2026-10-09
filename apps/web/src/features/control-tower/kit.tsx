// Themed building blocks of the Control Tower, after the warehouse control tower's patterns: a status pill is
// colour + icon + text (never colour alone), a headline bar puts a severity bar beside one sentence, a drawer is a
// 420 px right panel that Esc closes, and every panel has skeleton, empty and error states.

import clsx from "clsx";
import { AlertTriangle, CheckCircle2, CircleDot, Clock, Inbox, Info, OctagonAlert, X } from "lucide-react";
import { useEffect, type ReactNode } from "react";
import type { Severity } from "./api";

export const SEVERITY_STYLE: Record<Severity, { label: string; text: string; soft: string; bar: string; icon: typeof Info }> = {
  high: { label: "High", text: "text-ct-critical", soft: "bg-ct-critical-soft", bar: "bg-ct-critical", icon: OctagonAlert },
  med: { label: "Medium", text: "text-ct-warning", soft: "bg-ct-warning-soft", bar: "bg-ct-warning", icon: AlertTriangle },
  low: { label: "Low", text: "text-ct-info", soft: "bg-ct-info-soft", bar: "bg-ct-info", icon: Info },
};

export function SeverityPill({ severity, className }: { severity: Severity; className?: string }) {
  const s = SEVERITY_STYLE[severity];
  return (
    <span className={clsx("inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-semibold", s.soft, s.text, className)}>
      <s.icon className="size-3" aria-hidden />
      {s.label}
    </span>
  );
}

type Tone = "critical" | "warning" | "ok" | "info" | "muted" | "ai";
const TONE: Record<Tone, string> = {
  critical: "bg-ct-critical-soft text-ct-critical",
  warning: "bg-ct-warning-soft text-ct-warning",
  ok: "bg-ct-ok-soft text-ct-ok",
  info: "bg-ct-info-soft text-ct-info",
  muted: "bg-ct-raised text-ct-muted",
  ai: "bg-ct-ai-soft text-ct-ai",
};
const TONE_ICON: Record<Tone, typeof Info> = {
  critical: OctagonAlert, warning: AlertTriangle, ok: CheckCircle2, info: Info, muted: CircleDot, ai: CircleDot,
};

export function StatusPill({ tone, children, icon, className, title }: { tone: Tone; children: ReactNode; icon?: typeof Info | null; className?: string; title?: string }) {
  const Icon = icon === undefined ? TONE_ICON[tone] : icon;
  return (
    <span title={title} className={clsx("inline-flex items-center gap-1 whitespace-nowrap rounded-md px-1.5 py-0.5 text-[11px] font-medium", TONE[tone], className)}>
      {Icon && <Icon className="size-3" aria-hidden />}
      {children}
    </span>
  );
}

export function Panel({ title, icon, actions, children, className, testId }: {
  title: ReactNode;
  icon?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  testId?: string;
}) {
  return (
    <section className={clsx("flex min-h-0 flex-col rounded-xl border border-ct-border bg-ct-surface", className)} data-testid={testId}>
      <header className="flex items-center justify-between gap-2 border-b border-ct-border px-3 py-2">
        <h2 className="flex items-center gap-2 text-sm font-semibold text-ct-text">
          {icon}
          {title}
        </h2>
        {actions}
      </header>
      <div className="min-h-0 flex-1">{children}</div>
    </section>
  );
}

export function KpiTile({ label, value, hint, tone, testId, title }: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: "critical" | "warning" | "ok";
  testId?: string;
  title?: string;
}) {
  return (
    <div className="min-w-0 rounded-lg border border-ct-border bg-ct-raised px-2.5 py-2" data-testid={testId} title={title}>
      <p className="truncate text-[11px] font-medium text-ct-muted">{label}</p>
      <p
        className={clsx("tabular mt-0.5 text-xl font-semibold leading-tight", {
          "text-ct-critical": tone === "critical",
          "text-ct-warning": tone === "warning",
          "text-ct-ok": tone === "ok",
          "text-ct-text": !tone,
        })}
      >
        {value}
      </p>
      {hint && <p className="mt-0.5 truncate text-[11px] text-ct-muted">{hint}</p>}
    </div>
  );
}

/** A severity bar beside one sentence (the most important thing on the screen right now). */
export function HeadlineBar({ severity, children }: { severity: Severity | null; children: ReactNode }) {
  return (
    <div className="flex items-stretch gap-3 rounded-xl border border-ct-border bg-ct-surface px-3 py-2" data-testid="headline">
      <span className={clsx("w-1.5 shrink-0 rounded-full", severity ? SEVERITY_STYLE[severity].bar : "bg-ct-ok")} aria-hidden />
      <p className="text-sm text-ct-text">{children}</p>
    </div>
  );
}

export function SkeletonRows({ rows = 5 }: { rows?: number }) {
  return (
    <div className="space-y-2 p-3" role="status" aria-label="Loading">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="ct-skeleton h-7 rounded-md" />
      ))}
    </div>
  );
}

export function EmptyPanel({ title, hint, icon }: { title: string; hint?: string; icon?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-1 px-4 py-8 text-center">
      <div className="mb-1 text-ct-muted">{icon ?? <Inbox className="size-7" />}</div>
      <p className="text-sm font-medium text-ct-text">{title}</p>
      {hint && <p className="max-w-xs text-xs text-ct-muted">{hint}</p>}
    </div>
  );
}

export function ErrorPanel({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  return (
    <div className="flex flex-col items-center gap-2 px-4 py-8 text-center" role="alert">
      <AlertTriangle className="size-6 text-ct-critical" />
      <p className="text-sm text-ct-text">{error instanceof Error ? error.message : "Something went wrong"}</p>
      {onRetry && (
        <button onClick={onRetry} className="rounded-md border border-ct-border px-2.5 py-1 text-xs text-ct-text hover:bg-ct-raised">
          Try again
        </button>
      )}
    </div>
  );
}

/** A 420 px panel on the right; Esc or the close button closes it. */
export function Drawer({ open, onClose, title, children, testId, width = 420 }: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  children: ReactNode;
  testId?: string;
  width?: number;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <aside
      role="dialog"
      aria-label={typeof title === "string" ? title : "Details"}
      className="fixed inset-y-0 right-0 z-40 flex max-w-full flex-col border-l border-ct-border bg-ct-surface shadow-2xl"
      style={{ width }}
      data-testid={testId}
    >
      <header className="flex items-start justify-between gap-2 border-b border-ct-border px-4 py-3">
        <div className="min-w-0 text-sm font-semibold text-ct-text">{title}</div>
        <button onClick={onClose} className="rounded-md p-1 text-ct-muted hover:bg-ct-raised hover:text-ct-text" aria-label="Close">
          <X className="size-4" />
        </button>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
    </aside>
  );
}

export function Minutes({ value, warnAt, alertAt }: { value: number | null; warnAt?: number; alertAt?: number }) {
  if (value == null) return <span className="text-ct-muted">–</span>;
  const tone = alertAt != null && value >= alertAt ? "text-ct-critical" : warnAt != null && value >= warnAt ? "text-ct-warning" : "text-ct-text";
  return (
    <span className={clsx("tabular inline-flex items-center gap-1", tone)}>
      <Clock className="size-3 opacity-70" aria-hidden />
      {value >= 120 ? `${Math.floor(value / 60)}h${String(value % 60).padStart(2, "0")}` : `${value}m`}
    </span>
  );
}
