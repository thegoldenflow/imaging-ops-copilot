import { useQuery } from "@tanstack/react-query";
import { ShieldCheck } from "lucide-react";
import { Badge, Card, EmptyState, ErrorState, Loading, PageHeader } from "../../components/ui";
import { api } from "../../lib/api";
import { dateTime } from "../../lib/format";
import type { Report } from "../../lib/types";

export function ReferrerPage() {
  const q = useQuery({ queryKey: ["my-reports"], queryFn: () => api<{ reports: Report[] }>("/api/reports/mine"), refetchInterval: 5000 });
  return (
    <div className="mx-auto max-w-3xl">
      <PageHeader title="My patients' reports" subtitle="Only reports signed by a radiologist appear here. Drafts are never shared." />
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
            <p className="mt-3 text-xs text-slate-500">Signed by {r.signed_by}. Prepared with AI assistance and reviewed by the radiologist.</p>
          </Card>
        ))}
      </div>
    </div>
  );
}
