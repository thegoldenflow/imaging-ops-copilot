import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { ArrowLeft, CalendarPlus, Check, ExternalLink, ListPlus, Pencil, ShieldCheck } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { AiBadge, Badge, Button, Card, ErrorState, Loading, UrgencyBadge } from "../../components/ui";
import { api, patch, post } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { dateTime, LANGUAGE_LABEL, pct } from "../../lib/format";
import type { RequisitionDetail, Sourced } from "../../lib/types";
import { KnowledgePanel } from "../clinical-kg/ClinicalKnowledge";
import { KG_ROLES } from "../clinical-kg/KnowledgeAnswer";
import { ContrastBadge, DaysLeft, MriBadge, RequisitionStatus } from "./badges";
import { PriorTask } from "./PriorTask";

const asList = (v: Sourced | Sourced[]) => (Array.isArray(v) ? v : [v]);

/** The requisition text with every extracted source quote highlighted. */
function HighlightedText({ detail }: { detail: RequisitionDetail }) {
  const text = detail.text;
  const ranges: { start: number; end: number; label: string; low: boolean }[] = [];
  for (const [key, value] of Object.entries(detail.extraction?.fields ?? {})) {
    for (const item of asList(value)) {
      const quote = item.source_quote?.trim();
      if (!quote) continue;
      const start = text.indexOf(quote);
      if (start < 0 || ranges.some((r) => start < r.end && start + quote.length > r.start)) continue;
      ranges.push({ start, end: start + quote.length, label: detail.field_labels[key] ?? key, low: item.confidence < detail.low_confidence_threshold });
    }
  }
  ranges.sort((a, b) => a.start - b.start);
  const parts: ReactNode[] = [];
  let pos = 0;
  ranges.forEach((r, i) => {
    if (r.start > pos) parts.push(text.slice(pos, r.start));
    parts.push(
      <mark key={i} title={`${r.label}${r.low ? " (low confidence)" : ""}`}
        className={clsx("rounded px-0.5", r.low ? "bg-amber-200 text-amber-950" : "bg-brand-100 text-brand-700")}>
        {text.slice(r.start, r.end)}
      </mark>,
    );
    pos = r.end;
  });
  parts.push(text.slice(pos));
  return <pre className="font-mono text-xs leading-6 whitespace-pre-wrap text-slate-700" data-testid="requisition-text">{parts}</pre>;
}

function FieldRow({ id, label, value, low, canEdit }: { id: string; label: string; value: Sourced | Sourced[]; low: boolean; canEdit: boolean }) {
  const queryClient = useQueryClient();
  const { reqId } = useParams();
  const [editing, setEditing] = useState(false);
  const items = asList(value).filter((v) => v.value);
  const [text, setText] = useState(items.map((i) => i.value).join("; "));
  const save = useMutation({
    mutationFn: () => patch<RequisitionDetail>(`/api/requisitions/${reqId}/fields/${id}`, { value: Array.isArray(value) ? text.split(";") : text }),
    onSuccess: (d) => { queryClient.setQueryData(["requisition", reqId], d); setEditing(false); },
  });
  const corrected = items.some((i) => i.corrected_by);
  return (
    <tr className={clsx(low && "bg-amber-50")} data-testid={`field-${id}`}>
      <td className="py-2 pr-3 align-top text-xs font-medium text-slate-500">{label}</td>
      <td className="py-2 align-top">
        {editing ? (
          <div className="flex gap-2">
            <input value={text} onChange={(e) => setText(e.target.value)} className="h-8 flex-1 rounded-lg border border-slate-300 px-2 text-sm" aria-label={`Correct ${label}`} />
            <Button size="sm" variant="primary" loading={save.isPending} onClick={() => save.mutate()}>Save</Button>
            <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>Cancel</Button>
          </div>
        ) : (
          <div className="flex items-start justify-between gap-2">
            <div className="text-sm">
              {items.length === 0 ? <span className="text-slate-400">None found</span> : items.map((i, n) => (
                <p key={n}>{i.value} <span className={clsx("text-xs", i.confidence < 0.7 ? "text-amber-700" : "text-slate-400")}>{i.corrected_by ? `corrected by ${i.corrected_by}` : pct(i.confidence)}</span></p>
              ))}
              {low && !corrected && <p className="text-xs text-amber-800">Low confidence: please confirm or correct.</p>}
            </div>
            {canEdit && <Button size="sm" variant="ghost" onClick={() => setEditing(true)} aria-label={`Edit ${label}`}><Pencil className="size-3.5" /></Button>}
          </div>
        )}
      </td>
    </tr>
  );
}

