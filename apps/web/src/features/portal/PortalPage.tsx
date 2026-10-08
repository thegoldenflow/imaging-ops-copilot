import { useQuery } from "@tanstack/react-query";
import { FilePlus2, Search, UserRound } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Badge, Button, Card, EmptyState, ErrorState, Loading, PageHeader } from "../../components/ui";
import { api } from "../../lib/api";
import { dateTime } from "../../lib/format";
import type { PortalPatientRow, PortalRequisition } from "../../lib/types";

export function StatusBadge({ r }: { r: PortalRequisition }) {
  const tone = r.status === "booked" ? "green" : r.status === "failed" ? "amber" : "blue";
  return <Badge tone={tone}>{r.status_text}</Badge>;
}

export function PortalPage() {
  const navigate = useNavigate();
  const [lookup, setLookup] = useState("");
  const patients = useQuery({ queryKey: ["portal-patients"], queryFn: () => api<{ patients: PortalPatientRow[] }>("/api/portal/patients") });
  const reqs = useQuery({ queryKey: ["portal-reqs"], queryFn: () => api<{ requisitions: PortalRequisition[] }>("/api/portal/requisitions"), refetchInterval: 4000 });
  return (
    <div className="mx-auto max-w-4xl">
      <PageHeader title="My patients" subtitle="Patients you referred: their appointments, requisitions and signed reports."
        actions={<Link to="/portal/new"><Button variant="primary"><FilePlus2 className="size-4" /> New requisition</Button></Link>} />
      <Card title="My requisitions" className="mb-4" padded={false}>
        {reqs.isLoading && <Loading />}
        {reqs.error && <ErrorState error={reqs.error} onRetry={() => reqs.refetch()} />}
        {reqs.data?.requisitions.length === 0 && <EmptyState title="No requisitions yet" hint="Submit one online; it reaches our triage team within seconds." />}
        <ul className="divide-y divide-slate-100">
          {reqs.data?.requisitions.slice(0, 12).map((r) => (
            <li key={r.id} className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5 text-sm" data-testid={`portal-req-${r.id}`}>
              <div>
                <Link to={`/portal/patients/${r.patient_id}`} className="font-medium text-slate-900 hover:underline">{r.patient_name}</Link>
                <p className="text-xs text-slate-500">{r.requested_exam ?? "Exam being read from the requisition"} · {r.id} · {r.channel} · {dateTime(r.received_at)}</p>
              </div>
              <div className="flex items-center gap-2">
                {r.confirmed_priority && <Badge tone="slate">Priority {r.confirmed_priority} (radiologist)</Badge>}
                <StatusBadge r={r} />
              </div>
            </li>
          ))}
        </ul>
      </Card>
      <Card title="Patients" padded={false}
        actions={
          <form className="flex items-center gap-1" onSubmit={(e) => { e.preventDefault(); if (lookup.trim()) navigate(`/portal/patients/${lookup.trim()}`); }}>
            <input value={lookup} onChange={(e) => setLookup(e.target.value)} placeholder="Patient ID" className="h-8 w-32 rounded-lg border border-slate-300 px-2 text-xs" aria-label="Patient ID" />
            <Button size="sm" type="submit" aria-label="Open patient"><Search className="size-3.5" /></Button>
          </form>
        }>
        {patients.isLoading && <Loading />}
        {patients.error && <ErrorState error={patients.error} onRetry={() => patients.refetch()} />}
        {patients.data?.patients.length === 0 && <EmptyState title="No patients yet" />}
        <ul className="divide-y divide-slate-100">
          {patients.data?.patients.map((p) => (
            <li key={p.id}>
              <Link to={`/portal/patients/${p.id}`} className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5 text-sm hover:bg-slate-50" data-testid={`portal-patient-${p.id}`}>
                <span className="flex items-center gap-2"><UserRound className="size-4 text-slate-400" /><span className="font-medium text-slate-900">{p.name}</span><span className="text-xs text-slate-500">born {p.dob}</span></span>
                <span className="flex items-center gap-2 text-xs text-slate-600">
                  {p.open_requisitions > 0 && <Badge tone="blue">{p.open_requisitions} open requisition{p.open_requisitions > 1 ? "s" : ""}</Badge>}
                  {p.next_appointment ? `Next exam ${dateTime(p.next_appointment)}` : "No upcoming exam"}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}
