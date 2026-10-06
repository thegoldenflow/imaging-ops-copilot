import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Button, Card, EmptyState, ErrorState, Loading, PageHeader, Stat } from "../../components/ui";
import { api, put } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { dateTime } from "../../lib/format";
import type { Appointment, ContrastCheck } from "../../lib/types";
import { ContrastBadge } from "../requisitions/badges";

interface Config {
  egfr_threshold: number;
  egfr_max_age_days: number;
  risk_age: number;
  egfr_required_for_all: boolean;
  premedicate_mild_moderate_reaction: boolean;
  updated_by: string | null;
  updated_at: string | null;
}

interface Checks { checks: { appointment: Appointment & { requisition_id?: string | null }; check: ContrastCheck }[]; counts: Record<string, number>; config: Config }

function Settings({ config }: { config: Config }) {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState(config);
  useEffect(() => setDraft(config), [config]);
  const save = useMutation({ mutationFn: () => put("/api/contrast/config", draft), onSuccess: () => queryClient.invalidateQueries() });
  const canEdit = user && ["medical_director", "admin"].includes(user.role);
  const num = (k: keyof Config, label: string, hint: string) => (
    <label className="block text-sm">
      <span className="font-medium text-slate-700">{label}</span>
      <input type="number" value={draft[k] as number} disabled={!canEdit} onChange={(e) => setDraft({ ...draft, [k]: Number(e.target.value) })}
        className="mt-1 h-9 w-full rounded-lg border border-slate-300 px-2" aria-label={label} />
      <span className="text-xs text-slate-500">{hint}</span>
    </label>
  );
  const flag = (k: keyof Config, label: string) => (
    <label className="flex items-center gap-2 text-sm">
      <input type="checkbox" checked={draft[k] as boolean} disabled={!canEdit} onChange={(e) => setDraft({ ...draft, [k]: e.target.checked })} />
      {label}
    </label>
  );
  return (
    <Card title="Rule thresholds">
      <div className="space-y-3">
        <div className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">Demo placeholder values. A medical director must set the clinic's real thresholds.</div>
        {num("egfr_threshold", "eGFR review threshold", "Below this value a radiologist must review")}
        {num("egfr_max_age_days", "eGFR valid for (days)", "Older results do not count")}
        {num("risk_age", "Age requiring eGFR", "Patients this age or older need a recent eGFR")}
        {flag("egfr_required_for_all", "Require eGFR for every contrast exam")}
        {flag("premedicate_mild_moderate_reaction", "Mild or moderate prior reaction → premedication (otherwise review)")}
        {canEdit ? <Button variant="primary" loading={save.isPending} onClick={() => save.mutate()} data-testid="save-contrast-config">Save and recompute</Button>
          : <p className="text-xs text-slate-500">Only the medical director can change thresholds.</p>}
        {config.updated_by && <p className="text-xs text-slate-500">Last changed by {config.updated_by}, {dateTime(config.updated_at!)}</p>}
      </div>
    </Card>
  );
}

export function ContrastPage() {
  const q = useQuery({ queryKey: ["contrast-checks"], queryFn: () => api<Checks>("/api/contrast/checks"), refetchInterval: 5000 });
  return (
    <div>
      <PageHeader title="Contrast & kidney checks" subtitle="Every contrast exam in the next 14 days, with its status and the reasons behind it." />
      {q.isLoading && <Loading />}
      {q.error && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.data && (
        <div className="grid gap-4 lg:grid-cols-[1fr_20rem]">
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              <Stat label="Pass" value={q.data.counts.pass ?? 0} tone="green" />
              <Stat label="Needs eGFR" value={q.data.counts.needs_egfr ?? 0} tone="amber" />
              <Stat label="Needs premedication" value={q.data.counts.needs_premedication ?? 0} />
              <Stat label="Needs review" value={q.data.counts.needs_review ?? 0} tone="red" />
            </div>
            <Card padded={false}>
              {q.data.checks.length === 0 ? <EmptyState title="No contrast exams booked" /> : (
                <div className="max-h-[38rem] overflow-y-auto">
                  <table className="w-full text-left text-sm">
                    <thead className="sticky top-0 bg-slate-50 text-xs text-slate-500"><tr>{["Exam", "Patient", "Status", "Basis"].map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}</tr></thead>
                    <tbody className="divide-y divide-slate-100">
                      {q.data.checks.map(({ appointment: a, check: c }) => (
                        <tr key={a.id} data-testid={`contrast-${a.id}`}>
                          <td className="px-3 py-2 whitespace-nowrap"><p>{a.exam_name}</p><p className="text-xs text-slate-500">{dateTime(a.start)} · {a.site_id}</p></td>
                          <td className="px-3 py-2">{a.patient_name}{a.requisition_id && <Link to={`/requisitions/${a.requisition_id}`} className="ml-1 text-xs text-brand-700 underline">req</Link>}</td>
                          <td className="px-3 py-2"><ContrastBadge status={c.status} /></td>
                          <td className="px-3 py-2 text-xs text-slate-600"><ul className="list-disc pl-4">{c.basis.map((b) => <li key={b}>{b}</li>)}</ul></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>
          </div>
          <Settings config={q.data.config} />
        </div>
      )}
    </div>
  );
}
