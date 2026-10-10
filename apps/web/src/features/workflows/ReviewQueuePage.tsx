// The review queue (spec 6.4, 6.5): what waits for the signed-in role. Approval requests for privileged AI
// actions, AI recommendations, workflow sign-offs (a durable workflow waits on each), and low-confidence AI output
// to confirm or correct; a correction becomes the agent's next eval case. Every decision is audited and tells the
// waiting workflow.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, ClipboardCheck, ExternalLink, PencilLine, ShieldCheck, Sparkles, TowerControl, Workflow, XCircle } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { AiBadge, Badge, Button, Card, EmptyState, ErrorState, Loading, PageHeader } from "../../components/ui";
import { api, post } from "../../lib/api";
import { dateTime } from "../../lib/format";
import type { AgentReview, QueueTask } from "./api";

const KIND = {
  "approval-request": { label: "Approve an AI action", icon: ShieldCheck, tone: "red" as const },
  "ai-review": { label: "Review AI output", icon: Sparkles, tone: "ai" as const },
  "workflow-signoff": { label: "Workflow sign-off", icon: ClipboardCheck, tone: "blue" as const },
};

export function ReviewQueuePage() {
  const queue = useQuery({ queryKey: ["review-queue"], queryFn: () => api<{ tasks: QueueTask[] }>("/api/agents/approvals"), refetchInterval: 5000 });
  return (
    <div>
      <PageHeader
        title="Review queue"
        subtitle="Approvals, sign-offs and AI output waiting for your role. AI suggests; a person decides, and the decision is audited."
      />
      <Card padded={false}>
        {queue.isLoading && <Loading />}
        {queue.error && <ErrorState error={queue.error} onRetry={() => queue.refetch()} />}
        {queue.data && queue.data.tasks.length === 0 && <EmptyState title="Nothing waiting for you" icon={<ClipboardCheck className="size-8" />} />}
        {queue.data && queue.data.tasks.length > 0 && (
          <ul className="divide-y divide-slate-100" data-testid="review-queue">
            {queue.data.tasks.map((t) => <QueueRow key={t.task_id} t={t} />)}
          </ul>
        )}
      </Card>
    </div>
  );
}

function QueueRow({ t }: { t: QueueTask }) {
  const queryClient = useQueryClient();
  const [note, setNote] = useState("");
  const [correcting, setCorrecting] = useState(false);
  const decide = useMutation({
    mutationFn: (decision: "approve" | "reject") => post(`/api/agents/tasks/${t.task_id}/decision`, { decision, note }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["review-queue"] }),
  });
  const kind = KIND[t.kind] ?? KIND["ai-review"];
  const Icon = kind.icon;
  const controlTower = t.agent_id === "control_tower";
  return (
    <li className="p-4" data-task={t.task_id} data-kind={t.kind}>
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={kind.tone}><Icon className="size-3" />{t.review_id ? "Low AI confidence" : kind.label}</Badge>
        {t.priority && t.priority !== "routine" && <Badge tone="amber">{t.priority}</Badge>}
        <span className="text-xs text-slate-500">Task {t.task_id}{t.encounter_id ? ` · encounter ${t.encounter_id}` : ""}{t.authored_on ? ` · ${dateTime(t.authored_on)}` : ""}</span>
        {t.agent_id && <AiBadge label={t.agent_id} agent={t.agent_id} />}
        {t.workflow_id && (
          <Link to={`/workflows/${encodeURIComponent(t.workflow_id)}`} className="inline-flex items-center gap-1 text-xs text-brand-700 hover:underline"><Workflow className="size-3.5" />{t.workflow_id}</Link>
        )}
      </div>
      <p className="mt-1 text-sm text-slate-800">{t.description}</p>
      {t.recommendation && !controlTower && <p className="mt-1 rounded-lg bg-ai-50 p-2 text-sm text-slate-700">{t.recommendation}</p>}
      {controlTower ? (
        <Link to="/control-tower" className="mt-2 inline-flex items-center gap-1 text-sm text-brand-700 hover:underline"><TowerControl className="size-4" />Decide in the Control Tower's action drawer<ExternalLink className="size-3" /></Link>
      ) : correcting && t.review_id ? (
        <Correction reviewId={t.review_id} onDone={() => { setCorrecting(false); void queryClient.invalidateQueries({ queryKey: ["review-queue"] }); }} />
      ) : (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Note (optional)" className="h-8 w-64 rounded-lg border border-slate-300 px-2 text-sm" aria-label="Note" />
          {t.review_id && <Button size="sm" variant="ai" onClick={() => setCorrecting(true)} data-testid="correct"><PencilLine className="size-3.5" />Correct</Button>}
          <Button size="sm" variant="primary" onClick={() => decide.mutate("approve")} loading={decide.isPending} data-testid="approve">
            <CheckCircle2 className="size-3.5" />{t.kind === "approval-request" ? "Approve" : t.review_id ? "Confirm as is" : t.kind === "workflow-signoff" ? "Confirm" : "Accept"}
          </Button>
          <Button size="sm" variant="ghost" onClick={() => decide.mutate("reject")} loading={decide.isPending} data-testid="reject">
            <XCircle className="size-3.5" />{t.review_id ? "Dismiss" : "Reject"}
          </Button>
          {decide.error && <span className="text-sm text-rose-600">{decide.error.message}</span>}
        </div>
      )}
    </li>
  );
}

