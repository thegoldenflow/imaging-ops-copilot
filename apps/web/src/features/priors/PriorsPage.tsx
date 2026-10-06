import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Card, EmptyState, ErrorState, Loading, PageHeader, Stat } from "../../components/ui";
import { api, put } from "../../lib/api";
import type { RetrievalTask } from "../../lib/types";
import { PriorTask } from "../requisitions/PriorTask";

interface Board { tasks: RetrievalTask[]; counts: Record<string, number>; simulation: { failure_rate: number; latency_ms: number }; policy: { max_attempts: number; backoff_seconds: number } }

export function PriorsPage() {
  const queryClient = useQueryClient();
  const q = useQuery({ queryKey: ["priors"], queryFn: () => api<Board>("/api/priors/tasks"), refetchInterval: 2000 });
  const sim = useMutation({ mutationFn: (rate: number) => put("/api/priors/simulation", { failure_rate: rate }), onSuccess: () => queryClient.invalidateQueries({ queryKey: ["priors"] }) });
  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader title="Prior imaging retrieval" subtitle="Outside priors are requested automatically after booking, retried on failure, and linked to the exam when received." />
      {q.isLoading && <Loading />}
      {q.error && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.data && (
        <>
          <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-5">
            <Stat label="In progress" value={(q.data.counts.requested ?? 0) + (q.data.counts.retrying ?? 0)} />
            <Stat label="Received" value={q.data.counts.received ?? 0} tone="green" />
            <Stat label="Not found" value={q.data.counts.not_found ?? 0} />
            <Stat label="Failed" value={q.data.counts.failed ?? 0} tone={q.data.counts.failed ? "red" : undefined} />
            <Stat label="Retry policy" value={<span className="text-base">{q.data.policy.max_attempts} tries</span>} hint={`backoff ${q.data.policy.backoff_seconds}s × 2ⁿ`} />
          </div>
          <Card title="Mock outside archives" className="mb-4">
            <div className="flex flex-wrap items-center gap-3 text-sm">
              <span>Simulated failure rate:</span>
              {[0, 0.5, 1].map((rate) => (
                <button key={rate} onClick={() => sim.mutate(rate)} data-testid={`archive-failure-${rate}`}
                  className={`rounded-lg border px-3 py-1 ${q.data!.simulation.failure_rate === rate ? "border-brand-500 bg-brand-50 text-brand-700" : "border-slate-300"}`}>
                  {rate === 0 ? "Healthy" : rate === 1 ? "Outage (100%)" : "Flaky (50%)"}
                </button>
              ))}
              <span className="text-xs text-slate-500">Northview General Hospital, Riverside Health Centre, Lakeview Diagnostics (all simulated)</span>
            </div>
          </Card>
          <Card padded={false}>
            {q.data.tasks.length === 0 ? <EmptyState title="No retrievals yet" hint="Book a requisition that mentions outside imaging to create one." /> : (
              <div className="p-3">{q.data.tasks.map((t) => <PriorTask key={t.id} task={t} showPatient />)}</div>
            )}
          </Card>
        </>
      )}
    </div>
  );
}
