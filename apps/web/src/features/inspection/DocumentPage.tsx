import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { ArrowLeft, History } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { Badge, Button, Card, ErrorState, Loading, PageHeader } from "../../components/ui";
import { api, post, put } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import type { DocVersion, InspectionDoc } from "../../lib/types";
import { DUE_LABEL, DUE_TONE, EDITORS } from "./InspectionPage";

function highlight(text: string, quote: string | null): ReactNode {
  if (!quote) return text;
  const i = text.toLowerCase().indexOf(quote.toLowerCase().trim());
  if (i < 0) return text;
  const end = i + quote.trim().length;
  return <>{text.slice(0, i)}<mark className="rounded bg-amber-200 px-0.5" data-testid="cited-quote">{text.slice(i, end)}</mark>{text.slice(end)}</>;
}

function Sections({ version, active, quote }: { version: DocVersion; active: string | null; quote: string | null }) {
  return (
    <div className="space-y-4">
      {version.sections.map((s, i) => (
        <section key={s.id} id={s.id} className={clsx("scroll-mt-20 rounded-lg p-2", s.id === active && "bg-amber-50 ring-1 ring-amber-200")} data-testid={`section-${s.id}`}>
          <h3 className="text-sm font-semibold text-slate-900">{i + 1}. {s.heading}</h3>
          <p className="mt-1 text-sm leading-relaxed text-slate-700">{s.id === active ? highlight(s.text, quote) : s.text}</p>
        </section>
      ))}
    </div>
  );
}

function Renew({ doc }: { doc: InspectionDoc }) {
  const queryClient = useQueryClient();
  const today = new Date().toISOString().slice(0, 10);
  const [form, setForm] = useState({ performed: today, due: "", result: "" });
  const save = useMutation({
    mutationFn: () => put(`/api/inspection/documents/${doc.id}/record`, form),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["inspection-doc", doc.id] }); queryClient.invalidateQueries({ queryKey: ["inspection"] }); },
  });
  return (
    <Card title={doc.kind === "credential" ? "Record a renewal" : "Record a completed test or service"}>
      <div className="grid gap-2 sm:grid-cols-3">
        <label className="text-xs text-slate-600">Done on<input type="date" value={form.performed} onChange={(e) => setForm({ ...form, performed: e.target.value })} className="mt-1 h-9 w-full rounded-lg border border-slate-300 px-2 text-sm" /></label>
        <label className="text-xs text-slate-600">Next due<input type="date" value={form.due} onChange={(e) => setForm({ ...form, due: e.target.value })} className="mt-1 h-9 w-full rounded-lg border border-slate-300 px-2 text-sm" /></label>
        <label className="text-xs text-slate-600">Result / note<input value={form.result} onChange={(e) => setForm({ ...form, result: e.target.value })} className="mt-1 h-9 w-full rounded-lg border border-slate-300 px-2 text-sm" /></label>
      </div>
      <div className="mt-2 flex justify-end"><Button size="sm" variant="primary" disabled={!form.due} loading={save.isPending} onClick={() => save.mutate()} data-testid="save-renewal">Save</Button></div>
      {save.error && <p className="mt-2 text-sm text-rose-600" role="alert">{(save.error as Error).message}</p>}
    </Card>
  );
}

function NewVersion({ doc }: { doc: InspectionDoc }) {
  const queryClient = useQueryClient();
  const current = doc.versions![0];
  const [form, setForm] = useState({ version: "", change_note: "", text: current.sections.map((s) => `# ${s.heading}\n${s.text}`).join("\n\n") });
  const save = useMutation({
    mutationFn: () => post(`/api/inspection/documents/${doc.id}/versions`, form),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["inspection-doc", doc.id] }),
  });
  return (
    <details className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <summary className="cursor-pointer text-sm font-semibold text-slate-900">Publish a new version</summary>
      <div className="mt-3 grid gap-2 sm:grid-cols-2">
        <input value={form.version} onChange={(e) => setForm({ ...form, version: e.target.value })} placeholder={`Version (current ${current.version})`} className="h-9 rounded-lg border border-slate-300 px-2 text-sm" aria-label="New version" />
        <input value={form.change_note} onChange={(e) => setForm({ ...form, change_note: e.target.value })} placeholder="What changed" className="h-9 rounded-lg border border-slate-300 px-2 text-sm" aria-label="Change note" />
      </div>
      <textarea value={form.text} onChange={(e) => setForm({ ...form, text: e.target.value })} rows={10} className="mt-2 w-full rounded-lg border border-slate-300 p-2 font-mono text-xs" aria-label="Version text" />
      <div className="mt-2 flex justify-end"><Button size="sm" variant="primary" disabled={!form.version || form.change_note.length < 3} loading={save.isPending} onClick={() => save.mutate()}>Publish</Button></div>
      {save.error && <p className="mt-2 text-sm text-rose-600" role="alert">{(save.error as Error).message}</p>}
    </details>
  );
}

