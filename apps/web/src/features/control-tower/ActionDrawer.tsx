// The action drawer (spec 7.1): the AI's narrative and recommended actions for one exception, the engine's facts
// and evidence, and the decision: approve (a Task for each action's owner role, audited), reject with a reason,
// or defer with a reminder time. The AI never executes anything; a person decides here.

import { useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { AlarmClock, Check, CheckCircle2, FileSearch, FlaskConical, Loader2, ShieldCheck, Sparkles, Workflow, X } from "lucide-react";
import { Link } from "react-router-dom";
import { useEffect, useMemo, useState } from "react";
import { ApiError } from "../../lib/api";
import { useAgent } from "../../lib/agents";
import {
  approveException, deferException, hhmm, rejectException, ROLE_SHORT, RULE_LABEL, useException,
  type ExceptionDetail, type MenuItem, type RecommendedAction,
} from "./api";
import { Drawer, ErrorPanel, SeverityPill, SkeletonRows, StatusPill } from "./kit";

const FACT_LABELS: Record<string, string> = {
  occupancy_pct: "Occupancy %", occupied: "Occupied", beds: "Beds", free_beds: "Free beds", cleaning_beds: "Cleaning",
  ed_boarders: "ED boarders", expected_admissions_24h: "Expected admissions (24 h)",
  expected_discharges_24h: "Expected discharges (24 h)", net_gap: "Net gap", discharge_ready: "Discharge-ready",
  alc_patients: "ALC patients", boarders_over_2h: "Boarders over 2 h", longest_minutes: "Longest wait (min)",
  boarders: "Boarders", booked_start: "Booked start", booked_minutes: "Booked (min)",
  predicted_minutes: "Predicted (min)", overrun_minutes: "Over (min)", room_predicted_end: "Room's list ends",
  minutes_past_block: "Past the block (min)", minutes_to_start: "Minutes to start", open_count: "Open items",
};

export function ActionDrawer({ id, onClose }: { id: string | null; onClose: () => void }) {
  const detail = useException(id);
  const d = detail.data;
  return (
    <Drawer
      open={Boolean(id)}
      onClose={onClose}
      testId="action-drawer"
      width={440}
      title={
        d ? (
          <div className="space-y-1">
            <div className="flex items-center gap-2"><SeverityPill severity={d.severity} /><span className="text-[11px] font-normal text-ct-muted">{RULE_LABEL[d.rule]} · {d.id}</span></div>
            <p>{d.title}</p>
          </div>
        ) : "Exception"
      }
    >
      {detail.isLoading && <><p className="px-4 pt-3 text-xs text-ct-muted">Writing the narrative…</p><SkeletonRows rows={6} /></>}
      {detail.error && <ErrorPanel error={detail.error} onRetry={() => detail.refetch()} />}
      {d && <Body key={`${d.id}:${d.status}`} d={d} />}
    </Drawer>
  );
}

function AiLabel({ source }: { source: "model" | "template" }) {
  const spec = useAgent("control_tower");
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      {source === "model" ? (
        <StatusPill tone="ai" icon={Sparkles}>AI-generated</StatusPill>
      ) : (
        <StatusPill tone="muted" icon={ShieldCheck} title="The model was unavailable or its answer failed the guards: the rule engine's own text">Rule-engine template</StatusPill>
      )}
      {spec?.not_evaluated_flag && (
        <span data-testid="not-evaluated-control_tower" title={`${spec.agent_id} ${spec.version}: ${spec.eval_status.note || spec.eval_status.status}`}>
          <StatusPill tone="warning" icon={FlaskConical}>Not evaluated</StatusPill>
        </span>
      )}
    </span>
  );
}

