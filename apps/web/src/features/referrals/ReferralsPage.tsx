import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { CheckCircle2, Sparkles, TrendingDown } from "lucide-react";
import { useState } from "react";
import { LineChart } from "../../components/charts";
import { AiBadge, Badge, Button, Card, EmptyState, ErrorState, Loading, PageHeader, Stat } from "../../components/ui";
import { api, post, put } from "../../lib/api";
import { dateTime } from "../../lib/format";
import type { Breakdown, ReferralOverview, ReferrerTrend, SummarySegment, WeeklySummary } from "../../lib/types";

interface Filters { specialty: string; modality: string; site_id: string; referrer_id: string }
const NO_FILTERS: Filters = { specialty: "", modality: "", site_id: "", referrer_id: "" };

function flash(tile: string) {
  const el = document.querySelector<HTMLElement>(`[data-testid="${tile}"]`);
  if (!el) return;
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  el.classList.add("ring-2", "ring-ai-600", "bg-ai-50");
  setTimeout(() => el.classList.remove("ring-2", "ring-ai-600", "bg-ai-50"), 1800);
}

function Segments({ segments, onFact }: { segments: SummarySegment[]; onFact: (tile: string) => void }) {
  return (
    <>
      {segments.map((s, i) => "fact" in s ? (
        <button key={i} type="button" onClick={() => onFact(s.tile)} title={`${s.label} — click to see it on the dashboard`}
          className="rounded bg-ai-50 px-1 font-semibold text-ai-700 underline decoration-dotted underline-offset-2 hover:bg-ai-100" data-fact={s.fact} data-tile={s.tile}>
          {s.value}
        </button>
      ) : <span key={i}>{s.text}</span>)}
    </>
  );
}

function SummaryCard({ onFact }: { onFact: (tile: string) => void }) {
  const queryClient = useQueryClient();
  const q = useQuery({ queryKey: ["referral-summary"], queryFn: () => api<{ summary: WeeklySummary | null; week_start: string }>("/api/referrals/summary") });
  const done = (data: unknown) => queryClient.setQueryData(["referral-summary"], data);
  const generate = useMutation({ mutationFn: () => post<{ summary: WeeklySummary; week_start: string }>("/api/referrals/summary"), onSuccess: done });
  const approve = useMutation({ mutationFn: () => post<{ summary: WeeklySummary; week_start: string }>("/api/referrals/summary/approve"), onSuccess: done });
  const s = q.data?.summary;
  return (
    <Card className="mb-4" title={<span className="flex items-center gap-2"><Sparkles className="size-4 text-ai-600" /> Weekly summary</span>}
      actions={<>
        {s && <AiBadge label={s.status === "approved" ? "AI-drafted, approved" : "AI draft"} agent="referral_weekly_summary" />}
        <Button size="sm" variant="ai" loading={generate.isPending} onClick={() => generate.mutate()} data-testid="generate-summary">{s ? "Redraft" : "Draft this week's summary"}</Button>
      </>}>
      {q.isLoading && <Loading />}
      {q.error && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.data && !s && <EmptyState title="No summary for the last full week yet" hint="Claude drafts a short summary from the dashboard numbers. Every number is filled in from the queries, never written by the model." />}
      {s && s.ai_status !== "ok" && (
        <p className="rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800" role="alert">
          {s.ai_status === "unavailable" ? "AI is temporarily unavailable. The dashboard below is unaffected." : `The draft was rejected and needs a person: ${s.error}`}
        </p>
      )}
      {s && s.ai_status === "ok" && (
        <div data-testid="weekly-summary">
          <p className="text-base font-semibold text-slate-900"><Segments segments={s.headline} onFact={onFact} /></p>
          <div className="mt-2 space-y-1.5 text-sm leading-relaxed text-slate-700">
            {s.sentences.map((seg, i) => <p key={i}><Segments segments={seg} onFact={onFact} /></p>)}
          </div>
          <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-slate-100 pt-3 text-xs text-slate-500">
            <span>Highlighted values are filled in from the dashboard queries; click one to see it on the dashboard. Drafted {dateTime(s.generated_at)} for {s.generated_by} · {s.model} · {s.prompt_version}</span>
            {s.status === "approved" ? (
              <Badge tone="green"><CheckCircle2 className="size-3.5" /> Approved by {s.approved_by}</Badge>
            ) : (
              <Button size="sm" variant="primary" loading={approve.isPending} onClick={() => approve.mutate()} data-testid="approve-summary">Approve for distribution</Button>
            )}
          </div>
        </div>
      )}
      {(generate.error || approve.error) && <p className="mt-2 text-sm text-rose-600" role="alert">{((generate.error ?? approve.error) as Error).message}</p>}
    </Card>
  );
}

