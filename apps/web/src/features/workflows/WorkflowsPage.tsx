// The workflow view (spec 6.5): every durable workflow with its current step. Journeys (one per inpatient
// encounter), capacity exceptions (one per Control Tower exception) and low-confidence reviews. The timelines are
// read from the database, so the list works while the worker is down; without Temporal it says "offline".

import { useMutation, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { AlertTriangle, Bug, CircleOff, PlayCircle, Server, Workflow } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Button, Card, EmptyState, ErrorState, Loading, PageHeader, Tabs } from "../../components/ui";
import { api, post } from "../../lib/api";
import { dateTime } from "../../lib/format";
import { StepStatusPill } from "./StepStatus";
import { duration, useWorkflows, useWorkflowStatus, WORKFLOW_LABEL, type TemporalStatus, type WorkflowSummary, type WorkflowType } from "./api";

type Filter = WorkflowType | "all";

export function WorkflowsPage() {
  const [filter, setFilter] = useState<Filter>("InpatientJourneyWorkflow");
  const list = useWorkflows(filter);
  const status = useWorkflowStatus();
  const counts = list.data?.counts ?? {};
  const tab = (t: WorkflowType) => ({
    id: t,
    label: (
      <span className="inline-flex items-center gap-1.5">
        {WORKFLOW_LABEL[t]}
        {(counts[t]?.running ?? 0) > 0 && <Badge tone="blue">{counts[t].running}</Badge>}
      </span>
    ),
  });
  return (
    <div>
      <PageHeader
        title="Workflows"
        subtitle="Durable workflows (Temporal): the patient journey across departments, the loop behind each capacity exception and the human review of low-confidence AI output. Every step's side effects go through the Tool Gateway; a retried step never writes twice."
      />
      <StatusBanner status={status.data} />
      {status.data?.can_control && status.data.configured && <Controls status={status.data} />}
      <Tabs<Filter>
        value={filter}
        onChange={setFilter}
        tabs={[tab("InpatientJourneyWorkflow"), tab("CapacityExceptionWorkflow"), tab("LowConfidenceReviewWorkflow"), { id: "all", label: "All" }]}
      />
      <Card padded={false}>
        {list.isLoading && <Loading />}
        {list.error && <ErrorState error={list.error} onRetry={() => list.refetch()} />}
        {list.data && list.data.workflows.length === 0 && (
          <EmptyState
            title="No workflows yet"
            hint={status.data?.configured ? "Journeys start when a patient is admitted (advance the simulator), capacity workflows when the Control Tower finds an exception." : "Temporal is off: no workflows run."}
            icon={<Workflow className="size-8" />}
          />
        )}
        {list.data && list.data.workflows.length > 0 && (
          <table className="w-full text-sm" data-testid="workflow-list">
            <thead className="border-b border-slate-100 text-left text-xs text-slate-500">
              <tr>
                <th className="px-4 py-2 font-medium">Workflow</th>
                <th className="px-4 py-2 font-medium">Subject</th>
                <th className="px-4 py-2 font-medium">Current step</th>
                <th className="px-4 py-2 font-medium">Progress</th>
                <th className="px-4 py-2 font-medium">Updated</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {list.data.workflows.map((w) => <Row key={w.id} w={w} />)}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}

function Row({ w }: { w: WorkflowSummary }) {
  const subject = w.encounter_id ? `Encounter ${w.encounter_id}` : w.exception_id ? `${w.exception_id} · ${w.unit_id}` : w.run_id ?? "–";
  return (
    <tr className={clsx("hover:bg-slate-50", w.attention && "bg-rose-50/40")} data-workflow={w.id}>
      <td className="px-4 py-2.5">
        <Link to={`/workflows/${encodeURIComponent(w.id)}`} className="font-medium text-brand-700 hover:underline">{w.id}</Link>
        <p className="text-xs text-slate-500">{WORKFLOW_LABEL[w.workflow_type]}</p>
      </td>
      <td className="px-4 py-2.5 text-slate-700">{subject}</td>
      <td className="px-4 py-2.5">
        {w.status === "running" && w.current_status ? (
          <span className="flex flex-wrap items-center gap-1.5">
            <span className="text-slate-800">{w.current_label}</span>
            <StepStatusPill step={{ status: w.current_status, kind: "auto", detail: null }} />
          </span>
        ) : (
          <Badge tone={w.status === "completed" ? "green" : w.status === "failed" ? "red" : "slate"}>{w.status}</Badge>
        )}
        {w.attention && <span className="ml-1.5 inline-flex items-center gap-1 text-xs font-medium text-rose-700"><AlertTriangle className="size-3.5" />needs attention</span>}
      </td>
      <td className="px-4 py-2.5">
        <div className="flex items-center gap-2">
          <div className="h-1.5 w-24 overflow-hidden rounded-full bg-slate-100" aria-hidden>
            <div className="h-full bg-brand-500" style={{ width: `${(100 * w.done) / Math.max(1, w.total)}%` }} />
          </div>
          <span className="text-xs text-slate-600">{w.done} / {w.total}</span>
        </div>
      </td>
      <td className="px-4 py-2.5 text-xs text-slate-500">{w.updated_at ? dateTime(w.updated_at) : "–"}</td>
    </tr>
  );
}

export function StatusBanner({ status }: { status?: TemporalStatus }) {
  if (!status) return null;
  if (!status.configured) {
    return (
      <div className="mb-4 flex items-start gap-3 rounded-xl border border-slate-200 bg-slate-50 p-3 text-sm" data-testid="temporal-offline">
        <CircleOff className="mt-0.5 size-5 shrink-0 text-slate-500" />
        <div>
          <p className="font-semibold text-slate-800">Offline: durable workflows are off</p>
          <p className="text-slate-600">TEMPORAL_ADDRESS is not set. Every module works without them; journeys, capacity loops and reviews are not tracked here.</p>
        </div>
      </div>
    );
  }
  const pending = status.outbox?.pending ?? 0;
  const ok = status.online && status.workers > 0;
  return (
    <div className={clsx("mb-4 flex flex-wrap items-center gap-x-4 gap-y-1 rounded-xl border p-3 text-sm", ok ? "border-emerald-200 bg-emerald-50/60" : "border-amber-200 bg-amber-50")} data-testid="temporal-status">
      <span className="inline-flex items-center gap-1.5 font-semibold text-slate-800">
        <Server className={clsx("size-4", ok ? "text-emerald-600" : "text-amber-600")} />
        {status.online ? "Temporal online" : "Temporal unreachable"}
      </span>
      <span className="text-slate-600">namespace {status.namespace} · queue {status.task_queue}</span>
      <span className={clsx("font-medium", status.workers > 0 ? "text-emerald-700" : "text-amber-800")} data-testid="worker-count">
        {status.workers > 0 ? `${status.workers} worker${status.workers > 1 ? "s" : ""} polling` : "no worker polling: workflows wait (nothing is lost)"}
      </span>
      {pending > 0 && <span className="text-amber-800">{pending} command{pending > 1 ? "s" : ""} waiting to be sent</span>}
      {!status.online && status.error && <span className="w-full text-xs text-amber-800">{status.error}</span>}
    </div>
  );
}

const FAULT_STEPS = ["medRecAdmission", "orderReview", "draftDischargeSummary", "execute", "explainAndRecommend", "triggerRegression"];

function Controls({ status }: { status: TemporalStatus }) {
  const queryClient = useQueryClient();
  const [encounter, setEncounter] = useState("");
  const [step, setStep] = useState("medRecAdmission");
  const [times, setTimes] = useState(2);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["wf-status"] });
  const start = useMutation({
    mutationFn: () => post<{ workflow_id: string }>("/api/workflows/journeys", { encounter_id: encounter.trim() }),
    onSuccess: () => { setEncounter(""); void queryClient.invalidateQueries({ queryKey: ["wf-list"] }); },
  });
  const inject = useMutation({ mutationFn: () => post("/api/workflows/faults", { step, times }), onSuccess: refresh });
  const clear = useMutation({ mutationFn: () => api("/api/workflows/faults", { method: "DELETE" }), onSuccess: refresh });
  const faults = Object.entries(status.faults ?? {});
  return (
    <div className="mb-4 grid gap-3 md:grid-cols-2">
      <Card title="Start a journey" className="h-full">
        <p className="mb-2 text-xs text-slate-500">Every admission starts one. For a patient admitted before Temporal was on, give the inpatient encounter id. Audited.</p>
        <form className="flex flex-wrap items-center gap-2" onSubmit={(e) => { e.preventDefault(); start.mutate(); }}>
          <input value={encounter} onChange={(e) => setEncounter(e.target.value)} placeholder="stay-00012" aria-label="Inpatient encounter id" className="h-8 w-44 rounded-lg border border-slate-300 px-2 text-sm" />
          <Button size="sm" variant="primary" type="submit" disabled={!encounter.trim()} loading={start.isPending}><PlayCircle className="size-4" />Start</Button>
          {start.data && <Link className="text-sm text-brand-700 hover:underline" to={`/workflows/${start.data.workflow_id}`}>{start.data.workflow_id}</Link>}
          {start.error && <span className="text-sm text-rose-600">{start.error.message}</span>}
        </form>
      </Card>
      <Card title={<span className="inline-flex items-center gap-1.5"><Bug className="size-4 text-rose-600" />Deliberate failure (demo)</span>} className="h-full">
        <p className="mb-2 text-xs text-slate-500">The next attempts of a step fail after their work is written; Temporal retries them (1 s, 4 s, 16 s) and the Tool Gateway's idempotency key keeps one copy. Audited.</p>
        <div className="flex flex-wrap items-center gap-2">
          <select value={step} onChange={(e) => setStep(e.target.value)} aria-label="Step" className="h-8 rounded-lg border border-slate-300 px-2 text-sm">
            {FAULT_STEPS.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
          <select value={times} onChange={(e) => setTimes(Number(e.target.value))} aria-label="Failures" className="h-8 rounded-lg border border-slate-300 px-2 text-sm">
            {[1, 2, 3].map((n) => <option key={n} value={n}>{n} failure{n > 1 ? "s" : ""}</option>)}
          </select>
          <Button size="sm" variant="danger" onClick={() => inject.mutate()} loading={inject.isPending} data-testid="inject-fault">Inject</Button>
          {faults.length > 0 && <Button size="sm" variant="ghost" onClick={() => clear.mutate()}>Clear</Button>}
          {inject.error && <span className="text-sm text-rose-600">{inject.error.message}</span>}
        </div>
        {faults.length > 0 && (
          <p className="mt-2 text-xs text-rose-700" data-testid="pending-faults">Pending: {faults.map(([s, n]) => `${s} × ${n}`).join(", ")}</p>
        )}
        {status.timers && (
          <p className="mt-2 text-[11px] text-slate-500">Timers for new workflows: sign-off {duration(status.timers.signoff_timeout_s)}, escalation {duration(status.timers.escalation_timeout_s)}, follow-up {duration(status.timers.followup_delay_s)}, verify {duration(status.timers.verify_after_s)} (wall clock).</p>
        )}
      </Card>
    </div>
  );
}
