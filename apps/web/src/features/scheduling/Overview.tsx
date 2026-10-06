import { useQuery } from "@tanstack/react-query";
import { ArrowRight, MapPin } from "lucide-react";
import { useState } from "react";
import { Badge, Card, EmptyState, ErrorState, Loading, Stat, Tabs, UrgencyBadge } from "../../components/ui";
import { api } from "../../lib/api";
import { hours, pct } from "../../lib/format";
import type { Dashboard, UtilRow } from "../../lib/types";

// Sequential ramp (one hue, light -> dark) for utilization magnitude.
const RAMP = ["#eef7f7", "#cfe9e9", "#a3d6d6", "#6dbcbd", "#3a9fa2", "#14808a", "#0c585b"];
const rampColor = (v: number) => RAMP[Math.min(RAMP.length - 1, Math.floor(v * RAMP.length))];

function UtilizationBars({ rows }: { rows: UtilRow[] }) {
  return (
    <ul className="space-y-2.5">
      {rows.map((r) => (
        <li key={r.site_id} className="grid grid-cols-[9rem_1fr_3.5rem] items-center gap-3 text-sm">
          <span className="truncate text-slate-700" title={r.name}>{r.name.replace(" Imaging Centre", "")}</span>
          <div
            className="h-3 rounded bg-slate-100"
            title={`${r.name}: ${pct(r.utilization)} · booked ${hours(r.booked_min)} of ${hours(r.open_min)}, idle ${hours(r.idle_min)}`}
          >
            <div className="h-3 rounded bg-brand-500" style={{ width: `${Math.min(r.utilization, 1) * 100}%` }} />
          </div>
          <span className="tabular text-right font-medium text-slate-900">{pct(r.utilization)}</span>
        </li>
      ))}
    </ul>
  );
}