function Body({ d }: { d: ExceptionDetail }) {
  const queryClient = useQueryClient();
  const n = d.narration;
  const recommended = n?.recommended_actions ?? [];
  const recommendedIds = useMemo(() => new Set(recommended.map((a) => a.action_id)), [recommended]);
  const others = d.menu.filter((m) => !recommendedIds.has(m.action_id));
  const [chosen, setChosen] = useState<Set<string>>(() => new Set(recommendedIds));
  const [note, setNote] = useState("");
  const [reason, setReason] = useState("");
  const [rejecting, setRejecting] = useState(false);
  const [minutes, setMinutes] = useState(60);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const live = d.status === "open" || d.status === "deferred";

  useEffect(() => setChosen(new Set(recommendedIds)), [recommendedIds]);

  const toggle = (id: string) => setChosen((s) => {
    const next = new Set(s);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    return next;
  });

  const decide = async (name: string, fn: () => Promise<ExceptionDetail>) => {
    setBusy(name);
    setError(null);
    try {
      const out = await fn();
      queryClient.setQueryData(["ct-exception", d.id], (old: ExceptionDetail | undefined) => (old ? { ...old, ...out } : out));
      await queryClient.invalidateQueries({ queryKey: ["ct-board"] });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The decision was not saved");
    } finally {
      setBusy(null);
    }
  };

  const facts = Object.entries(d.facts).filter(([k, v]) => FACT_LABELS[k] && (typeof v === "number" || typeof v === "string"));

  return (
    <div className="space-y-4 px-4 py-3 text-sm text-ct-text">
      <section aria-label="Narrative">
        <div className="mb-1.5 flex items-center justify-between gap-2">
          {n ? <AiLabel source={n.source} /> : <StatusPill tone="muted">narrative pending</StatusPill>}
          {n && <span className="text-[11px] text-ct-muted">facts as of {hhmm(n.facts_as_of)}</span>}
        </div>
        <p className="leading-relaxed" data-testid="narrative">{n?.narrative ?? d.summary}</p>
        {n?.source === "template" && n.problems.length > 0 && (
          <details className="mt-1 text-[11px] text-ct-muted"><summary>Why the template ({n.problems.length})</summary>
            <ul className="mt-1 list-disc pl-4">{n.problems.map((p) => <li key={p}>{p}</li>)}</ul>
          </details>
        )}
      </section>

      {facts.length > 0 && (
        <section aria-label="Facts from the rule engine">
          <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-ct-muted">From the rule engine</h3>
          <dl className="grid grid-cols-2 gap-x-3 gap-y-1 rounded-lg bg-ct-raised p-2.5 text-xs" data-testid="facts">
            {facts.map(([k, v]) => (
              <div key={k} className="flex justify-between gap-2"><dt className="text-ct-muted">{FACT_LABELS[k]}</dt><dd className="tabular font-medium">{String(v)}</dd></div>
            ))}
          </dl>
        </section>
      )}

      <section aria-label="Recommended actions">
        <h3 className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-ct-muted">Recommended actions</h3>
        <ul className="space-y-2" data-testid="actions">
          {recommended.map((a) => (
            <ActionRow key={a.action_id} a={a} checked={chosen.has(a.action_id)} onToggle={() => toggle(a.action_id)} disabled={!live || !d.can_decide} />
          ))}
        </ul>
        {others.length > 0 && live && d.can_decide && (
          <details className="mt-2">
            <summary className="cursor-pointer text-xs text-ct-muted">More actions from the engine ({others.length})</summary>
            <ul className="mt-2 space-y-2">
              {others.map((m) => <ActionRow key={m.action_id} a={fromMenu(m)} checked={chosen.has(m.action_id)} onToggle={() => toggle(m.action_id)} />)}
            </ul>
          </details>
        )}
      </section>

      {n && (
        <section aria-label="Evidence">
          <h3 className="mb-1 flex items-center gap-1 text-[11px] font-semibold uppercase tracking-wide text-ct-muted"><FileSearch className="size-3" />Evidence (checked in the EHR)</h3>
          <div className="flex flex-wrap gap-1" data-testid="evidence">
            {n.evidence_refs.map((r) => <code key={r} className="rounded bg-ct-raised px-1.5 py-0.5 text-[11px] text-ct-muted">{r}</code>)}
          </div>
          {n.unresolved_refs.length > 0 && <p className="mt-1 text-[11px] text-ct-warning">{n.unresolved_refs.length} reference(s) did not resolve and are not shown.</p>}
        </section>
      )}

      <section aria-label="Decision" className="border-t border-ct-border pt-3">
        <WorkflowLine d={d} />
        {d.decision && <DecisionSummary d={d} />}
        {live && d.can_decide && (
          <div className="space-y-2.5">
            <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Note for the team (optional)" maxLength={500}
              className="h-8 w-full rounded-md border border-ct-border bg-ct-raised px-2 text-xs text-ct-text placeholder:text-ct-muted" />
            <button
              onClick={() => decide("approve", () => approveException(d.id, [...chosen], note))}
              disabled={chosen.size === 0 || busy !== null}
              className="inline-flex h-9 w-full items-center justify-center gap-1.5 rounded-md bg-ct-accent text-sm font-medium text-white hover:opacity-90 disabled:opacity-40"
              data-testid="approve"
            >
              {busy === "approve" ? <Loader2 className="size-4 animate-spin" /> : <Check className="size-4" />}
              Approve {chosen.size} action{chosen.size === 1 ? "" : "s"} → Tasks
            </button>
            <div className="flex gap-2">
              <select value={minutes} onChange={(e) => setMinutes(Number(e.target.value))} aria-label="Defer by"
                className="h-8 rounded-md border border-ct-border bg-ct-raised px-1.5 text-xs text-ct-text">
                {[15, 30, 60, 120, 240].map((m) => <option key={m} value={m}>{m < 60 ? `${m} min` : `${m / 60} h`}</option>)}
              </select>
              <button onClick={() => decide("defer", () => deferException(d.id, minutes))} disabled={busy !== null}
                className="inline-flex h-8 flex-1 items-center justify-center gap-1.5 rounded-md border border-ct-border text-xs font-medium text-ct-text hover:bg-ct-raised disabled:opacity-40" data-testid="defer">
                <AlarmClock className="size-3.5" />Defer
              </button>
              <button onClick={() => setRejecting((r) => !r)} disabled={busy !== null}
                className="inline-flex h-8 flex-1 items-center justify-center gap-1.5 rounded-md border border-ct-border text-xs font-medium text-ct-critical hover:bg-ct-critical-soft disabled:opacity-40" data-testid="reject-open">
                <X className="size-3.5" />Reject
              </button>
            </div>
            {rejecting && (
              <div className="space-y-1.5 rounded-lg bg-ct-raised p-2.5">
                <label className="block text-[11px] text-ct-muted" htmlFor="reject-reason">Reason (required)</label>
                <textarea id="reject-reason" value={reason} onChange={(e) => setReason(e.target.value)} rows={2} maxLength={500}
                  className="w-full rounded-md border border-ct-border bg-ct-surface p-2 text-xs text-ct-text" data-testid="reject-reason" />
                <button onClick={() => decide("reject", () => rejectException(d.id, reason))} disabled={reason.trim().length < 5 || busy !== null}
                  className="inline-flex h-8 w-full items-center justify-center gap-1.5 rounded-md bg-ct-critical text-xs font-medium text-white disabled:opacity-40" data-testid="reject">
                  {busy === "reject" && <Loader2 className="size-3.5 animate-spin" />}Reject with this reason
                </button>
              </div>
            )}
            {error && <p className="text-xs text-ct-critical" role="alert">{error}</p>}
          </div>
        )}
        {live && !d.can_decide && (
          <p className="text-xs text-ct-muted">Decided by the bed manager or a charge nurse of {d.unit_id}. You can follow it here.</p>
        )}
        {!live && !d.decision && <p className="text-xs text-ct-muted">The condition cleared at {hhmm(d.cleared_at)} before anyone decided.</p>}
      </section>
    </div>
  );
}

