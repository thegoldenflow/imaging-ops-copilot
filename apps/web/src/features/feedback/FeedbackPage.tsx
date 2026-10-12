import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { BellRing, ExternalLink, Star } from "lucide-react";
import { useState } from "react";
import { LineChart } from "../../components/charts";
import { AiBadge, Badge, Button, Card, EmptyState, ErrorState, Loading, PageHeader, Stat, Tabs } from "../../components/ui";
import { api, post } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { dateTime, LANGUAGE_LABEL, pct } from "../../lib/format";
import type { FeedbackOverview, FeedbackResponse, SurveyRow } from "../../lib/types";

const SENT_TONE = { positive: "green", neutral: "slate", negative: "red" } as const;
const MANAGERS = ["operations_manager", "medical_director", "admin"];

function Stars({ n }: { n: number }) {
  return (
    <span className="inline-flex" aria-label={`${n} of 5 stars`}>
      {[1, 2, 3, 4, 5].map((i) => <Star key={i} className={clsx("size-3.5", i <= n ? "fill-amber-400 text-amber-400" : "text-slate-300")} />)}
    </span>
  );
}

function ResponseRow({ r, themes }: { r: FeedbackResponse; themes: Record<string, string> }) {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [sentiment, setSentiment] = useState(r.sentiment ?? "neutral");
  const [picked, setPicked] = useState<string[]>(r.themes);
  const confirm = useMutation({
    mutationFn: (body: { sentiment: string; themes: string[] }) => post(`/api/feedback/responses/${r.id}/confirm`, body),
    onSuccess: () => { setEditing(false); queryClient.invalidateQueries({ queryKey: ["feedback"] }); },
  });
  const canConfirm = user && MANAGERS.includes(user.role);
  return (
    <li className="px-4 py-3 text-sm" data-testid={`fb-${r.id}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <Stars n={r.rating} />
          <span className="font-medium text-slate-800">{r.patient_name}</span>
          <span className="text-xs text-slate-500">{r.exam_name} · {r.site_name} · {LANGUAGE_LABEL[r.language]} · {dateTime(r.submitted_at)}</span>
        </div>
        <div className="flex flex-wrap items-center gap-1">
          {r.sentiment && <Badge tone={SENT_TONE[r.sentiment as keyof typeof SENT_TONE]}>{r.sentiment}</Badge>}
          {r.themes.map((t) => <Badge key={t} tone="blue">{themes[t]}</Badge>)}
          {r.ai_status === null && <Badge tone="ai">AI labelling…</Badge>}
          {r.ai_status === "unavailable" && <Badge tone="amber">AI unavailable, label by hand</Badge>}
          {r.confirmed_by ? <Badge tone="green">Confirmed by {r.confirmed_by}</Badge> : r.ai_status && ["ok", "seeded"].includes(r.ai_status) && <AiBadge label={r.ai_status === "seeded" ? "Baseline label" : "AI label"} agent={r.ai_status === "seeded" ? undefined : "feedback_classify"} />}
        </div>
      </div>
      {r.comment && <p className="mt-1 text-slate-700">“{r.comment}”</p>}
      {r.ai_summary && r.language !== "en" && <p className="mt-0.5 text-xs text-ai-700">AI summary in English: {r.ai_summary}</p>}
      {canConfirm && !editing && r.ai_status !== null && (
        <div className="mt-1.5 flex gap-2">
          {!r.confirmed_by && r.sentiment && <Button size="sm" variant="ghost" loading={confirm.isPending} onClick={() => confirm.mutate({ sentiment: r.sentiment!, themes: r.themes })} data-testid={`confirm-${r.id}`}>Confirm labels</Button>}
          <Button size="sm" variant="ghost" onClick={() => setEditing(true)}>Correct</Button>
        </div>
      )}
      {editing && (
        <div className="mt-2 space-y-2 rounded-lg border border-slate-200 p-2">
          <select value={sentiment} onChange={(e) => setSentiment(e.target.value)} className="h-8 rounded-lg border border-slate-300 px-2 text-xs" aria-label="Sentiment">
            {["positive", "neutral", "negative"].map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
          <div className="flex flex-wrap gap-2">
            {Object.entries(themes).map(([k, v]) => (
              <label key={k} className="flex items-center gap-1 text-xs"><input type="checkbox" checked={picked.includes(k)} onChange={(e) => setPicked(e.target.checked ? [...picked, k] : picked.filter((x) => x !== k))} />{v}</label>
            ))}
          </div>
          <div className="flex gap-2"><Button size="sm" variant="primary" loading={confirm.isPending} onClick={() => confirm.mutate({ sentiment, themes: picked })}>Save</Button><Button size="sm" variant="ghost" onClick={() => setEditing(false)}>Cancel</Button></div>
        </div>
      )}
    </li>
  );
}

function Alerts({ data }: { data: FeedbackOverview }) {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const [notes, setNotes] = useState<Record<string, string>>({});
  const follow = useMutation({
    mutationFn: (id: string) => post(`/api/feedback/alerts/${id}/follow-up`, { note: notes[id] ?? "" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["feedback"] }),
  });
  const open = data.alerts.filter((a) => a.status === "open");
  return (
    <Card className="mb-4" padded={false} title={<span className="flex items-center gap-2"><BellRing className="size-4 text-rose-600" /> Negative feedback alerts ({open.length} open)</span>}>
      {open.length === 0 ? <EmptyState title="No open alerts" hint="Low ratings and negative comments notify the site manager here and by email." /> : (
        <ul className="divide-y divide-slate-100" data-testid="feedback-alerts">
          {open.map((a) => (
            <li key={a.id} className="space-y-1.5 px-4 py-3 text-sm" data-testid={`fb-alert-${a.response_id}`}>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="flex items-center gap-2"><Stars n={a.response.rating} /><span className="font-medium text-slate-800">{a.response.site_name}</span><Badge tone="red">{a.reason}</Badge></span>
                <span className="text-xs text-slate-500">{dateTime(a.created_at)} · notified {a.notified}</span>
              </div>
              <p className="text-slate-700">“{a.response.comment || "(no comment)"}”</p>
              {user && MANAGERS.includes(user.role) && (
                <div className="flex gap-2">
                  <input value={notes[a.id] ?? ""} onChange={(e) => setNotes({ ...notes, [a.id]: e.target.value })} placeholder="Follow-up (e.g. called the patient)" className="h-8 flex-1 rounded-lg border border-slate-300 px-2 text-xs" aria-label="Follow-up note" />
                  <Button size="sm" variant="primary" disabled={(notes[a.id] ?? "").trim().length < 5} loading={follow.isPending && follow.variables === a.id} onClick={() => follow.mutate(a.id)}>Record follow-up</Button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function Surveys() {
  const q = useQuery({ queryKey: ["feedback-surveys"], queryFn: () => api<{ surveys: SurveyRow[] }>("/api/feedback/surveys"), refetchInterval: 5000 });
  return (
    <Card padded={false} title="Surveys sent (newest first)">
      {q.isLoading && <Loading />}
      {q.error && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      <ul className="divide-y divide-slate-100">
        {q.data?.surveys.map((s) => (
          <li key={s.id} className="flex flex-wrap items-center justify-between gap-2 px-4 py-2 text-sm" data-testid={`survey-${s.id}`}>
            <span><span className="font-medium text-slate-800">{s.patient_name}</span> <span className="text-xs text-slate-500">{s.exam_name} · {s.site_id} · {LANGUAGE_LABEL[s.language]} · sent {dateTime(s.sent_at)}</span></span>
            {s.status === "answered" ? <Badge tone="green">Answered</Badge> : (
              <a href={s.link} target="_blank" rel="noreferrer" className="flex items-center gap-1 text-xs font-medium text-brand-700 hover:underline" data-testid={`survey-link-${s.id}`}>Open as patient (demo) <ExternalLink className="size-3" /></a>
            )}
          </li>
        ))}
      </ul>
      <p className="px-4 py-2 text-xs text-slate-500">Each completed exam sends a survey by SMS in the patient's language (mock SMS; see the outbox).</p>
    </Card>
  );
}

export function FeedbackPage() {
  const [site, setSite] = useState("");
  const [sentiment, setSentiment] = useState("");
  const [theme, setTheme] = useState("");
  const [tab, setTab] = useState<"responses" | "trends" | "surveys">("responses");
  const params = new URLSearchParams(Object.entries({ site_id: site, sentiment, theme }).filter(([, v]) => v)).toString();
  const q = useQuery({ queryKey: ["feedback", params], queryFn: () => api<FeedbackOverview>(`/api/feedback/overview${params ? `?${params}` : ""}`), refetchInterval: 4000 });
  const d = q.data;
  const select = "h-9 rounded-lg border border-slate-300 bg-white px-2 text-sm";
  const maxTheme = Math.max(1, ...(d?.theme_counts.map((t) => t.positive + t.neutral + t.negative) ?? [1]));
  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader title="Patient feedback" subtitle="Post-exam surveys in four languages. AI suggests sentiment and themes; staff confirm them." />
      {q.isLoading && <Loading />}
      {q.error && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {d && (
        <>
          <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat label="Responses (30 days)" value={d.kpis.responses_30d} />
            <Stat label="Average rating (30 days)" value={d.kpis.avg_rating_30d ?? "–"} hint="out of 5" />
            <Stat label="Open negative alerts" value={<span data-testid="kpi-alerts">{d.kpis.open_alerts}</span>} tone={d.kpis.open_alerts ? "red" : "green"} />
            <Stat label="AI labels to confirm" value={d.kpis.to_confirm} />
          </div>
          <Alerts data={d} />
          <Card className="mb-4" padded={false} title="By site (last 30 days)">
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="bg-slate-50 text-xs text-slate-500"><tr>{["Site", "Responses", "Average rating", "Negative", "Top complaint"].map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}</tr></thead>
                <tbody className="divide-y divide-slate-100">
                  {d.by_site.map((s) => (
                    <tr key={s.site_id} data-testid={`fb-site-${s.site_id}`}>
                      <td className="px-3 py-2 font-medium text-slate-800">{s.name}</td>
                      <td className="tabular px-3 py-2">{s.responses_30d}</td>
                      <td className={clsx("tabular px-3 py-2 font-semibold", s.avg_rating_30d != null && s.avg_rating_30d < 3.5 ? "text-rose-600" : "text-slate-900")}>{s.avg_rating_30d ?? "–"}</td>
                      <td className="tabular px-3 py-2">{s.negative_share_30d == null ? "–" : pct(s.negative_share_30d)}</td>
                      <td className="px-3 py-2 text-xs">{s.top_complaint ? d.themes[s.top_complaint] : "–"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
          <Tabs value={tab} onChange={setTab} tabs={[{ id: "responses", label: "Responses" }, { id: "trends", label: "Trends" }, { id: "surveys", label: "Surveys sent" }]} />
          {tab === "trends" && (
            <div className="grid gap-4 md:grid-cols-2">
              <Card title="Average rating per week, by site"><LineChart labels={d.labels} series={d.rating_trend} label="Average rating per week" format={(v) => v.toFixed(1)} /></Card>
              <Card title="Mentions per week, by theme"><LineChart labels={d.labels} series={d.theme_trend} label="Theme mentions per week" format={(v) => String(Math.round(v))} /></Card>
              <Card title="Themes in the last 30 days" className="md:col-span-2">
                <ul className="space-y-1.5">
                  {d.theme_counts.map((t) => (
                    <li key={t.theme} className="flex items-center gap-3 text-sm">
                      <span className="w-36 shrink-0 text-slate-700">{t.label}</span>
                      <span className="flex h-3 flex-1 overflow-hidden rounded bg-slate-100">
                        <span className="bg-emerald-500" style={{ width: `${(t.positive / maxTheme) * 100}%` }} title={`${t.positive} positive`} />
                        <span className="bg-slate-400" style={{ width: `${(t.neutral / maxTheme) * 100}%` }} title={`${t.neutral} neutral`} />
                        <span className="bg-rose-500" style={{ width: `${(t.negative / maxTheme) * 100}%` }} title={`${t.negative} negative`} />
                      </span>
                      <span className="tabular w-24 text-right text-xs text-slate-500">{t.positive} / {t.neutral} / {t.negative}</span>
                    </li>
                  ))}
                </ul>
                <p className="mt-2 text-xs text-slate-500">Positive / neutral / negative mentions. Labels are AI suggestions until confirmed.</p>
              </Card>
            </div>
          )}
          {tab === "surveys" && <Surveys />}
          {tab === "responses" && (
            <Card padded={false} title="Responses" actions={
              <div className="flex flex-wrap gap-2">
                <select value={site} onChange={(e) => setSite(e.target.value)} className={select} aria-label="Site"><option value="">All sites</option>{d.sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select>
                <select value={sentiment} onChange={(e) => setSentiment(e.target.value)} className={select} aria-label="Sentiment filter"><option value="">Any sentiment</option>{["positive", "neutral", "negative"].map((s) => <option key={s} value={s}>{s}</option>)}</select>
                <select value={theme} onChange={(e) => setTheme(e.target.value)} className={select} aria-label="Theme"><option value="">Any theme</option>{Object.entries(d.themes).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
              </div>
            }>
              {d.responses.length === 0 ? <EmptyState title="No responses match" /> : (
                <ul className="divide-y divide-slate-100">{d.responses.map((r) => <ResponseRow key={r.id + (r.confirmed_by ?? "") + r.ai_status} r={r} themes={d.themes} />)}</ul>
              )}
            </Card>
          )}
        </>
      )}
    </div>
  );
}
