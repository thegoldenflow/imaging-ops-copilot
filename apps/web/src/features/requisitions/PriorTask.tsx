import { useMutation, useQueryClient } from "@tanstack/react-query";
import { RotateCcw } from "lucide-react";
import { Badge, Button } from "../../components/ui";
import { post } from "../../lib/api";
import { time } from "../../lib/format";
import type { RetrievalTask } from "../../lib/types";

export const PRIOR_TONE: Record<string, "slate" | "amber" | "green" | "red" | "blue"> = {
  requested: "blue", retrying: "amber", received: "green", not_found: "slate", failed: "red",
};

export function PriorTask({ task, showPatient }: { task: RetrievalTask; showPatient?: boolean }) {
  const queryClient = useQueryClient();
  const retry = useMutation({ mutationFn: () => post(`/api/priors/tasks/${task.id}/retry`), onSuccess: () => queryClient.invalidateQueries() });
  return (
    <div className="mb-2 rounded-lg border border-slate-200 p-2 text-xs" data-testid={`prior-${task.status}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="flex flex-wrap items-center gap-2">
          <Badge tone={PRIOR_TONE[task.status]}>{task.status.replace("_", " ")}</Badge>
          <span className="font-medium text-slate-800">{task.facility}</span>
          {showPatient && <span className="text-slate-500">{task.patient_name} · {task.exam_name}</span>}
          <span className="text-slate-400">attempt {task.attempts}/{task.max_attempts}</span>
        </span>
        {["failed", "not_found"].includes(task.status) && (
          <Button size="sm" variant="ghost" loading={retry.isPending} onClick={() => retry.mutate()}><RotateCcw className="size-3" /> Retry</Button>
        )}
      </div>
      <p className="mt-1 text-slate-500">{task.reason}</p>
      <ol className="mt-1 space-y-0.5 text-slate-600">
        {task.events.map((e, i) => <li key={i}><span className="tabular text-slate-400">{time(e.ts)}</span> {e.text}</li>)}
      </ol>
    </div>
  );
}