function fromMenu(m: MenuItem): RecommendedAction {
  return { action_id: m.action_id, action: m.label, rationale: m.why, owner_role: m.owner_role, expected_effect: m.effect };
}

function ActionRow({ a, checked, onToggle, disabled }: { a: RecommendedAction; checked: boolean; onToggle: () => void; disabled?: boolean }) {
  return (
    <li>
      <label className={clsx("flex gap-2 rounded-lg border p-2.5", checked ? "border-ct-accent bg-ct-raised" : "border-ct-border", disabled ? "cursor-default" : "cursor-pointer")}>
        <input type="checkbox" checked={checked} onChange={onToggle} disabled={disabled} className="mt-0.5 accent-[var(--ct-accent)]" data-testid={`action-${a.action_id}`} />
        <span className="min-w-0 space-y-0.5">
          <span className="block text-xs font-medium">{a.action}</span>
          <span className="block text-[11px] text-ct-muted">{a.rationale}</span>
          <span className="flex flex-wrap items-center gap-1.5 text-[11px]">
            <StatusPill tone="info" icon={null}>{ROLE_SHORT[a.owner_role]}</StatusPill>
            <span className="text-ct-ok">→ {a.expected_effect}</span>
          </span>
        </span>
      </label>
    </li>
  );
}

