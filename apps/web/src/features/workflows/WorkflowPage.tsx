// One workflow's timeline (spec 6.5 UI): current stage, each step's status (done / waiting for sign-off / failed
// and retrying), owner, next step and duration. Retry and skip are for the operations manager and admin only;
// each is audited and reaches the workflow as a signal (the workflow decides, the page only asks).

import { useMutation, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { ArrowLeft, ArrowRight, Bell, FileText, ListChecks, RotateCw, SkipForward, TowerControl, UserRound } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Badge, Button, Card, ErrorState, Loading, PageHeader } from "../../components/ui";
import { post } from "../../lib/api";
import { dateTime } from "../../lib/format";
import { StepIcon, StepStatusPill } from "./StepStatus";
import { StatusBanner } from "./WorkflowsPage";
import { duration, OWNER_LABEL, useWorkflow, useWorkflowStatus, WORKFLOW_LABEL, type Step, type WorkflowDetail } from "./api";

export function WorkflowPage() {
  const { workflowId = "" } = useParams();
  const wf = useWorkflow(workflowId);
  const status = useWorkflowStatus();
  return (
    <div>
      <Link to="/workflows" className="mb-2 inline-flex items-center gap-1 text-sm text-slate-500 hover:text-slate-800"><ArrowLeft className="size-4" />Workflows</Link>
      {wf.isLoading && <Loading />}
      {wf.error && <ErrorState error={wf.error} onRetry={() => wf.refetch()} />}
      {wf.data && (
        <>
          <PageHeader
            title={WORKFLOW_LABEL[wf.data.workflow_type]}
            subtitle={<span data-testid="workflow-id">{wf.data.id}</span>}
            actions={<Badge tone={wf.data.status === "completed" ? "green" : wf.data.status === "failed" ? "red" : "blue"}>{wf.data.status}</Badge>}
          />
          <StatusBanner status={status.data} />
          <Summary w={wf.data} />
          <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_18rem]">
            <Card title="Timeline" padded={false}>
              <ol className="divide-y divide-slate-100" data-testid="timeline">
                {wf.data.steps.map((s) => (
                  <StepRow key={s.key} w={wf.data} s={s} canControl={wf.data.can_control && !!status.data?.configured} />
                ))}
              </ol>
            </Card>
            <Side w={wf.data} />
          </div>
        </>
      )}
    </div>
  );
}

function Summary({ w }: { w: WorkflowDetail }) {
  const current = w.steps.find((s) => s.key === w.current_step);
  const next = w.steps.find((s) => s.key === w.next_step);
  return (
    <div className="mb-4 grid gap-3 sm:grid-cols-3">
      <div className="rounded-xl border border-slate-200 bg-white p-3" data-testid="current-stage">
        <p className="text-xs text-slate-500">Current stage</p>
        <p className="mt-0.5 font-semibold text-slate-900">{current ? current.label : w.status === "completed" ? "Completed" : "–"}</p>
        {current && <StepStatusPill step={current} className="mt-1" />}
      </div>
      <div className="rounded-xl border border-slate-200 bg-white p-3">
        <p className="text-xs text-slate-500">Next step</p>
        <p className="mt-0.5 font-semibold text-slate-900">{next ? next.label : "–"}</p>
        {next && <p className="text-xs text-slate-500">owner: {OWNER_LABEL[next.owner] ?? next.owner}</p>}
      </div>
      <div className="rounded-xl border border-slate-200 bg-white p-3">
        <p className="text-xs text-slate-500">Subject</p>
        <p className="mt-0.5 font-semibold text-slate-900">
          {w.encounter_id ? `Encounter ${w.encounter_id}` : w.exception_id ? w.exception_id : w.run_id ?? "–"}
        </p>
        <p className="text-xs text-slate-500">
          {w.unit_id ? `Unit ${w.unit_id} · ` : ""}started {w.started_at ? dateTime(w.started_at) : "–"}
        </p>
        {w.exception_id && (
          <Link to="/control-tower" className="mt-1 inline-flex items-center gap-1 text-xs text-brand-700 hover:underline"><TowerControl className="size-3.5" />Control Tower</Link>
        )}
      </div>
    </div>
  );
}

