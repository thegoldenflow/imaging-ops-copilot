import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, ShieldAlert, ShieldCheck } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { Badge, Card, EmptyState, ErrorState, Loading, PageHeader } from "../../components/ui";
import { ApiError, api } from "../../lib/api";
import { dateTime, LANGUAGE_LABEL } from "../../lib/format";
import type { PortalPatient } from "../../lib/types";
import { StatusBadge } from "./PortalPage";

export function PortalPatientPage() {
  const { patientId } = useParams();
  const q = useQuery({ queryKey: ["portal-patient", patientId], queryFn: () => api<PortalPatient>(`/api/portal/patients/${patientId}`), retry: false });
  const back = <Link to="/portal" className="mb-3 inline-flex items-center gap-1 text-sm text-brand-700 hover:underline"><ArrowLeft className="size-4" /> My patients</Link>;
  if (q.error instanceof ApiError && q.error.status === 403) {
    return (
      <div className="mx-auto max-w-2xl">
        {back}
        <Card>
          <div className="flex flex-col items-center gap-2 py-8 text-center" role="alert" data-testid="portal-denied">
            <ShieldAlert className="size-8 text-rose-500" />
            <p className="font-medium text-slate-900">You can only view patients you referred</p>
            <p className="max-w-md text-sm text-slate-600">This patient is not on your list. The attempt has been recorded in the privacy audit log.</p>
          </div>
        </Card>
      </div>
    );
  }
  const p = q.data;
  return (
    <div className="mx-auto max-w-3xl">
      {back}
      {q.isLoading && <Loading />}
      {q.error && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {p && (
        <>
          <PageHeader title={p.name} subtitle={`Born ${p.dob} · ${p.sex} · health card …${p.health_card_last4} · ${LANGUAGE_LABEL[p.preferred_language]} · ${p.phone}`} />
          <Card title="Requisitions" className="mb-4" padded={false}>
            {p.requisitions.length === 0 ? <EmptyState title="No requisitions from you for this patient" /> : (
              <ul className="divide-y divide-slate-100">
                {p.requisitions.map((r) => (
                  <li key={r.id} className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5 text-sm">
                    <span><span className="font-medium text-slate-800">{r.requested_exam ?? r.id}</span> <span className="text-xs text-slate-500">received {dateTime(r.received_at)}</span></span>
                    <StatusBadge r={r} />
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <Card title="Appointments" className="mb-4" padded={false}>
            {p.appointments.length === 0 ? <EmptyState title="No appointments" /> : (
              <ul className="divide-y divide-slate-100">
                {p.appointments.map((a) => (
                  <li key={a.id} className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5 text-sm">
                    <span><span className="font-medium text-slate-800">{a.exam_name}</span> <span className="text-xs text-slate-500">{dateTime(a.start)} · {a.site_name}</span></span>
                    <Badge tone={a.status === "completed" ? "slate" : a.status === "cancelled" ? "red" : "green"}>{a.status.replace("_", "-")}</Badge>
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <Card title="Signed reports" padded={false}>
            {p.reports.length === 0 ? <EmptyState title="No signed reports" hint="Reports appear here once a radiologist signs them. Drafts are never shared." /> : (
              <ul className="divide-y divide-slate-100">
                {p.reports.map((r) => (
                  <li key={r.id} className="px-4 py-2.5 text-sm">
                    <p className="flex items-center gap-2 font-medium text-slate-800">{r.exam_name} <Badge tone="green"><ShieldCheck className="size-3" /> Signed {r.signed_at && dateTime(r.signed_at)}</Badge></p>
                    <p className="mt-1 text-slate-700">{r.impression}</p>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </>
      )}
    </div>
  );
}
