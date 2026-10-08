import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { AlertTriangle, BookOpenCheck, CheckCircle2, FileText, MessageSquareQuote, Search, Upload, XCircle } from "lucide-react";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { AiBadge, Badge, Button, Card, EmptyState, ErrorState, Loading, PageHeader, Stat, Tabs } from "../../components/ui";
import { api, post } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { dateTime } from "../../lib/format";
import type { InspectionDoc, InspectionOverview, QaCitation, QaEntry } from "../../lib/types";

type Tab = "readiness" | "ask" | "documents";
export const EDITORS = ["operations_manager", "medical_director", "admin"];
export const DUE_TONE = { ok: "green", upcoming: "blue", due_soon: "amber", overdue: "red", none: "slate" } as const;
export const DUE_LABEL = { ok: "Current", upcoming: "Due within 60 days", due_soon: "Due within 30 days", overdue: "Overdue", none: "No due date" };
const KIND_LABEL = { policy: "Policies", equipment: "Equipment records", credential: "Staff credentials", qc_record: "Quality records" };

export function citationLink(c: { doc_id: string; chunk_id: string; quote?: string }) {
  const params = new URLSearchParams({ section: c.chunk_id, ...(c.quote ? { quote: c.quote } : {}) });
  return `/inspection/documents/${c.doc_id}?${params}`;
}