function Correction({ reviewId, onDone }: { reviewId: string; onDone: () => void }) {
  const review = useQuery({ queryKey: ["agent-review", reviewId], queryFn: () => api<AgentReview>(`/api/workflows/reviews/${reviewId}`) });
  const [values, setValues] = useState<Record<string, string>>({});
  const [note, setNote] = useState("");
  const save = useMutation({
    mutationFn: () => post(`/api/workflows/reviews/${reviewId}`, { correction: values, note }),
    onSuccess: onDone,
  });
  if (review.isLoading) return <Loading />;
  if (review.error || !review.data) return <ErrorState error={review.error} />;
  const r = review.data;
  return (
    <div className="mt-2 space-y-2 rounded-lg border border-ai-100 bg-ai-50/50 p-3" data-testid="correction-form">
      <p className="text-xs text-slate-600">
        {r.agent_id} answered with confidence {r.confidence.toFixed(2)} (threshold {r.threshold.toFixed(2)}). Your correction becomes an eval case (source human_review) and the agent's regression eval runs.
      </p>
      <pre className="max-h-28 overflow-auto whitespace-pre-wrap rounded bg-white p-2 text-xs text-slate-700">{r.input_excerpt}</pre>
      <p className="text-[11px] text-slate-500">{r.note}</p>
      <div className="flex flex-wrap items-end gap-3">
        {Object.entries(r.correctable).filter(([, options]) => options.length > 0).map(([field, options]) => (
          <label key={field} className="text-xs text-slate-600">
            {field} <span className="text-slate-400">(AI: {String(r.output[field] ?? "–")})</span>
            <select
              value={values[field] ?? String(r.output[field] ?? "")}
              onChange={(e) => setValues({ ...values, [field]: e.target.value })}
              className="mt-0.5 block h-8 rounded-lg border border-slate-300 bg-white px-2 text-sm"
              data-field={field}
            >
              {options.map((o) => <option key={o} value={o}>{o}</option>)}
            </select>
          </label>
        ))}
        <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Why (optional)" className="h-8 w-56 rounded-lg border border-slate-300 bg-white px-2 text-sm" aria-label="Correction note" />
        <Button size="sm" variant="ai" onClick={() => save.mutate()} loading={save.isPending} data-testid="save-correction">Save correction</Button>
        <Button size="sm" variant="ghost" onClick={onDone}>Cancel</Button>
      </div>
      {save.error && <p className="text-sm text-rose-600">{save.error.message}</p>}
    </div>
  );
}
