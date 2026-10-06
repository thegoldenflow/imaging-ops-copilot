import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Button, Card, ErrorState, Loading, UrgencyBadge } from "../../components/ui";
import { api, put } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { pct } from "../../lib/format";

interface Weights { wait_time: number; exam_value: number; key_referrer: number }
interface ModelInfo {
  auc: number;
  base_rate: number;
  n_train: number;
  n_test: number;
  coefficients: Record<string, number>;
  high_risk_threshold: number;
  note: string;
}
interface WaitlistEntry {
  id: string;
  patient_name: string;
  exam_name: string;
  urgency: string;
  wait_days: number;
  acceptable_site_ids: string[];
  earliest_date: string;
  language: string;
}

const LABELS: Record<keyof Weights, string> = { wait_time: "Wait time", exam_value: "Exam value", key_referrer: "Key referrer" };

export function SettingsTab() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const weights = useQuery({ queryKey: ["weights"], queryFn: () => api<Weights>("/api/scheduling/weights") });
  const model = useQuery({ queryKey: ["noshow-model"], queryFn: () => api<ModelInfo>("/api/scheduling/noshow-model") });
  const [draft, setDraft] = useState<Weights | null>(null);
  useEffect(() => { if (weights.data) setDraft(weights.data); }, [weights.data]);
  const save = useMutation({ mutationFn: (w: Weights) => put("/api/scheduling/weights", w), onSuccess: () => queryClient.invalidateQueries() });
  const canEdit = user && ["operations_manager", "admin"].includes(user.role);

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card title="Priority weights (within an urgency tier)">
        {weights.isLoading && <Loading />}
        {weights.error && <ErrorState error={weights.error} />}
        {draft && (
          <div className="space-y-4">
            {(Object.keys(LABELS) as (keyof Weights)[]).map((k) => (
              <label key={k} className="block">
                <div className="flex justify-between text-sm"><span>{LABELS[k]}</span><span className="tabular font-medium">{draft[k].toFixed(2)}</span></div>
                <input type="range" min={0} max={1} step={0.05} value={draft[k]} disabled={!canEdit}
                  onChange={(e) => setDraft({ ...draft, [k]: Number(e.target.value) })} className="w-full accent-brand-600" />
              </label>
            ))}
            <p className="text-xs text-slate-500">Clinical urgency (P1 → P4) is never overridden by these weights.</p>
            {canEdit && <Button variant="primary" loading={save.isPending} onClick={() => save.mutate(draft)}>Save weights</Button>}
            {save.isSuccess && <span className="ml-2 text-xs text-emerald-700">Saved. New cancellations use these weights.</span>}
          </div>
        )}
      </Card>
      <Card title="No-show model">
        {model.isLoading && <Loading />}
        {model.error && <ErrorState error={model.error} />}
        {model.data && (
          <div className="space-y-3 text-sm">
            <div className="grid grid-cols-3 gap-3">
              <div><p className="text-xs text-slate-500">Hold-out AUC</p><p className="tabular text-xl font-semibold">{model.data.auc.toFixed(3)}</p></div>
              <div><p className="text-xs text-slate-500">Base no-show rate</p><p className="tabular text-xl font-semibold">{pct(model.data.base_rate, 1)}</p></div>
              <div><p className="text-xs text-slate-500">High-risk threshold</p><p className="tabular text-xl font-semibold">{pct(model.data.high_risk_threshold)}</p></div>
            </div>
            <table className="w-full text-xs">
              <thead className="text-slate-500"><tr><th className="py-1 text-left">Feature</th><th className="text-right">Coefficient (standardized)</th></tr></thead>
              <tbody className="divide-y divide-slate-100">
                {Object.entries(model.data.coefficients).map(([f, c]) => (
                  <tr key={f}><td className="py-1">{f}</td><td className="tabular text-right">{c > 0 ? "+" : ""}{c.toFixed(3)}</td></tr>
                ))}
              </tbody>
            </table>
            <p className="text-xs text-slate-500">{model.data.note} Train {model.data.n_train.toLocaleString()} / test {model.data.n_test.toLocaleString()}.</p>
          </div>
        )}
      </Card>
    </div>
  );
}

export function WaitlistTab() {
  const q = useQuery({ queryKey: ["waitlist"], queryFn: () => api<{ entries: WaitlistEntry[] }>("/api/scheduling/waitlist"), refetchInterval: 5000 });
  return (
    <Card title="Waitlist" padded={false}>
      {q.isLoading && <Loading />}
      {q.error && <ErrorState error={q.error} />}
      {q.data && (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-50 text-xs text-slate-500">
              <tr>{["Urgency", "Patient", "Exam", "Sites", "Earliest", "Waiting"].map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}</tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {q.data.entries.map((w) => (
                <tr key={w.id}>
                  <td className="px-3 py-2"><UrgencyBadge urgency={w.urgency} /></td>
                  <td className="px-3 py-2 font-medium">{w.patient_name}</td>
                  <td className="px-3 py-2">{w.exam_name}</td>
                  <td className="px-3 py-2">{w.acceptable_site_ids.join(", ")}</td>
                  <td className="px-3 py-2">{w.earliest_date}</td>
                  <td className="tabular px-3 py-2">{w.wait_days.toFixed(0)} d</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