function Readiness({ d }: { d: InspectionOverview }) {
  const queryClient = useQueryClient();
  const ack = useMutation({ mutationFn: (id: string) => post(`/api/inspection/reminders/${id}/ack`), onSuccess: () => queryClient.invalidateQueries({ queryKey: ["inspection"] }) });
  return (
    <div className="grid gap-4 lg:grid-cols-5">
      <Card title="Inspection checklist" className="lg:col-span-3" padded={false}>
        <ul className="divide-y divide-slate-100" data-testid="checklist">
          {d.checklist.map((c) => (
            <li key={c.key} className="flex gap-3 px-4 py-3 text-sm" data-testid={`check-${c.key.replaceAll(" ", "-").replace("/", "")}`}>
              {c.ok ? <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-emerald-600" /> : <XCircle className="mt-0.5 size-4 shrink-0 text-rose-600" />}
              <div className="min-w-0">
                <p className="font-medium text-slate-800">{c.label}</p>
                <p className={clsx("text-xs", c.ok ? "text-slate-500" : "text-rose-700")}>{c.detail}</p>
                {c.evidence.length > 0 && (
                  <p className="mt-1 flex flex-wrap gap-1">{c.evidence.slice(0, 6).map((id) => <Link key={id} to={`/inspection/documents/${id}`} className="rounded bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-700 hover:bg-slate-200">{id}</Link>)}</p>
                )}
              </div>
            </li>
          ))}
        </ul>
      </Card>
      <div className="space-y-4 lg:col-span-2">
        <Card title="Coming due" padded={false}>
          {d.due.length === 0 ? <EmptyState title="Nothing due in the next 60 days" /> : (
            <ul className="divide-y divide-slate-100">
              {d.due.map((doc) => (
                <li key={doc.id} className="px-4 py-2 text-sm">
                  <Link to={`/inspection/documents/${doc.id}`} className="flex items-center justify-between gap-2 hover:underline">
                    <span className="text-slate-800">{doc.title}</span>
                    <Badge tone={DUE_TONE[doc.status]}>{doc.days_to_due! < 0 ? `${-doc.days_to_due!} d overdue` : `${doc.days_to_due} d`}</Badge>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card title="Reminders sent" padded={false}>
          {d.reminders.length === 0 ? <EmptyState title="No reminders yet" /> : (
            <ul className="divide-y divide-slate-100" data-testid="reminders">
              {d.reminders.map((r) => (
                <li key={r.id} className="flex items-start justify-between gap-2 px-4 py-2 text-sm" data-testid={`reminder-${r.doc_id}-${r.stage}`}>
                  <div>
                    <p className="text-slate-800">{r.text}</p>
                    <p className="text-xs text-slate-500">{r.stage === 0 ? "Overdue notice" : `${r.stage}-day reminder`} to {r.to} · {dateTime(r.sent_at)}</p>
                  </div>
                  {r.acknowledged_by ? <Badge tone="green">Seen</Badge> : <Button size="sm" variant="ghost" onClick={() => ack.mutate(r.id)}>Mark seen</Button>}
                </li>
              ))}
            </ul>
          )}
          <p className="px-4 py-2 text-xs text-slate-500">Reminders go out {d.stages.filter((s) => s > 0).join(", ")} days before a credential or equipment test is due, and again when it is overdue.</p>
        </Card>
      </div>
    </div>
  );
}

function CitationChip({ c, n }: { c: QaCitation; n: number }) {
  return (
    <Link to={citationLink(c)} className="inline-flex items-center gap-1 rounded-md bg-brand-50 px-1.5 py-0.5 text-xs font-medium text-brand-700 hover:bg-brand-100" data-testid="citation">
      [{n}] {c.title} v{c.version} · {c.heading}
    </Link>
  );
}

function Answer({ e }: { e: QaEntry }) {
  return (
    <div className="space-y-2 rounded-lg border border-slate-200 p-3" data-testid={`qa-${e.id}`}>
      <p className="text-sm font-medium text-slate-900">{e.question}</p>
      {e.found ? (
        <>
          <div className="flex items-start gap-2"><AiBadge label="AI answer from your documents" /></div>
          <p className="text-sm text-slate-800">{e.answer}</p>
          <ol className="space-y-1.5">
            {e.citations.map((c, i) => (
              <li key={i} className="text-xs">
                <CitationChip c={c} n={i + 1} />
                <blockquote className="mt-1 border-l-2 border-brand-200 pl-2 text-slate-600">“{c.quote}”</blockquote>
              </li>
            ))}
          </ol>
        </>
      ) : (
        <div className="rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-900" data-testid="not-found">
          <p className="flex items-start gap-2"><AlertTriangle className="mt-0.5 size-4 shrink-0" />{e.answer}</p>
          {e.suggested && e.suggested.length > 0 && (
            <p className="mt-1 flex flex-wrap gap-1 text-xs">Closest sections: {e.suggested.map((s) => <Link key={s.chunk_id} to={citationLink(s)} className="underline">{s.title} · {s.heading}</Link>)}</p>
          )}
        </div>
      )}
      <p className="text-xs text-slate-400">{e.asked_by} · {dateTime(e.asked_at)}{e.model ? ` · ${e.model} · ${e.prompt_version}` : " · no matching section, AI not called"}</p>
    </div>
  );
}

function Ask() {
  const queryClient = useQueryClient();
  const [question, setQuestion] = useState("");
  const history = useQuery({ queryKey: ["inspection-qa"], queryFn: () => api<{ entries: QaEntry[] }>("/api/inspection/qa") });
  const ask = useMutation({
    mutationFn: () => post<QaEntry>("/api/inspection/ask", { question }),
    onSuccess: () => { setQuestion(""); queryClient.invalidateQueries({ queryKey: ["inspection-qa"] }); },
  });
  const examples = ["How long must an outpatient stay after a contrast injection?", "Can staff look up their own results?", "What is the staff parking fee?"];
  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <Card title={<span className="flex items-center gap-2"><MessageSquareQuote className="size-4 text-ai-600" /> Ask the policies</span>}>
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (question.trim().length >= 3) ask.mutate(); }}>
          <input value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="e.g. Who reviews contrast requests when the eGFR is low?" className="h-10 flex-1 rounded-lg border border-slate-300 px-3 text-sm" aria-label="Question" />
          <Button type="submit" variant="ai" loading={ask.isPending} disabled={question.trim().length < 3} data-testid="ask"><Search className="size-4" /> Ask</Button>
        </form>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {examples.map((x) => <button key={x} type="button" onClick={() => setQuestion(x)} className="rounded-full border border-slate-200 px-2.5 py-0.5 text-xs text-slate-600 hover:bg-slate-50">{x}</button>)}
        </div>
        <p className="mt-2 text-xs text-slate-500">Answers come only from the uploaded documents, with a quote and a link for every statement. If the documents do not cover a question, it says so. Always check the cited section.</p>
        {ask.error && <p className="mt-2 text-sm text-rose-600" role="alert">{(ask.error as Error).message}</p>}
      </Card>
      {history.isLoading && <Loading />}
      {history.error && <ErrorState error={history.error} onRetry={() => history.refetch()} />}
      <div className="space-y-3">{history.data?.entries.map((e) => <Answer key={e.id} e={e} />)}</div>
    </div>
  );
}

function UploadForm({ onDone }: { onDone: () => void }) {
  const queryClient = useQueryClient();
  const [form, setForm] = useState({ title: "", owner: "", version: "1.0", text: "" });
  const upload = useMutation({
    mutationFn: () => post<InspectionDoc>("/api/inspection/documents", form),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["inspection-docs"] }); onDone(); },
  });
  const readFile = (f: File) => f.text().then((text) => setForm((x) => ({ ...x, text, title: x.title || f.name.replace(/\.(md|txt)$/i, "") })));
  return (
    <Card title="Upload a policy" className="mb-4">
      <div className="grid gap-2 sm:grid-cols-3">
        <input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} placeholder="Title" className="h-9 rounded-lg border border-slate-300 px-2 text-sm" aria-label="Title" />
        <input value={form.owner} onChange={(e) => setForm({ ...form, owner: e.target.value })} placeholder="Owner" className="h-9 rounded-lg border border-slate-300 px-2 text-sm" aria-label="Owner" />
        <input value={form.version} onChange={(e) => setForm({ ...form, version: e.target.value })} placeholder="Version" className="h-9 rounded-lg border border-slate-300 px-2 text-sm" aria-label="Version" />
      </div>
      <textarea value={form.text} onChange={(e) => setForm({ ...form, text: e.target.value })} rows={8} className="mt-2 w-full rounded-lg border border-slate-300 p-2 font-mono text-xs" aria-label="Policy text"
        placeholder={"# Section heading\nSection text...\n\n# Next section\n..."} />
      <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
        <label className="text-xs text-slate-600">Or load a .md / .txt file <input type="file" accept=".md,.txt,text/plain,text/markdown" onChange={(e) => e.target.files?.[0] && readFile(e.target.files[0])} className="text-xs" /></label>
        <Button variant="primary" size="sm" loading={upload.isPending} disabled={form.title.length < 3 || form.owner.length < 2 || form.text.length < 20} onClick={() => upload.mutate()} data-testid="upload-policy"><Upload className="size-4" /> Upload</Button>
      </div>
      {upload.error && <p className="mt-2 text-sm text-rose-600" role="alert">{(upload.error as Error).message}</p>}
    </Card>
  );
}

