import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { AlertTriangle, ArrowLeft, Check, Pencil, RotateCcw, Send, ShieldCheck, Trash2 } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { AiBadge, Badge, Button, Card, ErrorState, Loading } from "../../components/ui";
import { api, patch, post } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { dateTime, pct } from "../../lib/format";
import type { Report, Section } from "../../lib/types";
import { StudyImage } from "./StudyImage";

const SECTION_TONE = { pending: "amber", accepted: "green", edited: "blue", deleted: "slate" } as const;

function SectionEditor({ report, section, canEdit }: { report: Report; section: Section; canEdit: boolean }) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(section.final_text);
  const act = useMutation({
    mutationFn: (body: { action: string; text?: string }) => patch<Report>(`/api/reports/${report.id}/sections/${section.key}`, body),
    onSuccess: (r) => {
      queryClient.setQueryData(["report", report.id], r);
      setEditing(false);
    },
  });
  const shown = section.status === "pending" ? section.ai_text : section.final_text;

  return (
    <div className={clsx("rounded-lg border p-3", section.status === "pending" ? "border-ai-100 bg-ai-50/40" : "border-slate-200")} data-testid={`section-${section.key}`}>
      <div className="mb-1.5 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-semibold text-slate-900">{section.label}</h3>
          {section.status === "pending" && section.ai_text && <AiBadge label="AI draft" />}
          {section.status !== "pending" && <Badge tone={SECTION_TONE[section.status]}>{section.status}</Badge>}
        </div>
        {canEdit && !editing && (
          <div className="flex gap-1">
            {section.status === "pending" ? (
              <>
                <Button size="sm" variant="ghost" onClick={() => act.mutate({ action: "accept" })} disabled={!section.ai_text} aria-label={`Accept ${section.label}`}><Check className="size-3.5" />Accept</Button>
                <Button size="sm" variant="ghost" onClick={() => { setText(section.ai_text); setEditing(true); }} aria-label={`Edit ${section.label}`}><Pencil className="size-3.5" />Edit</Button>
                <Button size="sm" variant="ghost" onClick={() => act.mutate({ action: "delete" })} aria-label={`Delete ${section.label}`}><Trash2 className="size-3.5" /></Button>
              </>
            ) : (
              <Button size="sm" variant="ghost" onClick={() => act.mutate({ action: "reset" })}><RotateCcw className="size-3.5" />Undo</Button>
            )}
          </div>
        )}
      </div>
      {editing ? (
        <div className="space-y-2">
          <textarea value={text} onChange={(e) => setText(e.target.value)} rows={3} className="w-full rounded-lg border border-slate-300 p-2 text-sm" aria-label={`${section.label} text`} />
          <div className="flex gap-2">
            <Button size="sm" variant="primary" loading={act.isPending} onClick={() => act.mutate({ action: "edit", text })}>Save</Button>
            <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>Cancel</Button>
          </div>
        </div>
      ) : (
        <p className={clsx("text-sm", section.status === "deleted" ? "text-slate-400 line-through" : "text-slate-700")}>
          {section.status === "deleted" ? section.ai_text || "(empty)" : shown || <span className="text-slate-400">No AI text. Write this section yourself.</span>}
        </p>
      )}
      {section.status === "edited" && section.ai_text && (
        <p className="mt-1.5 text-xs text-slate-400">AI draft: {section.ai_text}</p>
      )}
    </div>
  );
}

