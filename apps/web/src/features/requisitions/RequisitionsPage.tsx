import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FilePlus2, Sparkles } from "lucide-react";
import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Badge, Button, Card, EmptyState, ErrorState, Loading, PageHeader, Stat, Tabs, UrgencyBadge } from "../../components/ui";
import { api, post, put } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { dateTime, pct } from "../../lib/format";
import type { RequisitionSummary } from "../../lib/types";
import { ContrastBadge, DaysLeft, MriBadge, RequisitionStatus } from "./badges";

interface QueueResponse { requisitions: RequisitionSummary[]; targets: Record<string, number>; counts: Record<string, number> }
interface Sample { patient_id: string; patient_name: string; referrer_id: string; text: string; expected: { priority: string; protocol_id: string } }

function NewRequisition({ onDone }: { onDone: (id: string) => void }) {
  const samples = useQuery({ queryKey: ["req-samples"], queryFn: () => api<{ samples: Sample[] }>("/api/requisitions/samples"), staleTime: Infinity });
  const [index, setIndex] = useState(0);
  const [text, setText] = useState<string | null>(null);
  const sample = samples.data?.samples[index];
  const submit = useMutation({
    mutationFn: () => post<RequisitionSummary>("/api/requisitions", { patient_id: sample!.patient_id, referrer_id: sample!.referrer_id, text: text ?? sample!.text, channel: "fax" }),
    onSuccess: (r) => onDone(r.id),
  });
  if (samples.isLoading) return <Loading />;
  if (samples.error) return <ErrorState error={samples.error} />;
  return (
    <Card title="Receive a requisition (simulated fax)" className="mb-4">
      <div className="mb-2 flex flex-wrap items-center gap-2 text-sm">
        <span className="text-slate-500">Synthetic sample:</span>
        {samples.data!.samples.map((s, i) => (
          <Button key={i} size="sm" variant={i === index ? "primary" : "secondary"} onClick={() => { setIndex(i); setText(null); }}>
            {s.patient_name}
          </Button>
        ))}
      </div>
      <textarea value={text ?? sample?.text ?? ""} onChange={(e) => setText(e.target.value)} rows={9}
        className="w-full rounded-lg border border-slate-300 p-3 font-mono text-xs" aria-label="Requisition text" />
      <div className="mt-2 flex items-center justify-between gap-2">
        <p className="text-xs text-slate-500">After submitting, the intake worker extracts, triages and suggests a protocol within seconds.</p>
        <Button variant="primary" loading={submit.isPending} onClick={() => submit.mutate()} data-testid="submit-requisition">Submit requisition</Button>
      </div>
      {submit.error && <p className="mt-2 text-sm text-rose-600">{(submit.error as Error).message}</p>}
    </Card>
  );
}