function StepRow({ w, s, canControl }: { w: WorkflowDetail; s: Step; canControl: boolean }) {
  const queryClient = useQueryClient();
  const [note, setNote] = useState("");
  const [asking, setAsking] = useState<"retry" | "skip" | null>(null);
  const control = useMutation({
    mutationFn: (action: "retry" | "skip") => post(`/api/workflows/${encodeURIComponent(w.id)}/steps/${s.key}/${action}`, { note }),
    onSuccess: () => { setAsking(null); setNote(""); void queryClient.invalidateQueries({ queryKey: ["wf", w.id] }); },
  });
  const current = s.key === w.current_step;
  const retry = canControl && w.status === "running" && s.status === "failed";
  const skip = canControl && w.status === "running" && (s.status === "failed" || s.status === "waiting");
  return (
    <li className={clsx("flex gap-3 px-4 py-3", current && "bg-brand-50/40")} data-step={s.key} data-step-status={s.status}>
      <StepIcon status={s.status} />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium text-slate-900">{s.label}</span>
          <StepStatusPill step={s} />
          {s.escalation > 0 && (
            <Badge tone="red"><Bell className="size-3" />{s.escalation === 1 ? "escalated" : "ops manager told"}</Badge>
          )}
          {s.stub && <Badge tone="slate" className="font-normal">{s.stub === "8.1 roadmap" ? "roadmap stub" : `placeholder until ${s.stub}`}</Badge>}
        </div>
        <p className="mt-0.5 text-xs text-slate-500">
          <UserRound className="mr-0.5 inline size-3" />{OWNER_LABEL[s.owner] ?? s.owner}
          {s.started_at && <> · started {dateTime(s.started_at)}</>}
          {(s.duration_s != null && s.status !== "pending") && <> · {s.status === "done" || s.status === "skipped" ? "took" : "so far"} {duration(s.duration_s)}</>}
          {s.attempts > 1 && <> · <span className="font-medium text-amber-800">attempt {s.attempts}</span></>}
        </p>
        {s.detail && <p className="mt-1 text-sm text-slate-700" data-testid={`detail-${s.key}`}>{s.detail}</p>}
        {s.waiting_for && s.status === "waiting" && (
          <p className="mt-1 text-xs text-amber-800">Waiting for the {OWNER_LABEL[s.waiting_for.role]?.toLowerCase() ?? s.waiting_for.role} on {s.waiting_for.refs.join(", ")}</p>
        )}
        {(s.refs.length > 0 || s.tasks.length > 0) && (
          <div className="mt-1 flex flex-wrap gap-1">
            {s.tasks.map((t) => (
              <Badge key={t.task_id + t.kind} tone={t.kind === "notify" || t.kind === "failed" ? "red" : t.kind === "escalation" ? "amber" : "slate"}>
                <ListChecks className="size-3" />{t.kind} · Task {t.task_id} → {OWNER_LABEL[t.role] ?? t.role}
              </Badge>
            ))}
            {s.refs.slice(0, 6).map((r) => <Badge key={r} tone="slate" className="font-normal"><FileText className="size-3" />{r}</Badge>)}
          </div>
        )}
        {(retry || skip) && (
          <div className="mt-2 flex flex-wrap items-center gap-2">
            {asking ? (
              <>
                <input value={note} onChange={(e) => setNote(e.target.value)} placeholder={`Why ${asking} (audited)`} className="h-8 w-64 rounded-lg border border-slate-300 px-2 text-sm" aria-label="Reason" />
                <Button size="sm" variant={asking === "skip" ? "danger" : "primary"} onClick={() => control.mutate(asking)} loading={control.isPending} data-testid={`confirm-${asking}`}>Confirm {asking}</Button>
                <Button size="sm" variant="ghost" onClick={() => setAsking(null)}>Cancel</Button>
              </>
            ) : (
              <>
                {retry && <Button size="sm" onClick={() => setAsking("retry")} data-testid={`retry-${s.key}`}><RotateCw className="size-3.5" />Retry</Button>}
                {skip && <Button size="sm" variant="ghost" onClick={() => setAsking("skip")} data-testid={`skip-${s.key}`}><SkipForward className="size-3.5" />Skip</Button>}
              </>
            )}
            {control.isSuccess && <span className="text-xs text-emerald-700">Sent to the workflow</span>}
            {control.error && <span className="text-xs text-rose-600">{control.error.message}</span>}
          </div>
        )}
      </div>
      {current && <ArrowRight className="mt-1 size-4 shrink-0 text-brand-500" aria-label="current step" />}
    </li>
  );
}

function Side({ w }: { w: WorkflowDetail }) {
  const t = w.temporal;
  return (
    <div className="space-y-4">
      <Card title="Notes">
        {w.notes.length === 0 ? <p className="text-xs text-slate-500">No escalations, and no retries or skips by people.</p> : (
          <ul className="space-y-1.5 text-xs" data-testid="notes">
            {w.notes.slice().reverse().map((n, i) => (
              <li key={i}><span className="text-slate-500">{dateTime(n.at)}</span> · {n.text}</li>
            ))}
          </ul>
        )}
      </Card>
      <Card title="Temporal">
        {!t ? <p className="text-xs text-slate-500">Temporal is off: this timeline is the last one recorded.</p> : t.error ? (
          <p className="text-xs text-amber-800">Unreachable: {t.error}</p>
        ) : (
          <dl className="space-y-1 text-xs">
            <div className="flex justify-between"><dt className="text-slate-500">Execution</dt><dd className="font-medium">{t.status ?? "–"}</dd></div>
            <div className="flex justify-between"><dt className="text-slate-500">History events</dt><dd className="font-medium">{t.history_length ?? "–"}</dd></div>
            {(t.pending_activities ?? []).map((p) => (
              <div key={p.activity} className="rounded bg-slate-50 p-1.5">
                <p className="font-medium">{p.activity} · attempt {p.attempt}</p>
                {p.last_failure && <p className="text-rose-700">{p.last_failure}</p>}
              </div>
            ))}
          </dl>
        )}
        <p className="mt-2 text-[11px] text-slate-500">Times are the wall clock (Temporal's timers); the simulated hospital clock is separate.</p>
      </Card>
    </div>
  );
}
