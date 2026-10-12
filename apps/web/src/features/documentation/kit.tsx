// Building blocks of the documentation screens: document status (colour + icon + text), the validator's verdict,
// an item's issues, and the source panel that highlights what a sentence cites.

import clsx from "clsx";
import { AlertTriangle, CheckCircle2, CircleDashed, FileCheck2, FileText, OctagonAlert, ShieldAlert, ShieldCheck } from "lucide-react";
import { useEffect, useRef } from "react";
import type { DocStatus, Issue, Source, Validation } from "./api";

const STATUS: Record<DocStatus, { label: string; className: string; icon: typeof FileText }> = {
  none: { label: "Not started", className: "bg-slate-100 text-slate-600", icon: CircleDashed },
  draft: { label: "Draft", className: "bg-amber-50 text-amber-800 ring-1 ring-inset ring-amber-200", icon: FileText },
  blocked: { label: "Blocked", className: "bg-rose-50 text-rose-700 ring-1 ring-inset ring-rose-200", icon: OctagonAlert },
  final: { label: "Final", className: "bg-emerald-50 text-emerald-700 ring-1 ring-inset ring-emerald-200", icon: FileCheck2 },
  "n/a": { label: "—", className: "text-slate-400", icon: CircleDashed },
};

export function DocStatusPill({ status, testId }: { status: DocStatus; testId?: string }) {
  const s = STATUS[status] ?? STATUS.none;
  return (
    <span data-testid={testId} className={clsx("inline-flex items-center gap-1 whitespace-nowrap rounded-md px-1.5 py-0.5 text-[11px] font-semibold", s.className)}>
      <s.icon className="size-3" aria-hidden />
      {s.label}
    </span>
  );
}

/** The validator's verdict for the whole document: untraceable items block signing; warnings do not. */
export function ValidationBar({ validation }: { validation: Validation }) {
  const red = new Set(validation.issues.filter((i) => i.severity === "red").map((i) => i.path)).size;
  const amber = validation.issues.filter((i) => i.severity === "amber").length;
  return (
    <div
      data-testid="validation-bar"
      className={clsx(
        "flex flex-wrap items-center gap-2 rounded-lg px-3 py-2 text-sm",
        red ? "bg-rose-50 text-rose-800 ring-1 ring-inset ring-rose-200" : "bg-emerald-50 text-emerald-800 ring-1 ring-inset ring-emerald-200",
      )}
    >
      {red ? <ShieldAlert className="size-4" aria-hidden /> : <ShieldCheck className="size-4" aria-hidden />}
      <span className="font-semibold">
        {red ? `${red} untraceable item${red === 1 ? "" : "s"}: signing is blocked` : "Every item traces to its sources"}
      </span>
      <span className="text-xs opacity-80">
        {validation.validator}: {validation.traceable} of {validation.checked} items checked traceable
        {amber ? ` · ${amber} warning${amber === 1 ? "" : "s"}` : ""}
      </span>
    </div>
  );
}

export function IssueList({ issues }: { issues: Issue[] }) {
  if (!issues.length) return null;
  return (
    <ul className="mt-1 space-y-0.5">
      {issues.map((i, n) => (
        <li
          key={n}
          data-testid={i.severity === "red" ? "issue-red" : "issue-amber"}
          className={clsx("flex items-start gap-1 text-xs", i.severity === "red" ? "text-rose-700" : "text-amber-700")}
        >
          {i.severity === "red" ? <OctagonAlert className="mt-0.5 size-3 shrink-0" aria-hidden /> : <AlertTriangle className="mt-0.5 size-3 shrink-0" aria-hidden />}
          <span>{i.message}</span>
        </li>
      ))}
    </ul>
  );
}

const KIND_LABEL: Record<string, string> = {
  encounter: "Encounter", diagnosis: "Diagnoses", procedure: "Procedures", medication: "Medications", medrec: "Discharge medication table",
  observation: "Vital signs", result: "Laboratory results", report: "Reports", plan: "Care plan", task: "Open tasks", pending: "Pending results",
  flag: "Flags",
};

/** The inputs the draft may cite, grouped by kind; the selected sentence's sources are highlighted and scrolled to. */
export function SourcePanel({ sources, highlight, picked, onPick }: {
  sources: Source[];
  highlight: string[];
  picked?: string[];
  onPick?: (ref: string) => void;
}) {
  const first = useRef<HTMLLIElement | null>(null);
  useEffect(() => {
    first.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [highlight]);
  const groups: Record<string, Source[]> = {};
  for (const s of sources) (groups[s.kind] ??= []).push(s);
  const firstOn = Object.values(groups).flat().find((s) => highlight.includes(s.ref))?.ref;
  return (
    <div className="space-y-3" data-testid="source-panel">
      {Object.entries(groups).map(([kind, items]) => (
        <section key={kind}>
          <h3 className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">{KIND_LABEL[kind] ?? kind}</h3>
          <ul className="mt-1 space-y-1">
            {items.map((s) => {
              const on = highlight.includes(s.ref);
              return (
                <li
                  key={s.ref}
                  ref={s.ref === firstOn ? first : undefined}
                  data-testid="source"
                  data-ref={s.ref}
                  data-highlighted={on ? "true" : "false"}
                  className={clsx(
                    "rounded-md border px-2 py-1 text-xs transition-colors",
                    on ? "border-brand-400 bg-brand-50 ring-2 ring-brand-200" : "border-slate-200 bg-white",
                  )}
                >
                  <div className="flex items-start justify-between gap-2">
                    <span className="text-slate-800">{s.detail || s.name}</span>
                    {onPick && (
                      <label className="flex shrink-0 items-center gap-1 text-[11px] text-slate-500">
                        <input type="checkbox" checked={picked?.includes(s.ref) ?? false} onChange={() => onPick(s.ref)} data-testid="pick-source" />
                        cite
                      </label>
                    )}
                  </div>
                  <span className="font-mono text-[10px] text-slate-400">{s.ref}</span>
                </li>
              );
            })}
          </ul>
        </section>
      ))}
      {!sources.length && <p className="text-xs text-slate-500">No sources.</p>}
    </div>
  );
}

export function TableStatus({ status }: { status: string }) {
  const ok = status === "final";
  return (
    <span className={clsx("inline-flex items-center gap-1 text-[11px]", ok ? "text-emerald-700" : "text-slate-500")}>
      {ok ? <CheckCircle2 className="size-3" aria-hidden /> : <CircleDashed className="size-3" aria-hidden />}
      {{ final: "Signed", awaiting_cosign: "Awaiting co-sign", in_review: "In review", none: "None" }[status] ?? status}
    </span>
  );
}