function TriageCard({ d, isRad }: { d: RequisitionDetail; isRad: boolean }) {
  const queryClient = useQueryClient();
  const [priority, setPriority] = useState("");
  const [reason, setReason] = useState("");
  const set = (r: RequisitionDetail) => queryClient.setQueryData(["requisition", d.summary.id], r);
  const confirm = useMutation({ mutationFn: () => post<RequisitionDetail>(`/api/requisitions/${d.summary.id}/triage/confirm`), onSuccess: set });
  const override = useMutation({ mutationFn: () => post<RequisitionDetail>(`/api/requisitions/${d.summary.id}/triage/override`, { priority, reason }), onSuccess: set });
  const t = d.triage!;
  return (
    <Card title={<span className="flex items-center gap-2">Triage <AiBadge label={t.ai_status === "seeded" ? "Baseline" : "AI suggestion"} agent={t.ai_status === "seeded" ? undefined : "requisition_triage"} /></span>}
      actions={<DaysLeft days={d.summary.days_left} />}>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-slate-500">AI:</span>{t.ai_priority ? <UrgencyBadge urgency={t.ai_priority} /> : <Badge tone="amber">unavailable</Badge>}
        <span className="text-slate-500">Final:</span><UrgencyBadge urgency={t.final_priority} />
        <span className="text-xs text-slate-500">target {d.targets[t.final_priority]} days</span>
      </div>
      <p className="mt-2 text-sm text-slate-700">{t.ai_rationale}</p>
      {t.ai_red_flags.length > 0 && <div className="mt-2 flex flex-wrap gap-1">{t.ai_red_flags.map((f) => <Badge key={f} tone="red">{f}</Badge>)}</div>}
      {t.review_action && (
        <p className="mt-2 text-xs text-slate-500">
          {t.review_action === "confirmed" ? "Confirmed" : "Changed"} by {t.reviewed_by}{t.override_reason ? `: “${t.override_reason}”` : ""}
        </p>
      )}
      {isRad && (
        <div className="mt-3 space-y-2 border-t border-slate-100 pt-3">
          {!t.review_action && t.ai_priority && <Button size="sm" variant="primary" loading={confirm.isPending} onClick={() => confirm.mutate()}><Check className="size-3.5" /> Confirm {t.ai_priority}</Button>}
          <div className="flex flex-wrap gap-2">
            <select value={priority} onChange={(e) => setPriority(e.target.value)} className="h-8 rounded-lg border border-slate-300 px-2 text-sm" aria-label="New priority">
              <option value="">Change priority…</option>
              {["P1", "P2", "P3", "P4"].map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
            <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Reason (required)" className="h-8 min-w-48 flex-1 rounded-lg border border-slate-300 px-2 text-sm" aria-label="Override reason" />
            <Button size="sm" disabled={!priority || reason.trim().length < 5} loading={override.isPending} onClick={() => override.mutate()}>Override</Button>
          </div>
          {(confirm.error || override.error) && <p className="text-sm text-rose-600">{((confirm.error || override.error) as Error).message}</p>}
        </div>
      )}
    </Card>
  );
}

function ProtocolCard({ d, isRad }: { d: RequisitionDetail; isRad: boolean }) {
  const queryClient = useQueryClient();
  const p = d.protocol!;
  const [choice, setChoice] = useState(p.approved_id ?? p.primary_id);
  const [reason, setReason] = useState("");
  const approve = useMutation({
    mutationFn: () => post<RequisitionDetail>(`/api/requisitions/${d.summary.id}/protocol/approve`, { protocol_id: choice, reason: reason || null }),
    onSuccess: (r) => queryClient.setQueryData(["requisition", d.summary.id], r),
  });
  const options = [p.primary, ...p.alternatives];
  return (
    <Card title={<span className="flex items-center gap-2">Protocol <AiBadge label={p.ai_status === "seeded" ? "Baseline" : "AI suggestion"} agent={p.ai_status === "seeded" ? undefined : "protocol_suggest"} /></span>}
      actions={p.approved ? <Badge tone="green"><ShieldCheck className="size-3" /> Approved by {p.approved_by}</Badge> : null}>
      <ul className="space-y-2">
        {options.map((o, i) => (
          <li key={o.id}>
            <label className={clsx("flex cursor-pointer items-start gap-2 rounded-lg border p-2 text-sm", choice === o.id ? "border-brand-500 bg-brand-50" : "border-slate-200")}>
              <input type="radio" name="protocol" className="mt-1" checked={choice === o.id} disabled={!isRad} onChange={() => setChoice(o.id)} />
              <span>
                <span className="font-medium">{o.name}</span> {i === 0 && <Badge tone="ai">AI first choice</Badge>}
                <span className="block text-xs text-slate-500">{o.minutes} min · {o.contrast ? "IV contrast" : "no contrast"} · {o.id}</span>
              </span>
            </label>
          </li>
        ))}
      </ul>
      <p className="mt-2 text-sm text-slate-600">{p.rationale}</p>
      {isRad && (
        <div className="mt-3 space-y-2 border-t border-slate-100 pt-3">
          <select value={options.some((o) => o.id === choice) ? "" : choice} onChange={(e) => e.target.value && setChoice(e.target.value)}
            className="h-8 w-full rounded-lg border border-slate-300 px-2 text-sm" aria-label="Other protocol">
            <option value="">Other protocol from the library…</option>
            {p.library.filter((l) => !options.some((o) => o.id === l.id)).map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
          </select>
          {choice !== p.primary_id && <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Why a different protocol? (optional)" className="h-8 w-full rounded-lg border border-slate-300 px-2 text-sm" />}
          <Button size="sm" variant="primary" loading={approve.isPending} onClick={() => approve.mutate()} data-testid="approve-protocol">
            <Check className="size-3.5" /> {p.approved_id ? "Update approval" : "Approve protocol"}
          </Button>
        </div>
      )}
    </Card>
  );
}

function MriCard({ d }: { d: RequisitionDetail }) {
  const queryClient = useQueryClient();
  const send = useMutation({ mutationFn: () => post<RequisitionDetail>(`/api/requisitions/${d.summary.id}/mri-screening`), onSuccess: (r) => queryClient.setQueryData(["requisition", d.summary.id], r) });
  const m = d.mri;
  return (
    <Card title="MRI safety screening" actions={m && <MriBadge status={m.status} />}>
      {!m ? (
        <div className="flex items-center justify-between gap-2 text-sm text-slate-600">
          Questionnaire not sent yet.
          <Button size="sm" loading={send.isPending} onClick={() => send.mutate()}>Send questionnaire</Button>
        </div>
      ) : (
        <div className="space-y-2 text-sm">
          {m.flags.length > 0 ? <ul className="list-disc pl-4 text-rose-700">{m.flags.map((f) => <li key={f}>{f}</li>)}</ul> : <p className="text-slate-600">{m.submitted_at ? "No safety flags in the answers." : "Waiting for the patient's answers."}</p>}
          {m.devices.length > 0 && (
            <ul className="space-y-1">{m.devices.map((dv, i) => (
              <li key={i} className="rounded-lg bg-slate-50 p-2 text-xs"><AiBadge label="AI read" agent="mri_implant_extract" /> “{dv.patient_words}” → <b>{dv.device_name}</b> · {dv.mr_status}</li>
            ))}</ul>
          )}
          {m.reviewed_by && <p className="text-xs text-slate-500">{m.review_decision === "cleared" ? "Cleared" : "Not cleared"} by {m.reviewed_by}: “{m.review_note}”</p>}
          <p className="text-xs text-slate-500">Flags never clear themselves; a technologist or radiologist reviews on the MRI safety page. Questionnaire language: {LANGUAGE_LABEL[m.language]}.</p>
          {m.token && <a href={`/mri-screening/${m.token}`} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-xs text-brand-700 underline">Open questionnaire as patient <ExternalLink className="size-3" /></a>}
        </div>
      )}
    </Card>
  );
}

function SchedulingCard({ d }: { d: RequisitionDetail }) {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const set = (r: RequisitionDetail) => { queryClient.setQueryData(["requisition", d.summary.id], r); queryClient.invalidateQueries({ queryKey: ["requisitions"] }); };
  const book = useMutation({ mutationFn: () => post<RequisitionDetail>(`/api/requisitions/${d.summary.id}/book`), onSuccess: set });
  const waitlist = useMutation({ mutationFn: () => post<RequisitionDetail>(`/api/requisitions/${d.summary.id}/waitlist`), onSuccess: set });
  const canBook = user && ["front_desk", "operations_manager", "admin"].includes(user.role);
  const a = d.appointment;
  return (
    <Card title="Scheduling">
      {a ? (
        <div className="space-y-3 text-sm">
          <p><Badge tone="green">Booked</Badge> {a.exam_name} · {dateTime(a.start)} · {a.site_name} · {a.scanner_id} ({Math.round((+new Date(a.end) - +new Date(a.start)) / 60000)} min from protocol)</p>
          <div>
            <p className="mb-1 text-xs font-semibold text-slate-500">Prior imaging retrieval</p>
            {d.priors.length === 0 ? <p className="text-xs text-slate-500">No outside priors mentioned or on file.</p> : d.priors.map((t) => <PriorTask key={t.id} task={t} />)}
          </div>
        </div>
      ) : d.waitlist ? (
        <p className="text-sm"><Badge tone="blue">On waitlist</Badge> {d.waitlist.urgency} · slot length {d.waitlist.duration_minutes} min. Booked automatically through backfill offers.</p>
      ) : d.summary.status === "approved" ? (
        canBook ? (
          <div className="flex flex-wrap gap-2">
            <Button variant="primary" loading={book.isPending} onClick={() => book.mutate()} data-testid="book-next"><CalendarPlus className="size-4" /> Book next available</Button>
            <Button loading={waitlist.isPending} onClick={() => waitlist.mutate()}><ListPlus className="size-4" /> Add to waitlist</Button>
          </div>
        ) : <p className="text-sm text-slate-500">Ready to book by the front desk.</p>
      ) : <p className="text-sm text-slate-500">A radiologist approves the protocol before booking.</p>}
      {(book.error || waitlist.error) && <p className="mt-2 text-sm text-rose-600">{((book.error || waitlist.error) as Error).message}</p>}
    </Card>
  );
}

export function RequisitionDetailPage() {
  const { reqId } = useParams();
  const { user } = useAuth();
  const q = useQuery({
    queryKey: ["requisition", reqId],
    queryFn: () => api<RequisitionDetail>(`/api/requisitions/${reqId}`),
    refetchInterval: (query) => (["received", "processing"].includes(query.state.data?.summary.status ?? "") || (query.state.data?.priors ?? []).some((p) => ["requested", "retrying"].includes(p.status)) ? 2000 : false),
  });
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const d = q.data!;
  const isRad = user?.role === "radiologist";
  const processing = ["received", "processing"].includes(d.summary.status);
  return (
    <div>
      <Link to="/requisitions" className="mb-3 inline-flex items-center gap-1 text-sm text-slate-500 hover:text-slate-800"><ArrowLeft className="size-4" /> Requisitions</Link>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold text-slate-900">{d.patient.name} · {d.summary.requested_exam ?? "New requisition"}</h1>
          <p className="text-sm text-slate-500">{d.patient.age} y {d.patient.sex} · {LANGUAGE_LABEL[d.patient.language]} · {d.summary.referrer_name} · received {dateTime(d.summary.received_at)} by {d.summary.channel}</p>
        </div>
        <div className="flex items-center gap-2"><ContrastBadge status={d.summary.contrast_status} /><RequisitionStatus status={d.summary.status} /></div>
      </div>
      {processing ? <Card><Loading label="AI is extracting, triaging and suggesting a protocol…" /></Card> : (
        <div className="grid gap-4 xl:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
          <div className="space-y-4">
            <Card title="Requisition as received" actions={<span className="flex items-center gap-2 text-xs"><mark className="rounded bg-brand-100 px-1 text-brand-700">extracted</mark><mark className="rounded bg-amber-200 px-1">low confidence</mark></span>}>
              <HighlightedText detail={d} />
            </Card>
            {d.extraction && (
              <Card title={<span className="flex items-center gap-2">Extracted fields <AiBadge label={d.extraction.ai_status === "seeded" ? "Baseline" : "AI-extracted"} agent={d.extraction.ai_status === "seeded" ? undefined : "requisition_extract"} /></span>}
                actions={<span className="text-xs text-slate-400">{d.extraction.prompt_version}</span>}>
                <table className="w-full"><tbody className="divide-y divide-slate-100">
                  {Object.entries(d.extraction.fields).map(([k, v]) => (
                    <FieldRow key={k} id={k} label={d.field_labels[k] ?? k} value={v} low={d.extraction!.low_confidence.includes(k)} canEdit={user?.role !== "referrer"} />
                  ))}
                </tbody></table>
              </Card>
            )}
          </div>
          <div className="space-y-4">
            {d.triage && <TriageCard d={d} isRad={isRad} />}
            {d.protocol && <ProtocolCard d={d} isRad={isRad} />}
            {user && KG_ROLES.includes(user.role) && (
              <KnowledgePanel requisitionId={d.summary.id}
                indication={asList(d.extraction?.fields.clinical_indication ?? []).map((v) => v.value).join(" ")} />
            )}
            {d.contrast && (
              <Card title="Contrast & kidney check" actions={<ContrastBadge status={d.contrast.status} />}>
                <ul className="list-disc space-y-1 pl-4 text-sm text-slate-700">{d.contrast.basis.map((b) => <li key={b}>{b}</li>)}</ul>
                <p className="mt-2 text-xs text-slate-500">Rules set by the medical director decide the status; AI only extracted the history. <Link to="/contrast" className="text-brand-700 underline">Thresholds</Link></p>
              </Card>
            )}
            {d.summary.modality === "MRI" && <MriCard d={d} />}
            {d.prep && (
              <Card title="Prep instructions the patient will receive">
                <p className="text-sm text-slate-700">{d.prep.text}</p>
                <p className="mt-1 text-xs text-slate-500">{LANGUAGE_LABEL[d.prep.language]} · approved text only{d.prep.note && <span className="text-amber-700"> · {d.prep.note}</span>}</p>
              </Card>
            )}
            <SchedulingCard d={d} />
          </div>
        </div>
      )}
    </div>
  );
}