function Queue() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const [showNew, setShowNew] = useState(false);
  const q = useQuery({ queryKey: ["requisitions"], queryFn: () => api<QueueResponse>("/api/requisitions"), refetchInterval: 3000 });
  const canReceive = user && ["front_desk", "operations_manager", "admin"].includes(user.role);
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const { requisitions, counts, targets } = q.data!;
  const overdue = requisitions.filter((r) => (r.days_left ?? 0) < 0).length;
  return (
    <>
      <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-5">
        <Stat label="Being processed by AI" value={(counts.received ?? 0) + (counts.processing ?? 0)} />
        <Stat label="Awaiting radiologist" value={counts.ready ?? 0} tone={counts.ready ? "amber" : undefined} />
        <Stat label="Ready to book" value={counts.approved ?? 0} />
        <Stat label="Past target wait" value={overdue} tone={overdue ? "red" : undefined} />
        <Stat label="Targets (days)" value={<span className="text-base">{Object.entries(targets).map(([k, v]) => `${k} ${v}`).join(" · ")}</span>} />
      </div>
      {canReceive && (
        <div className="mb-3 flex justify-end">
          <Button variant={showNew ? "ghost" : "primary"} onClick={() => setShowNew(!showNew)} data-testid="new-requisition">
            <FilePlus2 className="size-4" /> {showNew ? "Close" : "New requisition"}
          </Button>
        </div>
      )}
      {showNew && <NewRequisition onDone={() => setShowNew(false)} />}
      <Card padded={false}>
        {requisitions.length === 0 ? <EmptyState title="No open requisitions" /> : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="bg-slate-50 text-xs text-slate-500">
                <tr>{["Priority", "Target", "Patient", "Requested exam", "Protocol", "Checks", "Status", "Received"].map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}</tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {requisitions.map((r) => (
                  <tr key={r.id} onClick={() => navigate(`/requisitions/${r.id}`)} className="cursor-pointer hover:bg-slate-50" data-testid={`req-${r.id}`}>
                    <td className="px-3 py-2">
                      {r.priority ? (
                        <span className="flex items-center gap-1">
                          <UrgencyBadge urgency={r.priority} />
                          {!r.triage_reviewed && <Sparkles className="size-3 text-ai-600" aria-label="AI priority, not yet reviewed" />}
                        </span>
                      ) : <span className="text-xs text-slate-400">pending</span>}
                    </td>
                    <td className="px-3 py-2 whitespace-nowrap"><DaysLeft days={r.days_left} /></td>
                    <td className="px-3 py-2"><p className="font-medium">{r.patient_name}</p><p className="text-xs text-slate-500">{r.referrer_name}</p></td>
                    <td className="px-3 py-2">{r.requested_exam ?? "–"}{r.low_confidence.length > 0 && <Badge tone="amber" className="ml-1">{r.low_confidence.length} to check</Badge>}</td>
                    <td className="px-3 py-2 text-xs">{r.protocol_name ?? "–"}{r.protocol_name && !r.protocol_approved && <span className="text-ai-700"> (suggested)</span>}</td>
                    <td className="px-3 py-2"><div className="flex flex-col items-start gap-1"><ContrastBadge status={r.contrast_status} /><MriBadge status={r.mri_status} /></div></td>
                    <td className="px-3 py-2"><RequisitionStatus status={r.status} /></td>
                    <td className="px-3 py-2 text-xs whitespace-nowrap text-slate-500">{dateTime(r.received_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}

interface Agreement { reviewed: number; agreement: number | null; over_triaged: number; under_triaged: number; overrides: { requisition_id: string; ai_priority: string; final_priority: string; override_reason: string; reviewed_by: string }[] }
interface ProtocolStats { approved: number; adoption_rate: number | null; top3_rate: number | null; common_changes: { from_name: string; to_name: string; count: number }[]; by_protocol: { name: string; count: number }[] }

function Insights() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const agreement = useQuery({ queryKey: ["triage-agreement"], queryFn: () => api<Agreement>("/api/requisitions/triage/agreement") });
  const stats = useQuery({ queryKey: ["protocol-stats"], queryFn: () => api<ProtocolStats>("/api/protocols/stats") });
  const targets = useQuery({ queryKey: ["triage-targets"], queryFn: () => api<Record<string, number>>("/api/requisitions/triage/config") });
  const [draft, setDraft] = useState<Record<string, number> | null>(null);
  const save = useMutation({ mutationFn: (t: Record<string, number>) => put("/api/requisitions/triage/config", t), onSuccess: () => queryClient.invalidateQueries() });
  const canEdit = user && ["medical_director", "admin"].includes(user.role);
  const t = draft ?? targets.data;
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card title="Triage: AI vs radiologist">
        {agreement.isLoading && <Loading />}
        {agreement.data && (
          <>
            <div className="mb-3 grid grid-cols-3 gap-3">
              <div><p className="text-xs text-slate-500">Agreement</p><p className="tabular text-xl font-semibold">{agreement.data.agreement == null ? "–" : pct(agreement.data.agreement)}</p></div>
              <div><p className="text-xs text-slate-500">AI more urgent</p><p className="tabular text-xl font-semibold">{agreement.data.over_triaged}</p></div>
              <div><p className="text-xs text-slate-500">AI less urgent</p><p className="tabular text-xl font-semibold text-rose-600">{agreement.data.under_triaged}</p></div>
            </div>
            <p className="mb-2 text-xs text-slate-500">Over {agreement.data.reviewed} reviewed requisitions. Every override is kept with its reason and used as evaluation data.</p>
            <ul className="max-h-56 space-y-1.5 overflow-y-auto text-sm">
              {agreement.data.overrides.map((o) => (
                <li key={o.requisition_id} className="flex flex-wrap items-center gap-1.5">
                  <span className="font-mono text-xs text-slate-400">{o.requisition_id}</span>
                  <UrgencyBadge urgency={o.ai_priority} /> → <UrgencyBadge urgency={o.final_priority} />
                  <span className="text-slate-600">{o.override_reason}</span>
                </li>
              ))}
            </ul>
          </>
        )}
      </Card>
      <Card title="Protocol suggestions: adoption">
        {stats.isLoading && <Loading />}
        {stats.data && (
          <>
            <div className="mb-3 grid grid-cols-3 gap-3">
              <div><p className="text-xs text-slate-500">Approved</p><p className="tabular text-xl font-semibold">{stats.data.approved}</p></div>
              <div><p className="text-xs text-slate-500">First choice adopted</p><p className="tabular text-xl font-semibold">{stats.data.adoption_rate == null ? "–" : pct(stats.data.adoption_rate)}</p></div>
              <div><p className="text-xs text-slate-500">In top 3</p><p className="tabular text-xl font-semibold">{stats.data.top3_rate == null ? "–" : pct(stats.data.top3_rate)}</p></div>
            </div>
            <p className="mb-1 text-xs font-medium text-slate-500">Most common changes</p>
            {stats.data.common_changes.length === 0 ? <p className="text-sm text-slate-500">None yet.</p> : (
              <ul className="space-y-1 text-sm">{stats.data.common_changes.map((c, i) => <li key={i}>{c.from_name} → <span className="font-medium">{c.to_name}</span> <span className="text-slate-400">×{c.count}</span></li>)}</ul>
            )}
          </>
        )}
      </Card>
      <Card title="Target wait by priority (days)">
        {t && (
          <div className="flex flex-wrap items-end gap-3">
            {["P1", "P2", "P3", "P4"].map((k) => (
              <label key={k} className="text-sm">
                <span className="mb-1 block"><UrgencyBadge urgency={k} /></span>
                <input type="number" min={0} value={t[k]} disabled={!canEdit} onChange={(e) => setDraft({ ...t, [k]: Number(e.target.value) })}
                  className="h-9 w-20 rounded-lg border border-slate-300 px-2" aria-label={`${k} target days`} />
              </label>
            ))}
            {canEdit && <Button variant="primary" loading={save.isPending} onClick={() => save.mutate(t)}>Save</Button>}
          </div>
        )}
        <p className="mt-2 text-xs text-slate-500">Demo placeholder values. The medical director sets them; the queue re-sorts immediately.</p>
      </Card>
    </div>
  );
}

export function RequisitionsPage() {
  const [params, setParams] = useSearchParams();
  const tab = (params.get("tab") as "queue" | "insights") || "queue";
  return (
    <div>
      <PageHeader title="Requisitions" subtitle="Intake pipeline: AI extraction, triage and protocol suggestions, reviewed by radiologists." />
      <Tabs value={tab} onChange={(t) => setParams({ tab: t }, { replace: true })} tabs={[{ id: "queue", label: "Queue" }, { id: "insights", label: "Triage & protocol insights" }]} />
      {tab === "queue" ? <Queue /> : <Insights />}
    </div>
  );
}