function Heatmap({ data, siteNames }: { data: Dashboard["heatmap"]; siteNames: Record<string, string> }) {
  const [hover, setHover] = useState<string | null>(null);
  if (data.length === 0) return <EmptyState title="No sites open today" />;
  const hoursList = [...new Set(data.map((d) => d.hour))].sort((a, b) => a - b);
  const sites = [...new Set(data.map((d) => d.site_id))];
  const cell = new Map(data.map((d) => [`${d.site_id}-${d.hour}`, d.utilization]));
  return (
    <div>
      <div className="overflow-x-auto">
        <table className="border-separate border-spacing-[2px] text-xs" aria-label="Utilization by site and hour">
          <thead>
            <tr>
              <th />
              {hoursList.map((h) => (
                <th key={h} className="tabular w-8 font-normal text-slate-500">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sites.map((s) => (
              <tr key={s}>
                <th className="pr-2 text-left font-normal whitespace-nowrap text-slate-600">{(siteNames[s] ?? s).replace(" Imaging Centre", "")}</th>
                {hoursList.map((h) => {
                  const v = cell.get(`${s}-${h}`);
                  const key = `${s}-${h}`;
                  return (
                    <td
                      key={h}
                      className="h-7 w-8 rounded-[4px]"
                      style={{ background: v === undefined ? "transparent" : rampColor(v), outline: hover === key ? "2px solid #0f172a" : undefined }}
                      onMouseEnter={() => setHover(key)}
                      onMouseLeave={() => setHover(null)}
                      title={v === undefined ? "Closed" : `${siteNames[s]} ${h}:00–${h + 1}:00 · ${pct(v)} booked`}
                    />
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="mt-3 flex items-center gap-2 text-xs text-slate-500">
        <span>0%</span>
        <div className="flex">{RAMP.map((c) => <span key={c} className="h-2.5 w-6" style={{ background: c }} />)}</div>
        <span>100% booked</span>
      </div>
    </div>
  );
}

interface CrossSite {
  window_days: number;
  sites: { site_id: string; name: string; utilization: number; status: string }[];
  suggestions: {
    waitlist_id: string;
    patient_name: string;
    exam_name: string;
    urgency: string;
    current_sites: string[];
    suggested_site: string;
    distance_km: number;
    suggested_site_utilization: number;
  }[];
}

const STATUS_TONE = { full: "red", balanced: "slate", available: "green" } as const;

export function Overview({ dashboard, siteNames }: { dashboard: Dashboard; siteNames: Record<string, string> }) {
  const [range, setRange] = useState<"today" | "week">("today");
  const crossSite = useQuery({ queryKey: ["cross-site"], queryFn: () => api<CrossSite>("/api/scheduling/cross-site"), refetchInterval: 10000 });
  const k = dashboard.kpis;
  const util = dashboard[range];
  const totals = util.sites.reduce((a, r) => ({ b: a.b + r.booked_min, o: a.o + r.open_min }), { b: 0, o: 0 });

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <Stat label="Appointments today" value={k.appointments_today} hint={`${k.cancelled_today} cancelled · ${k.no_shows_today} no-shows`} />
        <Stat label={`Utilization (${range === "today" ? "today" : "this week"})`} value={pct(totals.o ? totals.b / totals.o : 0)} hint={`${hours(totals.o - totals.b)} idle scanner time`} />
        <Stat label="High no-show risk (7 days)" value={k.high_risk_upcoming} tone="amber" hint="Extra reminder queued" />
        <Stat label="Active waitlist" value={k.waitlist_active} />
        <Stat label="Open backfill cases" value={k.open_backfill_cases} tone={k.open_backfill_cases ? "red" : undefined} />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Utilization by site" actions={<Tabs value={range} onChange={setRange} tabs={[{ id: "today", label: "Today" }, { id: "week", label: "This week" }]} />}>
          <UtilizationBars rows={util.sites} />
          <details className="mt-4 text-sm">
            <summary className="cursor-pointer text-xs font-medium text-slate-500">By scanner</summary>
            <table className="mt-2 w-full text-left text-xs">
              <thead className="text-slate-500"><tr><th className="py-1">Scanner</th><th>Booked</th><th>Idle</th><th className="text-right">Utilization</th></tr></thead>
              <tbody className="divide-y divide-slate-100">
                {util.scanners.map((s) => (
                  <tr key={s.scanner_id}>
                    <td className="py-1">{s.scanner_id}</td>
                    <td className="tabular">{hours(s.booked_min)}</td>
                    <td className="tabular">{hours(s.idle_min)}</td>
                    <td className="tabular text-right">{pct(s.utilization)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        </Card>
        <Card title="Today by site and hour">
          <Heatmap data={dashboard.heatmap} siteNames={siteNames} />
        </Card>
      </div>

      <Card title="Cross-site load balancing · next 7 days">
        {crossSite.isLoading && <Loading />}
        {crossSite.error && <ErrorState error={crossSite.error} />}
        {crossSite.data && (
          <div className="space-y-4">
            <div className="flex flex-wrap gap-2">
              {crossSite.data.sites.map((s) => (
                <Badge key={s.site_id} tone={STATUS_TONE[s.status as keyof typeof STATUS_TONE]}>
                  <MapPin className="size-3" /> {s.name.replace(" Imaging Centre", "")} · {pct(s.utilization)} · {s.status}
                </Badge>
              ))}
            </div>
            {crossSite.data.suggestions.length === 0 ? (
              <EmptyState title="No redirects needed" hint="Every waitlisted patient has at least one acceptable site with capacity." />
            ) : (
              <table className="w-full text-left text-sm">
                <thead className="text-xs text-slate-500">
                  <tr><th className="py-1.5">Patient</th><th>Exam</th><th>Urgency</th><th>Waiting for</th><th /><th>Suggested site</th></tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {crossSite.data.suggestions.slice(0, 8).map((s) => (
                    <tr key={s.waitlist_id}>
                      <td className="py-2 font-medium">{s.patient_name}</td>
                      <td>{s.exam_name}</td>
                      <td><UrgencyBadge urgency={s.urgency} /></td>
                      <td className="text-slate-600">{s.current_sites.join(", ")} (full)</td>
                      <td><ArrowRight className="size-4 text-slate-400" /></td>
                      <td>
                        <span className="font-medium">{s.suggested_site.replace(" Imaging Centre", "")}</span>
                        <span className="text-xs text-slate-500"> · {s.distance_km} km · {pct(s.suggested_site_utilization)} booked</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}
      </Card>
    </div>
  );
}