export function DocumentPage() {
  const { docId } = useParams();
  const [params] = useSearchParams();
  const { user } = useAuth();
  const active = params.get("section");
  const quote = params.get("quote");
  const q = useQuery({ queryKey: ["inspection-doc", docId], queryFn: () => api<InspectionDoc>(`/api/inspection/documents/${docId}`) });
  const [shown, setShown] = useState(0);
  const d = q.data;
  useEffect(() => {
    if (d && active) document.getElementById(active)?.scrollIntoView({ block: "center" });
  }, [d, active]);
  const canEdit = user && EDITORS.includes(user.role);
  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <Link to="/inspection?tab=ask" className="inline-flex items-center gap-1 text-sm text-brand-700 hover:underline"><ArrowLeft className="size-4" /> Inspection readiness</Link>
      {q.isLoading && <Loading />}
      {q.error && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {d && (
        <>
          <PageHeader title={d.title} subtitle={`${d.category} · owner ${d.owner}${d.site_name ? ` · ${d.site_name}` : ""} · ${d.source}`}
            actions={d.status !== "none" && <Badge tone={DUE_TONE[d.status]}>{DUE_LABEL[d.status]}{d.due ? ` · ${d.kind === "policy" ? "review by" : "due"} ${d.due}` : ""}</Badge>} />
          {d.kind === "policy" && d.versions && (
            <>
              <Card title={`Version ${d.versions[shown].version} · effective ${d.versions[shown].effective}${shown > 0 ? " (superseded)" : " (current)"}`}
                actions={d.versions.length > 1 && (
                  <select value={shown} onChange={(e) => setShown(Number(e.target.value))} className="h-8 rounded-lg border border-slate-300 px-2 text-xs" aria-label="Version">
                    {d.versions.map((v, i) => <option key={v.version} value={i}>v{v.version}{i === 0 ? " (current)" : ""}</option>)}
                  </select>
                )}>
                <Sections version={d.versions[shown]} active={shown === 0 ? active : null} quote={quote} />
                <p className="mt-4 text-xs text-slate-400">Synthetic demo policy. Not clinical or legal guidance.</p>
              </Card>
              <Card title={<span className="flex items-center gap-2"><History className="size-4" /> Version history</span>} padded={false}>
                <ul className="divide-y divide-slate-100 text-sm">
                  {d.versions.map((v) => <li key={v.version} className="px-4 py-2"><span className="font-medium text-slate-800">v{v.version}</span> <span className="text-xs text-slate-500">effective {v.effective} · {v.uploaded_by}</span><p className="text-xs text-slate-600">{v.change_note}</p></li>)}
                </ul>
              </Card>
              {canEdit && <NewVersion key={d.versions[0].version} doc={d} />}
            </>
          )}
          {d.kind !== "policy" && (
            <Card title="Record">
              <dl className="grid grid-cols-2 gap-3 text-sm">
                <div><dt className="text-xs text-slate-500">Last done</dt><dd className="font-medium">{d.performed ?? "–"}</dd></div>
                <div><dt className="text-xs text-slate-500">Next due</dt><dd className="font-medium">{d.due ?? "–"}{d.days_to_due != null && ` (${d.days_to_due < 0 ? `${-d.days_to_due} days overdue` : `in ${d.days_to_due} days`})`}</dd></div>
                {d.scanner_id && <div><dt className="text-xs text-slate-500">Scanner</dt><dd>{d.scanner_id}</dd></div>}
                <div className="col-span-2"><dt className="text-xs text-slate-500">Result</dt><dd>{d.result ?? "–"}</dd></div>
              </dl>
            </Card>
          )}
          {canEdit && (d.kind === "credential" || d.kind === "equipment") && d.due && <Renew doc={d} />}
        </>
      )}
    </div>
  );
}
