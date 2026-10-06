import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";
import { useState } from "react";
import { Badge, Button, Card, EmptyState, ErrorState, Loading, UrgencyBadge } from "../../components/ui";
import { api, post } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { LANGUAGE_LABEL, pct, time } from "../../lib/format";
import type { Appointment, BackfillCase, Site } from "../../lib/types";

const STATUS_TONE: Record<string, "green" | "blue" | "slate" | "red" | "amber"> = {
  confirmed: "green", booked: "blue", completed: "slate", cancelled: "red", no_show: "amber",
};

function today() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export function AppointmentsTab({ sites, onBackfill }: { sites: Site[]; onBackfill: (c: BackfillCase) => void }) {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const [day, setDay] = useState(() => {
    const d = new Date();
    d.setDate(d.getDate() + 1);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  });
  const [site, setSite] = useState(user?.site_ids[0] ?? "LKS");
  const [highRisk, setHighRisk] = useState(false);
  const query = useQuery({
    queryKey: ["appointments", day, site, highRisk],
    queryFn: () => api<{ appointments: Appointment[]; total: number }>(`/api/scheduling/appointments?day=${day}&site_id=${site}&high_risk_only=${highRisk}`),
    refetchInterval: 5000,
  });
  const cancel = useMutation({
    mutationFn: (id: string) => post<{ backfill_case: BackfillCase | null }>(`/api/scheduling/appointments/${id}/cancel`, { reason: "Cancelled by staff" }),
    onSuccess: (res) => {
      queryClient.invalidateQueries();
      if (res.backfill_case) onBackfill(res.backfill_case);
    },
  });
  const canCancel = user && ["front_desk", "operations_manager", "admin"].includes(user.role);
  const allowedSites = user?.site_ids.length ? sites.filter((s) => user.site_ids.includes(s.id)) : sites;

  return (
    <Card
      title="Appointments"
      padded={false}
      actions={
        <>
          <input type="date" value={day} min={today()} onChange={(e) => setDay(e.target.value)} className="h-8 rounded-lg border border-slate-300 px-2 text-xs" aria-label="Day" />
          <select value={site} onChange={(e) => setSite(e.target.value)} className="h-8 rounded-lg border border-slate-300 px-2 text-xs" aria-label="Site">
            {allowedSites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
          <label className="flex items-center gap-1.5 text-xs text-slate-600">
            <input type="checkbox" checked={highRisk} onChange={(e) => setHighRisk(e.target.checked)} />
            High risk only
          </label>
        </>
      }
    >
      {query.isLoading && <Loading />}
      {query.error && <ErrorState error={query.error} onRetry={() => query.refetch()} />}
      {cancel.error && <p className="px-4 pt-3 text-sm text-rose-600" role="alert">{(cancel.error as Error).message}</p>}
      {query.data && query.data.appointments.length === 0 && <EmptyState title="No appointments" hint="Try another day or site." />}
      {query.data && query.data.appointments.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-50 text-xs text-slate-500">
              <tr>{["Time", "Patient", "Exam", "Urgency", "Status", "No-show risk", "Top factors", ""].map((h, i) => <th key={i} className="px-3 py-2 font-medium">{h}</th>)}</tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {query.data.appointments.map((a) => (
                <tr key={a.id} data-testid={`appt-${a.id}`}>
                  <td className="tabular px-3 py-2 whitespace-nowrap">{time(a.start)}</td>
                  <td className="px-3 py-2">
                    <p className="font-medium text-slate-800">{a.patient_name}</p>
                    <p className="text-xs text-slate-500">{LANGUAGE_LABEL[a.patient_language]}</p>
                  </td>
                  <td className="px-3 py-2">{a.exam_name}<p className="text-xs text-slate-500">{a.scanner_id}</p></td>
                  <td className="px-3 py-2"><UrgencyBadge urgency={a.urgency} /></td>
                  <td className="px-3 py-2"><Badge tone={STATUS_TONE[a.status]}>{a.status.replace("_", "-")}</Badge></td>
                  <td className="px-3 py-2">
                    {a.no_show_risk == null ? "–" : (
                      <span className="flex items-center gap-1.5">
                        {a.high_risk && <AlertTriangle className="size-3.5 text-amber-600" aria-label="High risk" />}
                        <span className="tabular font-medium">{pct(a.no_show_risk)}</span>
                        {a.high_risk && <span className="text-xs text-amber-700">high</span>}
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-xs text-slate-600">
                    {a.risk_factors.length ? <ul className="list-disc pl-4">{a.risk_factors.map((f) => <li key={f}>{f}</li>)}</ul> : "–"}
                    {a.extra_reminder && <Badge tone="brand" className="mt-1">Extra reminder queued</Badge>}
                  </td>
                  <td className="px-3 py-2 text-right">
                    {canCancel && ["booked", "confirmed"].includes(a.status) && (
                      <Button size="sm" variant="ghost" loading={cancel.isPending && cancel.variables === a.id} onClick={() => cancel.mutate(a.id)}>
                        Cancel
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="px-4 py-2 text-xs text-slate-500">
            Risk scores come from a logistic regression trained on synthetic history; factors are the features pushing this appointment's risk up the most.
          </p>
        </div>
      )}
    </Card>
  );
}
