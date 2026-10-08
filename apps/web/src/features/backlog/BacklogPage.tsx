import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { ArrowRight, PlayCircle } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { BarChart } from "../../components/charts";
import { Badge, Button, Card, EmptyState, ErrorState, Loading, PageHeader, Stat, Tabs, UrgencyBadge } from "../../components/ui";
import { api, post, put } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { dateTime, pct } from "../../lib/format";
import type { BacklogBoard, BacklogGroup, BacklogStudy, Reader } from "../../lib/types";
import { ReadPanel } from "./ReadPanel";

type Tab = "worklist" | "radiologists" | "turnaround";
const MANAGERS = ["operations_manager", "medical_director", "admin"];
const STATE_TONE = { overdue: "red", at_risk: "amber", on_track: "green" } as const;
const hrs = (h: number | null | undefined) => (h == null ? "–" : `${h.toFixed(1)} h`);

function remaining(s: BacklogStudy) {
  if (s.state === "overdue") return `Overdue ${hrs(-s.remaining_h)}`;
  return `${hrs(s.remaining_h)} left`;
}

function GroupCard({ title, groups, active, onPick }: { title: string; groups: BacklogGroup[]; active: string; onPick: (key: string) => void }) {
  const max = Math.max(1, ...groups.map((g) => g.count));
  return (
    <Card title={title}>
      <ul className="space-y-1.5">
        {groups.map((g) => (
          <li key={g.key}>
            <button onClick={() => onPick(active === g.key ? "" : g.key)}
              className={clsx("w-full rounded-md px-1.5 py-1 text-left text-xs hover:bg-slate-50", active === g.key && "bg-brand-50 ring-1 ring-brand-100")}>
              <div className="flex justify-between gap-2">
                <span className="truncate font-medium text-slate-700">{g.label}</span>
                <span className="tabular text-slate-500">{g.count}{g.overdue > 0 && <span className="text-rose-600"> · {g.overdue} overdue</span>}</span>
              </div>
              <div className="mt-1 flex h-1.5 overflow-hidden rounded-full bg-slate-100">
                <div className="bg-rose-500" style={{ width: `${(g.overdue / max) * 100}%` }} />
                <div className="bg-amber-400" style={{ width: `${(g.at_risk / max) * 100}%` }} />
                <div className="bg-brand-500" style={{ width: `${((g.count - g.overdue - g.at_risk) / max) * 100}%` }} />
              </div>
            </button>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function Worklist({ board, onRead }: { board: BacklogBoard; onRead: (id: string) => void }) {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const [params, setParams] = useSearchParams();
  const isRad = user?.role === "radiologist";
  const filter = {
    site: params.get("site") ?? "", modality: params.get("modality") ?? "", priority: params.get("priority") ?? "",
    age: params.get("age") ?? "", state: params.get("state") ?? "", reader: params.get("reader") ?? (isRad ? user!.id : ""),
  };
  const set = (k: string, v: string) => {
    const next = new URLSearchParams(params);
    next.set(k, v);
    setParams(next, { replace: true });
  };
  const rows = board.studies.filter((s) =>
    (!filter.site || s.site_id === filter.site) && (!filter.modality || s.modality === filter.modality)
    && (!filter.priority || s.priority === filter.priority) && (!filter.age || s.age_bucket === filter.age)
    && (!filter.state || s.state === filter.state) && (!filter.reader || s.assigned_to?.id === filter.reader));
  const reassign = useMutation({
    mutationFn: ({ id, to }: { id: string; to: string }) => post(`/api/backlog/studies/${id}/assign`, { radiologist_id: to, reason: "Manual reassignment" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["backlog"] }),
  });
  const canAssign = user && MANAGERS.includes(user.role);
  const [limit, setLimit] = useState(50);
  const shown = rows.slice(0, limit);
  return (
    <>
      <div className="mb-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <GroupCard title="By site" groups={board.groups.site} active={filter.site} onPick={(v) => set("site", v)} />
        <GroupCard title="By exam type" groups={board.groups.modality} active={filter.modality} onPick={(v) => set("modality", v)} />
        <GroupCard title="By priority" groups={board.groups.priority} active={filter.priority} onPick={(v) => set("priority", v)} />
        <GroupCard title="By time waiting" groups={board.groups.age} active={filter.age} onPick={(v) => set("age", v)} />
      </div>
      <Card
        padded={false}
        title={`Unreported studies (${rows.length})`}
        actions={
          <>
            <select value={filter.state} onChange={(e) => set("state", e.target.value)} className="h-8 rounded-lg border border-slate-300 px-2 text-xs" aria-label="Status filter">
              <option value="">All statuses</option>
              <option value="overdue">Overdue</option>
              <option value="at_risk">At risk</option>
              <option value="on_track">On track</option>
            </select>
            <select value={filter.reader} onChange={(e) => set("reader", e.target.value)} className="h-8 rounded-lg border border-slate-300 px-2 text-xs" aria-label="Radiologist filter" data-testid="reader-filter">
              <option value="">All radiologists</option>
              {board.radiologists.map((r) => <option key={r.id} value={r.id}>{r.id === user?.id ? `${r.name} (me)` : r.name}</option>)}
            </select>
          </>
        }
      >
        {rows.length === 0 ? <EmptyState title="Nothing waiting" hint="No unreported studies match these filters." /> : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="bg-slate-50 text-xs text-slate-500">
                <tr>{["Priority", "Patient", "Exam", "Site", "Completed", "Turnaround", "Assigned to", ""].map((h, i) => <th key={i} className="px-3 py-2 font-medium">{h}</th>)}</tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {shown.map((s) => (
                  <tr key={s.id} data-testid={`backlog-${s.id}`} className={clsx(s.state === "overdue" && "bg-rose-50/40")}>
                    <td className="px-3 py-2"><UrgencyBadge urgency={s.priority} /></td>
                    <td className="px-3 py-2 font-medium text-slate-800">{s.patient_name}</td>
                    <td className="px-3 py-2">{s.exam_name}<p className="text-xs text-slate-500">{s.modality}</p></td>
                    <td className="px-3 py-2 text-xs text-slate-600">{s.site_name ?? "–"}</td>
                    <td className="tabular px-3 py-2 text-xs whitespace-nowrap text-slate-600">{dateTime(s.performed_at)}<p>{hrs(s.age_h)} ago</p></td>
                    <td className="px-3 py-2 whitespace-nowrap">
                      <Badge tone={STATE_TONE[s.state]}>{remaining(s)}</Badge>
                      <p className="text-xs text-slate-500">target {s.target_h} h</p>
                    </td>
                    <td className="px-3 py-2 text-xs">
                      {canAssign ? (
                        <select value={s.assigned_to?.id ?? ""} onChange={(e) => reassign.mutate({ id: s.id, to: e.target.value })}
                          className="h-8 max-w-44 rounded-lg border border-slate-300 px-1.5 text-xs" aria-label={`Reassign ${s.id}`}>
                          {board.radiologists.filter((r) => r.modalities.includes(s.modality)).map((r) => (
                            <option key={r.id} value={r.id}>{r.name}{r.on_shift ? "" : " (off shift)"}</option>
                          ))}
                        </select>
                      ) : <span className="text-slate-600">{s.assigned_to?.name ?? "Unassigned"}</span>}
                    </td>
                    <td className="px-3 py-2 text-right">
                      {isRad && (s.has_image ? (
                        <Link to="/reading" className="inline-flex items-center gap-1 text-xs font-medium text-ai-700 hover:underline">AI draft <ArrowRight className="size-3" /></Link>
                      ) : (
                        <Button size="sm" variant="secondary" onClick={() => onRead(s.id)} data-testid={`read-${s.id}`}>Read</Button>
                      ))}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {rows.length > shown.length && (
              <div className="flex items-center justify-between gap-3 px-4 py-2 text-xs text-slate-500">
                <span>Showing the {shown.length} most urgent of {rows.length}.</span>
                <Button size="sm" variant="ghost" onClick={() => setLimit(limit + 50)}>Show more</Button>
              </div>
            )}
          </div>
        )}
        {reassign.error && <p className="px-4 py-2 text-sm text-rose-600" role="alert">{(reassign.error as Error).message}</p>}
      </Card>
    </>
  );
}

function ReaderCard({ r, canEdit, average }: { r: Reader; canEdit: boolean; average: number }) {
  const queryClient = useQueryClient();
  const toggle = useMutation({
    mutationFn: () => put(`/api/backlog/roster/${r.id}`, { on_shift: !r.on_shift }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["backlog"] }),
  });
  const heavy = r.on_shift && r.queue_minutes > average * 1.2;
  return (
    <div className={clsx("rounded-xl border bg-white p-4 shadow-sm", r.on_shift ? "border-slate-200" : "border-dashed border-slate-300 opacity-80")} data-testid={`reader-${r.id}`}>
      <div className="flex items-start justify-between gap-2">
        <div>
          <p className="text-sm font-semibold text-slate-900">{r.name}</p>
          <p className="text-xs text-slate-500">Reads {r.modalities.join(", ")}</p>
        </div>
        {canEdit ? (
          <button onClick={() => toggle.mutate()} className={clsx("rounded-full px-2 py-0.5 text-xs font-medium", r.on_shift ? "bg-emerald-50 text-emerald-700" : "bg-slate-100 text-slate-600")}
            aria-label={`${r.name} ${r.on_shift ? "on shift" : "off shift"}; toggle`}>
            {r.on_shift ? "On shift" : "Off shift"}
          </button>
        ) : <Badge tone={r.on_shift ? "green" : "slate"}>{r.on_shift ? "On shift" : "Off shift"}</Badge>}
      </div>
      <p className={clsx("tabular mt-3 text-2xl font-semibold", heavy ? "text-amber-600" : "text-slate-900")}>{hrs(r.queue_minutes / 60)}</p>
      <p className="text-xs text-slate-500">{r.queue_count} studies queued{r.overdue > 0 && <span className="text-rose-600"> · {r.overdue} overdue</span>} · {r.signed_today} signed today</p>
      <div className="mt-2 h-1.5 rounded-full bg-slate-100">
        <div className={clsx("h-1.5 rounded-full", heavy ? "bg-amber-500" : "bg-brand-500")} style={{ width: `${Math.min(100, (r.queue_minutes / Math.max(1, average * 2)) * 100)}%` }} />
      </div>
    </div>
  );
}

function Radiologists({ board }: { board: BacklogBoard }) {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const canEdit = !!user && MANAGERS.includes(user.role);
  const working = board.radiologists.filter((r) => r.on_shift);
  const average = board.radiologists.reduce((a, r) => a + r.queue_minutes, 0) / Math.max(1, working.length);
  const apply = useMutation({
    mutationFn: (ids: string[] | null) => post("/api/backlog/suggestions/apply", { study_ids: ids }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["backlog"] }),
  });
  return (
    <>
      <div className="mb-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        {board.radiologists.map((r) => <ReaderCard key={r.id} r={r} canEdit={canEdit} average={average} />)}
      </div>
      <Card
        title={`Suggested reassignments (${board.suggestions.length})`}
        padded={false}
        actions={canEdit && board.suggestions.length > 0 && (
          <Button size="sm" variant="primary" loading={apply.isPending && apply.variables === null} onClick={() => apply.mutate(null)} data-testid="apply-all-suggestions">Apply all</Button>
        )}
      >
        {board.suggestions.length === 0 ? <EmptyState title="Queues are balanced" hint="Suggestions appear when a reader is off shift or well above the team average." /> : (
          <ul className="divide-y divide-slate-100">
            {board.suggestions.map((s) => {
              const study = board.studies.find((x) => x.id === s.study_id);
              return (
                <li key={s.study_id} className="flex flex-wrap items-center justify-between gap-3 px-4 py-2.5" data-testid={`suggestion-${s.study_id}`}>
                  <div className="min-w-0 text-sm">
                    <p className="font-medium text-slate-800">
                      {study ? `${study.exam_name} · ${study.patient_name}` : s.study_id}
                      {study && <span className="ml-2"><Badge tone={STATE_TONE[study.state]}>{remaining(study)}</Badge></span>}
                    </p>
                    <p className="text-xs text-slate-500">{s.from_name} → <span className="font-medium text-slate-700">{s.to_name}</span> · {s.reason}</p>
                  </div>
                  {canEdit && <Button size="sm" loading={apply.isPending && apply.variables?.[0] === s.study_id} onClick={() => apply.mutate([s.study_id])}>Apply</Button>}
                </li>
              );
            })}
          </ul>
        )}
      </Card>
      <p className="mt-2 text-xs text-slate-500">Workload uses estimated reading minutes per study (MRI 15, CT 10, US 6, X-ray 3). Every reassignment is written to the audit log.</p>
    </>
  );
}

function Turnaround({ board }: { board: BacklogBoard }) {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const canEdit = !!user && MANAGERS.includes(user.role);
  const [draft, setDraft] = useState(board.targets.hours);
  const key = JSON.stringify(board.targets.hours);
  useEffect(() => setDraft(board.targets.hours), [key]); // eslint-disable-line react-hooks/exhaustive-deps
  const save = useMutation({
    mutationFn: () => put("/api/backlog/targets", { hours: draft, at_risk_fraction: board.targets.at_risk_fraction }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["backlog"] }),
  });
  const t = board.turnaround;
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <div className="space-y-4 lg:col-span-2">
        <Card title={`Turnaround by priority, last ${t.window_days} days`} padded={false}>
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-50 text-xs text-slate-500">
              <tr>{["Priority", "Target", "Reports", "Median", "90th percentile", "Within target"].map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}</tr>
            </thead>
            <tbody className="tabular divide-y divide-slate-100">
              {t.by_priority.map((p) => (
                <tr key={p.priority}>
                  <td className="px-3 py-2"><UrgencyBadge urgency={p.priority} /></td>
                  <td className="px-3 py-2">{p.target_h} h</td>
                  <td className="px-3 py-2">{p.count}</td>
                  <td className="px-3 py-2">{hrs(p.median_h)}</td>
                  <td className={clsx("px-3 py-2", p.p90_h != null && p.p90_h > p.target_h && "text-rose-600")}>{hrs(p.p90_h)}</td>
                  <td className="px-3 py-2">{p.within_target == null ? "–" : pct(p.within_target)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
        <Card title="Reports signed within target, per day">
          <BarChart label="Share of reports signed within target per day"
            data={t.daily.map((d) => ({ label: d.date.slice(5), value: d.within_target == null ? null : d.within_target * 100,
              tone: d.within_target != null && d.within_target < 0.8 ? "amber" : undefined }))}
            reference={90} referenceLabel="90% goal" format={(v) => `${v.toFixed(0)}%`} />
          <p className="text-xs text-slate-500">Turnaround = report signed − exam completed. Bars show the share of each day's sign-offs that met their priority target.</p>
        </Card>
      </div>
      <Card title="Targets by priority">
        <div className="space-y-2">
          {(["P1", "P2", "P3", "P4"] as const).map((p) => (
            <label key={p} className="flex items-center justify-between gap-3 text-sm">
              <UrgencyBadge urgency={p} />
              <span className="flex items-center gap-1.5">
                <input type="number" min={0.5} step={0.5} value={draft[p] ?? ""} disabled={!canEdit}
                  onChange={(e) => setDraft({ ...draft, [p]: Number(e.target.value) })}
                  className="h-8 w-20 rounded-lg border border-slate-300 px-2 text-right" aria-label={`${p} target hours`} />
                hours
              </span>
            </label>
          ))}
          <p className="text-xs text-slate-500">Studies are flagged at risk after {pct(board.targets.at_risk_fraction)} of the target has elapsed.</p>
          {canEdit ? <Button variant="primary" size="sm" loading={save.isPending} onClick={() => save.mutate()} data-testid="save-targets">Save targets</Button>
            : <p className="text-xs text-slate-500">Operations managers and the medical director set targets.</p>}
          {save.error && <p className="text-sm text-rose-600" role="alert">{(save.error as Error).message}</p>}
        </div>
      </Card>
    </div>
  );
}

export function BacklogPage() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<Tab>("worklist");
  const [reading, setReading] = useState<string | null>(null);
  const q = useQuery({ queryKey: ["backlog"], queryFn: () => api<BacklogBoard>("/api/backlog"), refetchInterval: 3000 });
  const simulate = useMutation({ mutationFn: () => post("/api/backlog/simulate-completion"), onSuccess: () => queryClient.invalidateQueries({ queryKey: ["backlog"] }) });
  const mine = useMemo(() => q.data?.studies.filter((s) => s.assigned_to?.id === user?.id).length ?? 0, [q.data, user]);
  const k = q.data?.kpis;
  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title="Reading backlog"
        subtitle="Completed exams waiting for a signed report, turnaround against target, and each radiologist's queue. Updates live."
        actions={user && MANAGERS.includes(user.role) && (
          <Button size="sm" onClick={() => simulate.mutate()} loading={simulate.isPending} title="Demo: the technologist finishes the next exam on the schedule" data-testid="simulate-completion">
            <PlayCircle className="size-4" /> Simulate completed exam
          </Button>
        )}
      />
      {q.isLoading && <Loading />}
      {q.error && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.data && k && (
        <>
          <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
            <Stat label="Unreported" value={<span data-testid="kpi-unread">{k.unread}</span>} hint={user?.role === "radiologist" ? `${mine} in my queue` : undefined} />
            <Stat label="Overdue" value={k.overdue} tone={k.overdue ? "red" : "green"} />
            <Stat label="At risk" value={k.at_risk} tone={k.at_risk ? "amber" : undefined} />
            <Stat label="Oldest waiting" value={hrs(k.oldest_h)} />
            <Stat label="Median turnaround (7 d)" value={hrs(k.median_tat_h)} />
            <Stat label="Within target (7 d)" value={k.within_target == null ? "–" : pct(k.within_target)} tone={k.within_target != null && k.within_target < 0.9 ? "amber" : "green"} />
          </div>
          <Tabs value={tab} onChange={setTab} tabs={[
            { id: "worklist", label: "Worklist" },
            { id: "radiologists", label: <>Radiologists {q.data.suggestions.length > 0 && <Badge tone="amber">{q.data.suggestions.length} suggestions</Badge>}</> },
            { id: "turnaround", label: "Turnaround" },
          ]} />
          {tab === "worklist" && <Worklist board={q.data} onRead={setReading} />}
          {tab === "radiologists" && <Radiologists board={q.data} />}
          {tab === "turnaround" && <Turnaround board={q.data} />}
        </>
      )}
      {reading && <ReadPanel studyId={reading} onClose={() => setReading(null)} />}
    </div>
  );
}
