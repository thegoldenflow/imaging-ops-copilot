import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BellRing, ShieldCheck } from "lucide-react";
import { Badge, Button, Card, EmptyState, ErrorState, Loading, PageHeader } from "../../components/ui";
import { api, post } from "../../lib/api";
import { dateTime } from "../../lib/format";
import type { CriticalCase, Report } from "../../lib/types";

function CriticalAlerts() {
  const queryClient = useQueryClient();
  const q = useQuery({ queryKey: ["my-critical"], queryFn: () => api<{ cases: CriticalCase[] }>("/api/critical/mine"), refetchInterval: 5000 });
  const ack = useMutation({
    mutationFn: (id: string) => post(`/api/critical/${id}/acknowledge-portal`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["my-critical"] }),
  });
  const cases = q.data?.cases ?? [];
  if (cases.length === 0) return null;
  return (
    <Card title={<span className="flex items-center gap-2"><BellRing className="size-4 text-rose-600" /> Results needing your attention</span>} className="mb-4">
      <ul className="divide-y divide-slate-100">
        {cases.map((c) => (
          <li key={c.id} className="flex flex-wrap items-center justify-between gap-3 py-2.5" data-testid={`my-critical-${c.id}`}>
            <div className="text-sm">
              <p className="font-medium text-slate-900">{c.finding}</p>
              <p className="text-xs text-slate-500">{c.patient_name}, {c.exam_name} ({c.level_label}), reported {dateTime(c.created_at)}</p>
            </div>
            {c.acknowledgement ? (
              <Badge tone="green">Acknowledged {dateTime(c.acknowledgement.at)}</Badge>
            ) : (
              <Button size="sm" variant="danger" loading={ack.isPending && ack.variables === c.id} onClick={() => ack.mutate(c.id)}>I have received this result</Button>
            )}
          </li>
        ))}
      </ul>
      {ack.error && <p className="text-sm text-rose-600" role="alert">{(ack.error as Error).message}</p>}
    </Card>
  );
}

export function ReferrerPage() {
  const q = useQuery({ queryKey: ["my-reports"], queryFn: () => api<{ reports: Report[] }>("/api/reports/mine"), refetchInterval: 5000 });
  return (
    <div className="mx-auto max-w-3xl">
      <PageHeader title="My patients' reports" subtitle="Only reports signed by a radiologist appear here. Drafts are never shared." />
      <CriticalAlerts />
      {q.isLoading && <Loading />}
      {q.error && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.data?.reports.length === 0 && (
        <Card><EmptyState title="No signed reports yet" hint="In the demo, sign Mei Chen's chest X-ray as the radiologist first." /></Card>
      )}
      <div className="space-y-4">
        {q.data?.reports.map((r) => (
          <Card key={r.id} title={`${r.study.patient_name} · ${r.study.exam_name}`} actions={<Badge tone="green"><ShieldCheck className="size-3.5" /> Signed {dateTime(r.signed_at!)}</Badge>}>
            <dl className="space-y-2 text-sm">
              {r.sections.filter((s) => s.status !== "deleted" && s.final_text).map((s) => (
                <div key={s.key}>
                  <dt className="text-xs font-semibold text-slate-500 uppercase">{s.label}</dt>
                  <dd className={s.key === "impression" ? "font-medium text-slate-900" : "text-slate-700"}>{s.final_text}</dd>
                </div>
              ))}
            </dl>
            <p className="mt-3 text-xs text-slate-500">Signed by {r.signed_by}.{r.source === "ai_draft" ? " Prepared with AI assistance and reviewed by the radiologist." : ""}</p>
          </Card>
        ))}
      </div>
    </div>
  );
}
