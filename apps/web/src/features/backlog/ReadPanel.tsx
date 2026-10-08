import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { X } from "lucide-react";
import { useEffect, useState } from "react";
import { Badge, Button, ErrorState, Loading, UrgencyBadge } from "../../components/ui";
import { api, post } from "../../lib/api";
import { dateTime } from "../../lib/format";
import type { BacklogStudyDetail } from "../../lib/types";

const LEVELS = [
  { id: "critical", label: "Critical (Level 1)" },
  { id: "urgent", label: "Urgent (Level 2)" },
  { id: "significant", label: "Significant unexpected (Level 3)" },
];

/** Dictate and sign a report for a study that has no AI draft. */
export function ReadPanel({ studyId, onClose }: { studyId: string; onClose: () => void }) {
  const queryClient = useQueryClient();
  const q = useQuery({ queryKey: ["backlog-study", studyId], queryFn: () => api<BacklogStudyDetail>(`/api/backlog/studies/${studyId}`) });
  const [findings, setFindings] = useState("");
  const [impression, setImpression] = useState("");
  const [critical, setCritical] = useState("");
  const [level, setLevel] = useState("urgent");
  useEffect(() => {
    if (q.data) {
      setFindings(q.data.template.findings);
      setImpression(q.data.template.impression);
    }
  }, [q.data]);
  const sign = useMutation({
    mutationFn: () => post<{ report_id: string; critical_results: unknown[] }>(`/api/backlog/studies/${studyId}/sign`, {
      findings, impression, critical_finding: critical || null, critical_level: level,
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["backlog"] });
      onClose();
    },
  });
  const d = q.data;
  return (
    <div className="fixed inset-0 z-20 flex justify-end bg-slate-900/30" onClick={onClose}>
      <aside className="flex h-full w-full max-w-lg flex-col bg-white shadow-xl" onClick={(e) => e.stopPropagation()} aria-label="Read study" data-testid="read-panel">
        <header className="flex items-start justify-between gap-3 border-b border-slate-100 px-5 py-4">
          <div>
            <p className="text-sm font-semibold text-slate-900">{d ? `${d.patient_name} · ${d.exam_name}` : "Study"}</p>
            {d && <p className="text-xs text-slate-500">{d.patient_age} {d.patient_sex} · {d.site_name} · completed {dateTime(d.performed_at)} · {d.referrer_name}</p>}
          </div>
          <button onClick={onClose} className="rounded-md p-1 text-slate-500 hover:bg-slate-100" aria-label="Close"><X className="size-4" /></button>
        </header>
        <div className="flex-1 space-y-4 overflow-y-auto px-5 py-4">
          {q.isLoading && <Loading />}
          {q.error && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
          {d && (
            <>
              <div className="flex flex-wrap gap-2">
                <UrgencyBadge urgency={d.priority} />
                <Badge tone={d.state === "overdue" ? "red" : d.state === "at_risk" ? "amber" : "green"}>
                  {d.state === "overdue" ? `Overdue by ${(-d.remaining_h).toFixed(1)} h` : `${d.remaining_h.toFixed(1)} h to target`}
                </Badge>
                <Badge>{d.modality}</Badge>
              </div>
              <div className="rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-600">
                Images are not part of this demo for {d.modality} studies. The text below is the clinic's normal-report template, not AI output; edit it before signing.
              </div>
              <label className="block text-sm">
                <span className="font-medium text-slate-700">Findings</span>
                <textarea value={findings} onChange={(e) => setFindings(e.target.value)} rows={5} className="mt-1 w-full rounded-lg border border-slate-300 p-2 text-sm" aria-label="Findings" />
              </label>
              <label className="block text-sm">
                <span className="font-medium text-slate-700">Impression</span>
                <textarea value={impression} onChange={(e) => setImpression(e.target.value)} rows={3} className="mt-1 w-full rounded-lg border border-slate-300 p-2 text-sm" aria-label="Impression" />
              </label>
              <fieldset className="rounded-lg border border-rose-200 p-3">
                <legend className="px-1 text-xs font-semibold text-rose-700">Finding that needs communication (optional)</legend>
                <input value={critical} onChange={(e) => setCritical(e.target.value)} placeholder="e.g. Acute pulmonary embolism, right lower lobe"
                  className="h-9 w-full rounded-lg border border-slate-300 px-2 text-sm" aria-label="Critical finding" />
                <select value={level} onChange={(e) => setLevel(e.target.value)} className="mt-2 h-9 w-full rounded-lg border border-slate-300 px-2 text-sm" aria-label="Critical level">
                  {LEVELS.map((l) => <option key={l.id} value={l.id}>{l.label}</option>)}
                </select>
                <p className="mt-1 text-xs text-slate-500">Signing opens a case in the critical results tracker; the ordering physician is notified automatically.</p>
              </fieldset>
              {d.assignment_history.length > 0 && (
                <div className="text-xs text-slate-500">
                  <p className="font-medium text-slate-600">Assignment history</p>
                  <ul className="mt-1 space-y-0.5">
                    {d.assignment_history.map((h, i) => <li key={i}>{dateTime(h.ts)} · {h.by === "auto" ? "Auto-assigned" : `Assigned by ${h.by}`}: {h.reason}</li>)}
                  </ul>
                </div>
              )}
            </>
          )}
          {sign.error && <p className="text-sm text-rose-600" role="alert">{(sign.error as Error).message}</p>}
        </div>
        <footer className="flex justify-end gap-2 border-t border-slate-100 px-5 py-3">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!findings.trim() || !impression.trim()} loading={sign.isPending} onClick={() => sign.mutate()} data-testid="sign-dictated">Sign report</Button>
        </footer>
      </aside>
    </div>
  );
}