function BreakdownCard({ title, rows, prefix, onPick }: { title: string; rows: Breakdown[]; prefix: string; onPick?: (key: string) => void }) {
  return (
    <Card title={title} padded={false}>
      <table className="w-full text-left text-sm">
        <thead className="bg-slate-50 text-xs text-slate-500"><tr><th className="px-3 py-1.5 font-medium" /><th className="px-3 py-1.5 text-right font-medium">Last week</th><th className="px-3 py-1.5 text-right font-medium">12 weeks</th></tr></thead>
        <tbody className="divide-y divide-slate-100">
          {rows.map((b) => (
            <tr key={b.key} data-testid={`${prefix}-${b.key}`} className={clsx("transition-colors", onPick && "cursor-pointer hover:bg-slate-50")} onClick={() => onPick?.(b.key)}>
              <td className="px-3 py-1.5">{b.name ?? b.key}</td>
              <td className="tabular px-3 py-1.5 text-right font-medium" data-testid={`${prefix}-${b.key}-week`}>{b.last_week}</td>
              <td className="tabular px-3 py-1.5 text-right text-slate-500">{b.total}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

function Spark({ values }: { values: number[] }) {
  const max = Math.max(...values, 1);
  const pts = values.map((v, i) => `${(i / (values.length - 1)) * 80},${22 - (v / max) * 20}`).join(" ");
  return <svg viewBox="0 0 80 24" className="h-5 w-20" aria-hidden><polyline points={pts} fill="none" stroke="#14868a" strokeWidth={1.5} /></svg>;
}

function VisitList({ rows }: { rows: ReferrerTrend[] }) {
  const queryClient = useQueryClient();
  const plan = useMutation({
    mutationFn: ({ id, status }: { id: string; status: string }) => put(`/api/referrals/visits/${id}`, { status }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["referrals"] }),
  });
  return (
    <Card padded={false} className="mb-4" title={<span className="flex items-center gap-2"><TrendingDown className="size-4 text-rose-600" /> Visit list · referrers with a marked drop</span>}>
      {rows.length === 0 ? <EmptyState title="No referrer is markedly below their usual volume" /> : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm" data-testid="visit-list">
            <thead className="bg-slate-50 text-xs text-slate-500"><tr>{["Referrer", "Usual / week", "Last 4 weeks / week", "Change", "Trend", "Contact", ""].map((h, i) => <th key={i} className="px-3 py-2 font-medium">{h}</th>)}</tr></thead>
            <tbody className="divide-y divide-slate-100">
              {rows.map((r) => (
                <tr key={r.referrer_id} data-testid={`visit-${r.referrer_id}`} className="transition-colors">
                  <td className="px-3 py-2"><p className="font-medium text-slate-800">{r.name} {r.is_key && <Badge tone="brand">Key</Badge>}</p><p className="text-xs text-slate-500">{r.specialty} · {r.clinic}</p></td>
                  <td className="tabular px-3 py-2">{r.baseline_per_week}</td>
                  <td className="tabular px-3 py-2">{r.recent_per_week}</td>
                  <td className="tabular px-3 py-2 font-semibold text-rose-600" data-testid={`visit-${r.referrer_id}-change`}>{r.change_label}</td>
                  <td className="px-3 py-2"><Spark values={r.series} /></td>
                  <td className="px-3 py-2 text-xs">{r.phone}<p className="text-slate-500">last referral {r.last_referral}</p></td>
                  <td className="px-3 py-2 text-right whitespace-nowrap">
                    {r.visit?.status === "visited" ? <Badge tone="green">Visited</Badge> : r.visit?.status === "planned" ? (
                      <Button size="sm" onClick={() => plan.mutate({ id: r.referrer_id, status: "visited" })}>Mark visited</Button>
                    ) : (
                      <Button size="sm" variant="primary" onClick={() => plan.mutate({ id: r.referrer_id, status: "planned" })} data-testid={`plan-${r.referrer_id}`}>Plan visit</Button>
                    )}
                    {r.visit && <p className="mt-0.5 text-xs text-slate-500">{r.visit.status} by {r.visit.by}</p>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

export function ReferralsPage() {
  const [filters, setFilters] = useState<Filters>(NO_FILTERS);
  const [byModality, setByModality] = useState(false);
  const qs = new URLSearchParams(Object.entries(filters).filter(([, v]) => v)).toString();
  const q = useQuery({ queryKey: ["referrals", qs], queryFn: () => api<ReferralOverview>(`/api/referrals/overview${qs ? `?${qs}` : ""}`) });
  const d = q.data;
  const filtered = qs.length > 0;
  const onFact = (tile: string) => {
    if (filtered) {
      setFilters(NO_FILTERS);  // the summary covers all referrals
      setTimeout(() => flash(tile), 600);
    } else flash(tile);
  };
  const set = (k: keyof Filters) => (e: React.ChangeEvent<HTMLSelectElement>) => setFilters({ ...filters, [k]: e.target.value });
  const select = "h-9 rounded-lg border border-slate-300 bg-white px-2 text-sm";
  const pickedReferrer = d?.referrers.find((r) => r.referrer_id === filters.referrer_id);
  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader title="Referral analytics" subtitle="Referred exams by referrer, specialty, exam type, site and week (last 12 full weeks)." />
      <div className="mb-4 flex flex-wrap items-center gap-2" aria-label="Filters">
        <select value={filters.specialty} onChange={set("specialty")} className={select} aria-label="Specialty">
          <option value="">All specialties</option>
          {d?.specialties.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <select value={filters.modality} onChange={set("modality")} className={select} aria-label="Modality">
          <option value="">All modalities</option>
          {["CT", "MRI", "US", "XR"].map((m) => <option key={m} value={m}>{m}</option>)}
        </select>
        <select value={filters.site_id} onChange={set("site_id")} className={select} aria-label="Site">
          <option value="">All sites</option>
          {d?.sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
        {filters.referrer_id && <Badge tone="brand">Referrer: {pickedReferrer?.name ?? filters.referrer_id}</Badge>}
        {filtered && <Button size="sm" variant="ghost" onClick={() => setFilters(NO_FILTERS)}>Clear filters</Button>}
      </div>
      <SummaryCard onFact={onFact} />
      {q.isLoading && <Loading />}
      {q.error && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {d && (
        <>
          <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-5">
            <div data-testid="kpi-week-tile" className="rounded-xl transition-colors"><Stat label="Last full week" value={<span data-testid="kpi-last-week" className="rounded transition-colors">{d.kpis.last_week}</span>} hint={<span data-testid="kpi-week">{d.kpis.week_label}</span>} /></div>
            <Stat label="Week before" value={<span data-testid="kpi-prior-week" className="rounded transition-colors">{d.kpis.prior_week}</span>} />
            <Stat label="Change" value={<span data-testid="kpi-change" className="rounded transition-colors">{d.kpis.week_change_label}</span>} tone={d.kpis.week_change != null && d.kpis.week_change < -0.1 ? "red" : undefined} />
            <Stat label="Weekly average (12 weeks)" value={<span data-testid="kpi-avg" className="rounded transition-colors">{d.kpis.avg_per_week}</span>} hint={`${d.kpis.total} in total`} />
            <Stat label="Referrers with a marked drop" value={<span data-testid="kpi-declining" className="rounded transition-colors">{d.kpis.declining}</span>} tone={d.kpis.declining ? "red" : "green"} hint={`of ${d.kpis.active_referrers} active`} />
          </div>
          <Card className="mb-4" title="Referrals per week" actions={
            <label className="flex items-center gap-1.5 text-xs text-slate-600"><input type="checkbox" checked={byModality} onChange={(e) => setByModality(e.target.checked)} /> By modality</label>
          }>
            {d.kpis.total === 0 ? <EmptyState title="No referrals match these filters" /> : (
              <LineChart labels={d.labels} series={byModality ? d.series_by_modality : d.series} label="Referrals per week" format={(v) => String(Math.round(v))} />
            )}
          </Card>
          <div className="mb-4 grid gap-4 md:grid-cols-3">
            <BreakdownCard title="By modality" rows={d.by_modality} prefix="mod" onPick={(m) => setFilters({ ...filters, modality: m })} />
            <BreakdownCard title="By site" rows={d.by_site} prefix="site" onPick={(s) => setFilters({ ...filters, site_id: s })} />
            <BreakdownCard title="By specialty" rows={d.by_specialty} prefix="spec" onPick={(s) => setFilters({ ...filters, specialty: s })} />
          </div>
          <VisitList rows={d.visit_list} />
          <Card padded={false} title="Referrers (most referrals last week first)">
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="bg-slate-50 text-xs text-slate-500"><tr>{["Referrer", "Specialty", "Last week", "12 weeks", "Change vs usual", "Trend"].map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}</tr></thead>
                <tbody className="divide-y divide-slate-100">
                  {[...d.referrers].sort((a, b) => b.last_week - a.last_week || b.total - a.total).slice(0, 25).map((r) => (
                    <tr key={r.referrer_id} data-testid={`ref-${r.referrer_id}`} className="cursor-pointer transition-colors hover:bg-slate-50" onClick={() => setFilters({ ...filters, referrer_id: r.referrer_id })}>
                      <td className="px-3 py-2 font-medium text-slate-800">{r.name} {r.is_key && <Badge tone="brand">Key</Badge>}</td>
                      <td className="px-3 py-2 text-xs">{r.specialty}</td>
                      <td className="tabular px-3 py-2 font-medium" data-testid={`ref-${r.referrer_id}-week`}>{r.last_week}</td>
                      <td className="tabular px-3 py-2">{r.total}</td>
                      <td className={clsx("tabular px-3 py-2", r.declining ? "font-semibold text-rose-600" : "text-slate-600")}>{r.change_label}</td>
                      <td className="px-3 py-2"><Spark values={r.series} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="px-4 py-2 text-xs text-slate-500">
              A drop means the last {d.thresholds.recent_weeks} weeks averaged at least {Math.round(d.thresholds.decline * 100)}% below the previous {d.thresholds.baseline_weeks} weeks, for referrers usually sending {d.thresholds.min_baseline_per_week}+ per week. Click a row to filter by that referrer.
            </p>
          </Card>
        </>
      )}
    </div>
  );
}
