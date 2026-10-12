// Small building blocks of the pharmacist workbench: severity (colour + icon + text, never colour alone), where a
// flag comes from (a rule by name, or the AI with its confidence), and "view evidence".

import clsx from "clsx";
import { AlertTriangle, BookOpen, CheckCircle2, FileSearch, Info, Loader2, OctagonAlert, ShieldCheck, Sparkles, X } from "lucide-react";
import { useEffect, useState } from "react";
import { useEvidence, type Severity } from "./api";

const SEVERITY: Record<Severity, { label: string; className: string; icon: typeof Info }> = {
  blocking: { label: "Blocking", className: "bg-rose-600 text-white", icon: OctagonAlert },
  major: { label: "Major", className: "bg-rose-50 text-rose-700 ring-1 ring-inset ring-rose-200", icon: AlertTriangle },
  moderate: { label: "Moderate", className: "bg-amber-50 text-amber-800 ring-1 ring-inset ring-amber-200", icon: AlertTriangle },
  minor: { label: "Minor", className: "bg-sky-50 text-sky-700 ring-1 ring-inset ring-sky-200", icon: Info },
  info: { label: "Info", className: "bg-slate-100 text-slate-700", icon: Info },
  none: { label: "No flags", className: "bg-emerald-50 text-emerald-700 ring-1 ring-inset ring-emerald-200", icon: CheckCircle2 },
};

export function SeverityPill({ severity, className }: { severity: Severity; className?: string }) {
  const s = SEVERITY[severity] ?? SEVERITY.info;
  return (
    <span className={clsx("inline-flex items-center gap-1 whitespace-nowrap rounded-md px-1.5 py-0.5 text-[11px] font-semibold", s.className, className)} data-testid="severity">
      <s.icon className="size-3" aria-hidden />
      {s.label}
    </span>
  );
}

/** Where a flag comes from: a deterministic rule (by name) or the AI (with its confidence). */
export function SourceChip({ source, confidence, title }: { source: string; confidence?: number | null; title?: string }) {
  const ai = source === "ai";
  return (
    <span
      title={title}
      data-testid={ai ? "source-ai" : "source-rule"}
      className={clsx(
        "inline-flex items-center gap-1 whitespace-nowrap rounded-md px-1.5 py-0.5 text-[11px] font-medium ring-1 ring-inset",
        ai ? "bg-ai-50 text-ai-700 ring-ai-100" : "bg-slate-50 text-slate-700 ring-slate-200",
      )}
    >
      {ai ? <Sparkles className="size-3" aria-hidden /> : <ShieldCheck className="size-3" aria-hidden />}
      {ai ? `AI${confidence != null ? ` · confidence ${confidence.toFixed(2)}` : ""}` : `Rule: ${source}`}
    </span>
  );
}

/** A button that lists the references behind a flag: FHIR resources read as you (your role's view) and guideline sections. */
export function ViewEvidence({ refs, label = "View evidence" }: { refs: string[]; label?: string }) {
  const [open, setOpen] = useState(false);
  if (!refs.length) return null;
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-medium text-brand-700 hover:bg-brand-50"
        data-testid="view-evidence"
      >
        <FileSearch className="size-3" aria-hidden />
        {label} ({refs.length})
      </button>
      {open && <EvidenceDialog refs={refs} onClose={() => setOpen(false)} />}
    </>
  );
}

function EvidenceDialog({ refs, onClose }: { refs: string[]; onClose: () => void }) {
  const evidence = useEvidence(refs);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/30 p-4" onClick={onClose}>
      <div
        role="dialog"
        aria-label="Evidence"
        className="max-h-[80vh] w-full max-w-xl overflow-y-auto rounded-xl bg-white shadow-2xl"
        onClick={(e) => e.stopPropagation()}
        data-testid="evidence-dialog"
      >
        <header className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
          <h2 className="text-sm font-semibold text-slate-900">Evidence</h2>
          <button onClick={onClose} className="rounded-md p-1 text-slate-500 hover:bg-slate-100" aria-label="Close">
            <X className="size-4" />
          </button>
        </header>
        <div className="space-y-2 p-4">
          {evidence.isLoading && (
            <p className="flex items-center gap-2 text-xs text-slate-500"><Loader2 className="size-3 animate-spin" />Reading the record…</p>
          )}
          {evidence.data?.evidence.map((e) => (
            <div key={e.ref} className="rounded-lg border border-slate-200 p-2.5" data-testid="evidence-entry">
              <div className="flex items-center gap-2 text-[11px] text-slate-500">
                {e.kind === "guideline" ? <BookOpen className="size-3" /> : <FileSearch className="size-3" />}
                <code>{e.ref}</code>
                {e.kind === "denied" && <span className="text-rose-700">not visible to your role</span>}
              </div>
              <p className="mt-1 text-sm text-slate-800">{e.label}</p>
              {e.text && <p className="mt-1 text-xs leading-relaxed text-slate-600">{e.text}</p>}
              {e.source && <p className="mt-1 text-[11px] italic text-slate-400">{e.source}</p>}
            </div>
          ))}
          <p className="pt-1 text-[11px] text-slate-400">FHIR references are read as you, under your role's rights; guideline sections are synthetic demo summaries, not clinical guidance.</p>
        </div>
      </div>
    </div>
  );
}