function Documents() {
  const { user } = useAuth();
  const [kind, setKind] = useState("policy");
  const [uploading, setUploading] = useState(false);
  const q = useQuery({ queryKey: ["inspection-docs", kind], queryFn: () => api<{ documents: InspectionDoc[] }>(`/api/inspection/documents?kind=${kind}`) });
  return (
    <>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap gap-2">
          {Object.entries(KIND_LABEL).map(([k, v]) => (
            <button key={k} onClick={() => setKind(k)} className={clsx("rounded-full border px-3 py-1 text-xs font-medium", kind === k ? "border-brand-600 bg-brand-50 text-brand-700" : "border-slate-300 text-slate-600")}>{v}</button>
          ))}
        </div>
        {user && EDITORS.includes(user.role) && kind === "policy" && <Button size="sm" onClick={() => setUploading(!uploading)}><Upload className="size-4" /> Upload policy</Button>}
      </div>
      {uploading && <UploadForm onDone={() => setUploading(false)} />}
      <Card padded={false}>
        {q.isLoading && <Loading />}
        {q.error && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
        {q.data?.documents.length === 0 && <EmptyState title="No documents" />}
        <ul className="divide-y divide-slate-100">
          {q.data?.documents.map((d) => (
            <li key={d.id}>
              <Link to={`/inspection/documents/${d.id}`} className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5 text-sm hover:bg-slate-50" data-testid={`doc-${d.id}`}>
                <span className="flex items-center gap-2"><FileText className="size-4 text-slate-400" /><span className="font-medium text-slate-800">{d.title}</span>
                  <span className="text-xs text-slate-500">{d.category}{d.version ? ` · v${d.version}` : ""}{d.site_id ? ` · ${d.site_id}` : ""} · {d.owner}</span></span>
                <span className="flex items-center gap-2 text-xs text-slate-500">
                  {d.due && <span>{d.kind === "policy" ? "review by" : "due"} {d.due}</span>}
                  {d.status !== "none" && <Badge tone={DUE_TONE[d.status]}>{DUE_LABEL[d.status]}</Badge>}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      </Card>
    </>
  );
}

export function InspectionPage() {
  const [params, setParams] = useSearchParams();
  const tab = (params.get("tab") as Tab) || "readiness";
  const q = useQuery({ queryKey: ["inspection"], queryFn: () => api<InspectionOverview>("/api/inspection/overview"), refetchInterval: 5000 });
  const d = q.data;
  const failing = d?.checklist.filter((c) => !c.ok).length ?? 0;
  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader title="Inspection readiness" subtitle="Policies, equipment records, staff credentials and quality records in one place." />
      {q.isLoading && <Loading />}
      {q.error && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {d && (
        <>
          <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat label="Checklist items in order" value={<span data-testid="kpi-checklist">{d.checklist.length - failing} / {d.checklist.length}</span>} tone={failing ? "amber" : "green"} />
            <Stat label="Overdue items" value={d.due.filter((x) => x.status === "overdue").length} tone={d.due.some((x) => x.status === "overdue") ? "red" : "green"} />
            <Stat label="Policies" value={d.counts.policy} hint={`${d.counts.qc_record} quality records`} />
            <Stat label="Credentials and equipment records" value={d.counts.credential + d.counts.equipment} />
          </div>
          <Tabs value={tab} onChange={(t) => setParams({ tab: t })} tabs={[
            { id: "readiness", label: <span className="flex items-center gap-1.5"><BookOpenCheck className="size-4" /> Readiness</span> },
            { id: "ask", label: <span className="flex items-center gap-1.5"><MessageSquareQuote className="size-4" /> Ask the policies</span> },
            { id: "documents", label: <span className="flex items-center gap-1.5"><FileText className="size-4" /> Documents</span> },
          ]} />
          {tab === "readiness" && <Readiness d={d} />}
          {tab === "ask" && <Ask />}
          {tab === "documents" && <Documents />}
        </>
      )}
    </div>
  );
}
