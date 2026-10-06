import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { AlertOctagon, BellRing, CheckCircle2, FileText, Lock, PhoneCall, Siren } from "lucide-react";
import { useEffect, useState } from "react";
import { Badge, Button, Card, EmptyState, ErrorState, Loading, PageHeader, Stat, Tabs } from "../../components/ui";
import { api, post, put } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { dateTime, time } from "../../lib/format";
import type { CaseEvent, CriticalCase, CriticalPolicy } from "../../lib/types";

interface CaseList { cases: CriticalCase[]; counts: Record<string, number>; overdue: number; methods: Record<string, string> }
type Filter = "active" | "closed" | "all";

const LEVEL_TONE = { critical: "red", urgent: "amber", significant: "blue" } as const;
const STATUS: Record<CriticalCase["status"], { label: string; tone: "red" | "amber" | "green" | "slate" | "blue" }> = {
  open: { label: "Awaiting acknowledgement", tone: "amber" },
  escalated: { label: "Escalated", tone: "red" },
  acknowledged: { label: "Acknowledged", tone: "green" },
  closed: { label: "Closed", tone: "slate" },
};
const EVENT_ICON: Record<CaseEvent["kind"], typeof Siren> = {
  opened: FileText, notify: PhoneCall, renotify: BellRing, escalate: AlertOctagon, acknowledged: CheckCircle2, closed: Lock,
};

function useNow() {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  return now;
}

function duration(seconds: number) {
  const s = Math.abs(Math.round(seconds));
  if (s < 90) return `${s} s`;
  if (s < 5400) return `${Math.round(s / 60)} min`;
  return `${(s / 3600).toFixed(1)} h`;
}

export function AckCountdown({ c, now }: { c: CriticalCase; now: number }) {
  if (c.acknowledgement) return <span className="text-xs text-emerald-700">Acknowledged {time(c.acknowledgement.at)}</span>;
  const left = (new Date(c.ack_due_at).getTime() - now) / 1000;
  return left >= 0
    ? <span className="tabular text-xs text-amber-700">Acknowledge within {duration(left)}</span>
    : <span className="tabular text-xs font-medium text-rose-600">Overdue by {duration(left)}</span>;
}

export function Timeline({ events }: { events: CaseEvent[] }) {
  return (
    <ol className="relative space-y-3 border-l border-slate-200 pl-5" data-testid="case-timeline">
      {events.map((e, i) => {
        const Icon = EVENT_ICON[e.kind] ?? FileText;
        return (
          <li key={i} className="relative">
            <span className={clsx("absolute -left-[29px] grid size-5 place-items-center rounded-full ring-4 ring-white",
              e.kind === "escalate" ? "bg-rose-100 text-rose-700" : e.kind === "acknowledged" ? "bg-emerald-100 text-emerald-700" : "bg-slate-100 text-slate-600")}>
              <Icon className="size-3" />
            </span>
            <p className="text-sm text-slate-800">{e.text}</p>
            <p className="text-xs text-slate-500">{dateTime(e.ts)} · {e.actor === "system" ? "Automatic" : e.actor}</p>
          </li>
        );
      })}
    </ol>
  );
}