export function ReportReviewPage() {
  const { reportId } = useParams();
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const report = useQuery({ queryKey: ["report", reportId], queryFn: () => api<Report>(`/api/reports/${reportId}`) });
  const [confirmed, setConfirmed] = useState<number[]>([]);
  const [levels, setLevels] = useState<Record<number, string>>({});
  const sign = useMutation({
    mutationFn: () => post<{ report: Report; critical_results: unknown[] }>(`/api/reports/${reportId}/sign`, { confirmed_urgent: confirmed, levels }),
    onSuccess: (res) => {
      queryClient.setQueryData(["report", reportId], res.report);
      queryClient.invalidateQueries({ queryKey: ["worklist"] });
    },
  });
  const send = useMutation({
    mutationFn: () => post<Report>(`/api/reports/${reportId}/send`),
    onSuccess: (r) => queryClient.setQueryData(["report", reportId], r),
  });

  if (report.isLoading) return <Loading />;
  if (report.error) return <ErrorState error={report.error} onRetry={() => report.refetch()} />;
  const r = report.data!;
  const canEdit = user?.role === "radiologist" && r.status === "draft";
  const pending = r.sections.filter((s) => s.status === "pending").length;

  return (
    <div>
      <Link to="/reading" className="mb-3 inline-flex items-center gap-1 text-sm text-slate-500 hover:text-slate-800"><ArrowLeft className="size-4" /> Worklist</Link>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold text-slate-900">{r.study.patient_name} · {r.study.exam_name}</h1>
          <p className="text-sm text-slate-500">{r.study.patient_age} y {r.study.patient_sex} · {dateTime(r.study.performed_at)} · Indication: {r.study.indication} · Referrer: {r.study.referrer_name}</p>
        </div>
        {r.status === "signed" ? (
          <Badge tone="green"><ShieldCheck className="size-3.5" /> Signed by {r.signed_by} · {dateTime(r.signed_at!)}</Badge>
        ) : (
          <Badge tone="amber">Draft — not visible to referrer</Badge>
        )}
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
        <div className="space-y-3">
          <StudyImage studyId={r.study_id} />
          {r.image_quality && (
            <p className="text-xs text-slate-500"><span className="font-medium">Image quality (AI):</span> {r.image_quality.adequate ? "Adequate" : "Limited"} — {r.image_quality.notes}</p>
          )}
          {r.uncertainties.length > 0 && (
            <Card title={<span className="flex items-center gap-2">Uncertainties <AiBadge /></span>}>
              <ul className="list-disc space-y-1 pl-4 text-sm text-slate-700">{r.uncertainties.map((u) => <li key={u}>{u}</li>)}</ul>
            </Card>
          )}
        </div>

        <div className="space-y-3">
          <div className="rounded-lg border border-ai-100 bg-ai-50 px-3 py-2 text-xs text-ai-700">
            <strong>AI-generated preliminary draft for radiologist review only. Not a diagnosis.</strong>{" "}
            Model {r.model} · prompt {r.prompt_version} · {r.llm_mode === "mock" ? "mock output" : "Claude API"}
          </div>
          {r.ai_status !== "ok" && (
            <div className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800" role="alert">
              <AlertTriangle className="mt-0.5 size-4 shrink-0" />
              AI draft {r.ai_status === "unavailable" ? "is temporarily unavailable" : "needs human review"}. You can write the report manually.
            </div>
          )}

          {r.sections.map((s) => <SectionEditor key={`${s.key}-${s.status}`} report={r} section={s} canEdit={canEdit} />)}

          {r.urgent_findings.length > 0 && (
            <Card title={<span className="flex items-center gap-2"><AlertTriangle className="size-4 text-rose-600" /> Findings that may need urgent communication</span>}>
              <ul className="space-y-2">
                {r.urgent_findings.map((f, i) => (
                  <li key={i} className="flex items-start gap-2 text-sm">
                    {r.status === "draft" ? (
                      <input type="checkbox" className="mt-1" checked={confirmed.includes(i)} disabled={!canEdit} aria-label={`Confirm ${f.finding}`}
                        onChange={(e) => setConfirmed((p) => (e.target.checked ? [...p, i] : p.filter((x) => x !== i)))} />
                    ) : (
                      <Badge tone={f.confirmed ? "red" : "slate"}>{f.confirmed ? "Confirmed" : "Not confirmed"}</Badge>
                    )}
                    <div>
                      <p className="font-medium text-slate-900">{f.finding} <AiBadge label="AI flagged" /></p>
                      <p className="text-xs text-slate-500">{f.reason}</p>
                      {r.status === "draft" && confirmed.includes(i) && (
                        <select value={levels[i] ?? "urgent"} onChange={(e) => setLevels({ ...levels, [i]: e.target.value })}
                          className="mt-1 h-7 rounded-md border border-slate-300 px-1.5 text-xs" aria-label={`Level for ${f.finding}`}>
                          <option value="critical">Critical (Level 1)</option>
                          <option value="urgent">Urgent (Level 2)</option>
                          <option value="significant">Significant unexpected (Level 3)</option>
                        </select>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
              {r.status === "draft" && <p className="mt-2 text-xs text-slate-500">Confirmed findings open a case in the Critical Results Tracker when you sign.</p>}
            </Card>
          )}

          <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-slate-200 bg-white px-4 py-3">
            {r.status === "draft" ? (
              <>
                <p className="text-sm text-slate-600">{pending ? `${pending} section${pending > 1 ? "s" : ""} still to review` : "All sections reviewed"}</p>
                {canEdit && <Button variant="primary" disabled={pending > 0} loading={sign.isPending} onClick={() => sign.mutate()}><ShieldCheck className="size-4" /> Sign report</Button>}
              </>
            ) : (
              <>
                <p className="text-sm text-slate-600">Radiologist changed {pct(r.edit_ratio ?? 0)} of the AI draft.{r.sent_to_referrer_at && ` Sent to referrer ${dateTime(r.sent_to_referrer_at)}.`}</p>
                {!r.sent_to_referrer_at && <Button variant="primary" loading={send.isPending} onClick={() => send.mutate()}><Send className="size-4" /> Send to referring physician</Button>}
              </>
            )}
          </div>
          {(sign.error || send.error) && <p className="text-sm text-rose-600" role="alert">{((sign.error || send.error) as Error).message}</p>}
          {sign.data && sign.data.critical_results.length > 0 && (
            <p className="text-sm text-rose-700">Critical result case opened for {sign.data.critical_results.length} finding(s); the referrer will be notified.</p>
          )}
        </div>
      </div>
    </div>
  );
}