function WorkflowLine({ d }: { d: ExceptionDetail }) {
  const w = d.workflow;
  if (!w) return null;
  return (
    <Link to={`/workflows/${encodeURIComponent(w.id)}`} className="mb-3 flex items-center gap-1.5 rounded-lg border border-ct-border p-2 text-xs text-ct-text hover:border-ct-accent" data-testid="exception-workflow">
      <Workflow className="size-3.5 text-ct-accent" />
      <span className="flex-1">Workflow {w.id} · {w.status === "running" ? `${w.current_label ?? "–"} (${w.done}/${w.total})` : w.status}</span>
      {d.outcome?.verified && (
        <span className="text-ct-muted">{d.outcome.verified.occupancy_after_pct != null ? `${d.outcome.verified.occupancy_before_pct}% → ${d.outcome.verified.occupancy_after_pct}%` : d.outcome.resolved ? "resolved" : "still present"}</span>
      )}
    </Link>
  );
}

function DecisionSummary({ d }: { d: ExceptionDetail }) {
  const dec = d.decision!;
  return (
    <div className="mb-3 space-y-1.5 rounded-lg bg-ct-raised p-2.5 text-xs" data-testid="decision">
      <p className="flex items-center gap-1.5 font-medium">
        {dec.decision === "approved" ? <CheckCircle2 className="size-4 text-ct-ok" /> : dec.decision === "rejected" ? <X className="size-4 text-ct-critical" /> : <AlarmClock className="size-4 text-ct-info" />}
        {dec.decision === "approved" ? "Approved" : dec.decision === "rejected" ? "Rejected" : `Deferred until ${hhmm(dec.remind_at ?? null)}`} by {dec.name} ({ROLE_SHORT[dec.role as keyof typeof ROLE_SHORT] ?? dec.role}) at {hhmm(dec.at)}
      </p>
      {dec.note && <p className="text-ct-muted">“{dec.note}”</p>}
      {dec.executed_by === "workflow" && <p className="text-ct-muted">Executed by the exception's durable workflow (retried by Temporal, idempotent Tasks).</p>}
      {dec.actions && (
        <ul className="space-y-1" data-testid="created-tasks">
          {dec.actions.map((a) => (
            <li key={a.action_id} className="flex items-center justify-between gap-2">
              <span className="min-w-0 truncate">{a.label}</span>
              <span className="shrink-0 text-ct-muted">{a.task_id ? <>Task <code className="text-ct-text">{a.task_id}</code> → {ROLE_SHORT[a.owner_role]}</> : a.status === "queued" ? <span className="inline-flex items-center gap-1 text-ct-info"><Loader2 className="size-3 animate-spin" />queued for the workflow</span> : <span className="text-ct-critical">not created: {a.detail}</span>}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