function CaseDetail({ c, methods, now }: { c: CriticalCase; methods: Record<string, string>; now: number }) {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const [byName, setByName] = useState(c.referrer_name);
  const [byRole, setByRole] = useState("Ordering physician");
  const [method, setMethod] = useState("phone");
  const [note, setNote] = useState("");
  useEffect(() => {
    setByName(c.status === "escalated" ? "" : c.referrer_name);
    setByRole(c.status === "escalated" ? "Covering physician" : "Ordering physician");
  }, [c.id, c.status, c.referrer_name]);
  const done = () => queryClient.invalidateQueries({ queryKey: ["critical"] });
  const ack = useMutation({ mutationFn: () => post(`/api/critical/${c.id}/acknowledge`, { by_name: byName, by_role: byRole, method }), onSuccess: done });
  const close = useMutation({ mutationFn: () => post(`/api/critical/${c.id}/close`, { note }), onSuccess: done });
  const canRecord = user && ["radiologist", "medical_director", "admin", "front_desk"].includes(user.role);
  const canClose = user && ["radiologist", "medical_director", "admin"].includes(user.role);
  return (
    <Card title={<span className="flex items-center gap-2">{c.id} <Badge tone={STATUS[c.status].tone}>{STATUS[c.status].label}</Badge></span>}
      actions={<Badge tone={LEVEL_TONE[c.level]}>{c.level_label}</Badge>}>
      <p className="text-base font-semibold text-slate-900">{c.finding}</p>
      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
        <div><dt className="text-xs text-slate-500">Patient</dt><dd>{c.patient_name}</dd></div>
        <div><dt className="text-xs text-slate-500">Exam</dt><dd>{c.exam_name ?? "–"} · {c.report_id}</dd></div>
        <div><dt className="text-xs text-slate-500">Ordering physician</dt><dd>{c.referrer_name}<span className="block text-xs text-slate-500">{c.referrer_phone}</span></dd></div>
        <div><dt className="text-xs text-slate-500">Opened</dt><dd>{dateTime(c.created_at)} by {c.opened_by}</dd></div>
        <div className="col-span-2"><dt className="text-xs text-slate-500">Acknowledgement deadline</dt><dd>{dateTime(c.ack_due_at)} · <AckCountdown c={c} now={now} /></dd></div>
      </dl>

      <h3 className="mt-5 mb-2 text-xs font-semibold tracking-wide text-slate-500 uppercase">Timeline</h3>
      <Timeline events={c.events} />

      {!c.acknowledgement && c.status !== "closed" && canRecord && (
        <div className="mt-5 rounded-lg border border-slate-200 p-3">
          <p className="mb-2 text-sm font-medium text-slate-800">Record acknowledgement</p>
          <div className="grid gap-2 sm:grid-cols-3">
            <input value={byName} onChange={(e) => setByName(e.target.value)} placeholder="Who acknowledged" className="h-9 rounded-lg border border-slate-300 px-2 text-sm" aria-label="Acknowledged by" data-testid="ack-name" />
            <input value={byRole} onChange={(e) => setByRole(e.target.value)} placeholder="Their role" className="h-9 rounded-lg border border-slate-300 px-2 text-sm" aria-label="Role" />
            <select value={method} onChange={(e) => setMethod(e.target.value)} className="h-9 rounded-lg border border-slate-300 px-2 text-sm" aria-label="How">
              {Object.entries(methods).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </div>
          <div className="mt-2 flex items-center justify-between gap-2">
            <p className="text-xs text-slate-500">Time is recorded as now, with your name as the recorder.</p>
            <Button size="sm" variant="primary" disabled={!byName.trim() || !byRole.trim()} loading={ack.isPending} onClick={() => ack.mutate()} data-testid="ack-submit">Record acknowledgement</Button>
          </div>
          {ack.error && <p className="mt-1 text-sm text-rose-600" role="alert">{(ack.error as Error).message}</p>}
        </div>
      )}
      {c.acknowledgement && (
        <div className="mt-5 rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
          Acknowledged by <strong>{c.acknowledgement.by_name}</strong> ({c.acknowledgement.by_role}) via {methods[c.acknowledgement.method]?.toLowerCase()} at {dateTime(c.acknowledgement.at)}; recorded by {c.acknowledgement.recorded_by}.
        </div>
      )}
      {c.status !== "closed" && canClose && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Closing note (optional)" className="h-9 min-w-0 flex-1 rounded-lg border border-slate-300 px-2 text-sm" aria-label="Closing note" />
          <Button size="sm" disabled={!c.acknowledgement} loading={close.isPending} onClick={() => close.mutate()} data-testid="close-case"
            title={c.acknowledgement ? "Close the case" : "Record an acknowledgement first"}>Close case</Button>
          {!c.acknowledgement && <p className="w-full text-xs text-slate-500">A case cannot be closed until an acknowledgement is recorded.</p>}
          {close.error && <p className="w-full text-sm text-rose-600" role="alert">{(close.error as Error).message}</p>}
        </div>
      )}
      {c.status === "closed" && <p className="mt-3 text-sm text-slate-600">Closed by {c.closed_by} on {dateTime(c.closed_at!)}{c.close_note && `: ${c.close_note}`}</p>}
    </Card>
  );
}

function PolicyCard() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const q = useQuery({ queryKey: ["critical", "policy"], queryFn: () => api<CriticalPolicy>("/api/critical/policy") });
  const [draft, setDraft] = useState<CriticalPolicy | null>(null);
  useEffect(() => { if (q.data) setDraft(q.data); }, [q.data]);
  const save = useMutation({ mutationFn: () => put("/api/critical/policy", { levels: draft!.levels }), onSuccess: () => queryClient.invalidateQueries({ queryKey: ["critical"] }) });
  const canEdit = user && ["medical_director", "admin"].includes(user.role);
  if (!draft) return null;
  const set = (level: string, key: "renotify_after_s" | "escalate_after_s", v: number) =>
    setDraft({ ...draft, levels: { ...draft.levels, [level]: { ...draft.levels[level], [key]: v } } });
  return (
    <Card title="Notification and escalation policy">
      <div className="mb-3 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">Demo timings are compressed to seconds so escalation is visible live. The typical clinic policy is shown under each level.</div>
      <div className="space-y-3">
        {Object.entries(draft.levels).map(([k, lv]) => (
          <div key={k} className="text-sm">
            <div className="flex items-center gap-2"><Badge tone={LEVEL_TONE[k as keyof typeof LEVEL_TONE]}>{lv.label}</Badge></div>
            <p className="mt-0.5 text-xs text-slate-500">{lv.real_world}</p>
            <div className="mt-1 grid grid-cols-2 gap-2">
              <label className="text-xs text-slate-600">Re-notify after (s)
                <input type="number" min={5} value={lv.renotify_after_s} disabled={!canEdit} onChange={(e) => set(k, "renotify_after_s", Number(e.target.value))}
                  className="mt-0.5 h-8 w-full rounded-lg border border-slate-300 px-2" />
              </label>
              <label className="text-xs text-slate-600">Escalate after (s)
                <input type="number" min={10} value={lv.escalate_after_s} disabled={!canEdit} onChange={(e) => set(k, "escalate_after_s", Number(e.target.value))}
                  className="mt-0.5 h-8 w-full rounded-lg border border-slate-300 px-2" />
              </label>
            </div>
          </div>
        ))}
        <p className="text-xs text-slate-500">Escalation goes to the medical director. New timings apply to cases opened afterwards.</p>
        {canEdit ? <Button size="sm" variant="primary" loading={save.isPending} onClick={() => save.mutate()}>Save policy</Button>
          : <p className="text-xs text-slate-500">Only the medical director changes the policy.</p>}
        {save.error && <p className="text-sm text-rose-600" role="alert">{(save.error as Error).message}</p>}
      </div>
    </Card>
  );
}

export function CriticalPage() {
  const now = useNow();
  const [filter, setFilter] = useState<Filter>("active");
  const [selected, setSelected] = useState<string | null>(null);
  const q = useQuery({ queryKey: ["critical", "list"], queryFn: () => api<CaseList>("/api/critical"), refetchInterval: 2000 });
  const cases = q.data?.cases.filter((c) => filter === "all" || (filter === "closed" ? c.status === "closed" : c.status !== "closed")) ?? [];
  const current = q.data?.cases.find((c) => c.id === selected) ?? cases[0];
  const n = q.data?.counts ?? {};
  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader title="Critical results" subtitle="Findings that must reach the ordering physician: automatic notification, escalation when nobody acknowledges, and a full record of who was told, when and how." />
      {q.isLoading && <Loading />}
      {q.error && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.data && (
        <>
          <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-5">
            <Stat label="Awaiting acknowledgement" value={n.open ?? 0} tone={n.open ? "amber" : undefined} />
            <Stat label="Escalated" value={<span data-testid="kpi-escalated">{n.escalated ?? 0}</span>} tone={n.escalated ? "red" : undefined} />
            <Stat label="Past deadline" value={q.data.overdue} tone={q.data.overdue ? "red" : "green"} />
            <Stat label="Acknowledged, not closed" value={n.acknowledged ?? 0} />
            <Stat label="Closed" value={n.closed ?? 0} />
          </div>
          <div className="grid gap-4 lg:grid-cols-5">
            <div className="lg:col-span-2">
              <Tabs value={filter} onChange={setFilter} tabs={[{ id: "active", label: "Active" }, { id: "closed", label: "Closed" }, { id: "all", label: "All" }]} />
              {cases.length === 0 ? <Card><EmptyState title="No cases" hint="Confirmed urgent findings from signed reports appear here." /></Card> : (
                <ul className="space-y-2">
                  {cases.map((c) => (
                    <li key={c.id}>
                      <button onClick={() => setSelected(c.id)} data-testid={`case-${c.id}`}
                        className={clsx("w-full rounded-xl border bg-white p-3 text-left shadow-sm hover:border-slate-300",
                          current?.id === c.id ? "border-brand-500 ring-1 ring-brand-100" : "border-slate-200", c.status === "escalated" && "border-l-4 border-l-rose-500")}>
                        <div className="flex flex-wrap items-center gap-1.5">
                          <Badge tone={LEVEL_TONE[c.level]}>{c.level}</Badge>
                          <Badge tone={STATUS[c.status].tone}>{STATUS[c.status].label}</Badge>
                          <span className="ml-auto text-xs text-slate-400">{c.id}</span>
                        </div>
                        <p className="mt-1.5 text-sm font-medium text-slate-900">{c.finding}</p>
                        <p className="text-xs text-slate-500">{c.patient_name} · {c.referrer_name}</p>
                        {c.status !== "closed" && <div className="mt-1"><AckCountdown c={c} now={now} /></div>}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <div className="space-y-4 lg:col-span-3">
              {current && <CaseDetail key={current.id} c={current} methods={q.data.methods} now={now} />}
              <PolicyCard />
            </div>
          </div>
        </>
      )}
    </div>
  );
}
