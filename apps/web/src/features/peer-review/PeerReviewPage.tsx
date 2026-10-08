import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { Download, EyeOff, Play } from "lucide-react";
import { useEffect, useState } from "react";
import { Badge, Button, Card, EmptyState, ErrorState, Loading, PageHeader, Stat } from "../../components/ui";
import { api, post, put } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { dateTime, pct } from "../../lib/format";
import type { PeerReviewItem, QaData, QaSummary } from "../../lib/types";

interface Mine { reviews: PeerReviewItem[]; open: number; scores: Record<string, string>; discrepancy_types: Record<string, string> }

const SCORE_TONE = { concur: "green", minor: "amber", significant: "red" } as const;
const FLAG_RATE = 0.08; // highlight significant-discrepancy rates above this

function ReviewForm({ review, scores, types, onSubmitted }: { review: PeerReviewItem; scores: Record<string, string>; types: Record<string, string>; onSubmitted: () => void }) {
  const queryClient = useQueryClient();
  const [score, setScore] = useState("concur");
  const [dtype, setDtype] = useState("");
  const [comment, setComment] = useState("");
  const submit = useMutation({
    mutationFn: () => post(`/api/peer-review/${review.id}/submit`, { score, discrepancy_type: score === "concur" ? null : dtype || null, comment }),
    onSuccess: () => {
      onSubmitted();
      queryClient.invalidateQueries({ queryKey: ["peer-review"] });
    },
  });
  return (
    <div className="mt-4 space-y-3 rounded-lg border border-slate-200 p-3">
      <p className="text-sm font-medium text-slate-800">Your assessment</p>
      <div className="space-y-1.5">
        {Object.entries(scores).map(([k, label]) => (
          <label key={k} className="flex items-center gap-2 text-sm">
            <input type="radio" name="score" value={k} checked={score === k} onChange={() => setScore(k)} data-testid={`score-${k}`} />
            <Badge tone={SCORE_TONE[k as keyof typeof SCORE_TONE]}>{k}</Badge> {label}
          </label>
        ))}
      </div>
      {score !== "concur" && (
        <select value={dtype} onChange={(e) => setDtype(e.target.value)} className="h-9 w-full rounded-lg border border-slate-300 px-2 text-sm" aria-label="Discrepancy type">
          <option value="">Choose the discrepancy type…</option>
          {Object.entries(types).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
      )}
      <textarea value={comment} onChange={(e) => setComment(e.target.value)} rows={2} placeholder="Comment (optional)" className="w-full rounded-lg border border-slate-300 p-2 text-sm" aria-label="Comment" />
      <div className="flex justify-end">
        <Button variant="primary" size="sm" disabled={score !== "concur" && !dtype} loading={submit.isPending} onClick={() => submit.mutate()} data-testid="submit-review">Submit review</Button>
      </div>
      {submit.error && <p className="text-sm text-rose-600" role="alert">{(submit.error as Error).message}</p>}
    </div>
  );
}

function MyReviews() {
  const q = useQuery({ queryKey: ["peer-review", "mine"], queryFn: () => api<Mine>("/api/peer-review/mine") });
  const [selected, setSelected] = useState<string | null>(null);
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const items = q.data!.reviews;
  const current = items.find((r) => r.id === selected) ?? items.find((r) => r.status === "assigned") ?? items[0];
  if (!current) return <Card><EmptyState title="No reviews assigned" hint="Sampled reports are assigned to you after the nightly run." /></Card>;
  return (
    <div className="grid gap-4 lg:grid-cols-5">
      <div className="lg:col-span-2">
        <p className="mb-2 text-sm text-slate-600">{q.data!.open} waiting for your review</p>
        <ul className="space-y-2">
          {items.map((r) => (
            <li key={r.id}>
              <button onClick={() => setSelected(r.id)} data-testid={`review-${r.id}`}
                className={clsx("w-full rounded-xl border bg-white p-3 text-left shadow-sm", current.id === r.id ? "border-brand-500 ring-1 ring-brand-100" : "border-slate-200 hover:border-slate-300")}>
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-slate-900">{r.exam_name}</span>
                  <span className="ml-auto">{r.status === "completed" ? <Badge tone={SCORE_TONE[r.score!]}>{r.score}</Badge> : <Badge tone="amber">To review</Badge>}</span>
                </div>
                <p className="text-xs text-slate-500">{r.patient_age} {r.patient_sex} · signed {r.report_signed_on} · assigned {dateTime(r.assigned_at)}</p>
              </button>
            </li>
          ))}
        </ul>
      </div>
      <Card className="lg:col-span-3" title={`${current.exam_name} · ${current.id}`} actions={<Badge><EyeOff className="size-3" /> Original reader hidden</Badge>}>
        <p className="text-sm text-slate-600">{current.patient_age} {current.patient_sex} · performed {dateTime(current.performed_at)}{current.indication && ` · ${current.indication}`}</p>
        <div className="mt-3 space-y-2 rounded-lg bg-slate-50 p-3">
          {current.report_sections.map((s) => (
            <div key={s.label}>
              <p className="text-xs font-semibold text-slate-500 uppercase">{s.label}</p>
              <p className="text-sm text-slate-800">{s.text}</p>
            </div>
          ))}
        </div>
        {current.status === "assigned" ? <ReviewForm key={current.id} review={current} scores={q.data!.scores} types={q.data!.discrepancy_types} onSubmitted={() => setSelected(current.id)} /> : (
          <p className="mt-3 text-sm text-slate-600">
            Submitted {dateTime(current.completed_at!)}: <Badge tone={SCORE_TONE[current.score!]}>{current.score}</Badge>
            {current.discrepancy_type && ` · ${q.data!.discrepancy_types[current.discrepancy_type]}`}{current.comment && ` · ${current.comment}`}
          </p>
        )}
      </Card>
    </div>
  );
}

function Rate({ s }: { s: QaSummary | undefined }) {
  if (!s || !s.reviews) return <span className="text-slate-400">–</span>;
  const flag = (s.significant_rate ?? 0) > FLAG_RATE;
  return <span className={clsx("tabular", flag && "font-semibold text-rose-600")} title={`${s.significant} of ${s.reviews} significant`}>{pct(s.significant_rate ?? 0)} <span className="text-xs text-slate-400">({s.reviews})</span></span>;
}

function QaReport() {
  const queryClient = useQueryClient();
  const q = useQuery({ queryKey: ["peer-review", "qa"], queryFn: () => api<QaData>("/api/peer-review/qa") });
  const [cfg, setCfg] = useState<QaData["config"] | null>(null);
  useEffect(() => { if (q.data) setCfg(q.data.config); }, [q.data]);
  const save = useMutation({ mutationFn: () => put("/api/peer-review/config", cfg), onSuccess: () => queryClient.invalidateQueries({ queryKey: ["peer-review"] }) });
  const run = useMutation({ mutationFn: () => post("/api/peer-review/run"), onSuccess: () => queryClient.invalidateQueries({ queryKey: ["peer-review"] }) });
  const [exporting, setExporting] = useState(false);
  const exportCsv = async () => {
    setExporting(true);
    try {
      const blob = await api<Blob>("/api/peer-review/qa/export");
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `peer-review-${new Date().toISOString().slice(0, 10)}.csv`;
      a.click();
      URL.revokeObjectURL(url);
    } finally {
      setExporting(false);
    }
  };
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const d = q.data!;
  const modalities = ["MRI", "CT", "US", "XR"];
  return (
    <>
      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-5">
        <Stat label="Completed reviews" value={d.report.overall.reviews} />
        <Stat label="Concur" value={pct(d.report.overall.concur_rate ?? 0)} tone="green" />
        <Stat label="Significant discrepancies" value={d.report.overall.significant} hint={pct(d.report.overall.significant_rate ?? 0, 1)} tone={d.report.overall.significant ? "red" : undefined} />
        <Stat label="Waiting for reviewers" value={d.open} />
        <Stat label="Could not assign" value={d.unassigned} hint="no other credentialed reader" />
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          <Card title="By radiologist" padded={false} actions={<Button size="sm" onClick={exportCsv} loading={exporting} data-testid="export-qa"><Download className="size-4" /> Export CSV</Button>}>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="bg-slate-50 text-xs text-slate-500">
                  <tr><th className="px-3 py-2 font-medium">Radiologist</th><th className="px-3 py-2 font-medium">Reviews</th><th className="px-3 py-2 font-medium">Concur</th><th className="px-3 py-2 font-medium">Minor</th><th className="px-3 py-2 font-medium">Significant</th>
                    {modalities.map((m) => <th key={m} className="px-3 py-2 font-medium">{m} significant</th>)}</tr>
                </thead>
                <tbody className="tabular divide-y divide-slate-100">
                  {d.report.by_radiologist.map((r) => (
                    <tr key={r.radiologist_id} data-testid={`qa-row-${r.radiologist_id}`}>
                      <td className="px-3 py-2 font-medium text-slate-800">{r.name}</td>
                      <td className="px-3 py-2">{r.reviews}</td>
                      <td className="px-3 py-2">{r.concur_rate == null ? "–" : pct(r.concur_rate)}</td>
                      <td className="px-3 py-2">{r.minor}</td>
                      <td className="px-3 py-2">{r.significant}</td>
                      {modalities.map((m) => <td key={m} className="px-3 py-2"><Rate s={r.by_modality[m]} /></td>)}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="px-4 py-2 text-xs text-slate-500">Significant-discrepancy rates above {pct(FLAG_RATE)} are highlighted. Small samples are noisy; numbers in brackets are review counts.</p>
          </Card>
          <Card title="By exam type" padded={false}>
            <table className="w-full text-left text-sm">
              <thead className="bg-slate-50 text-xs text-slate-500">
                <tr>{["Exam", "Reviews", "Concur", "Minor", "Significant"].map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}</tr>
              </thead>
              <tbody className="tabular divide-y divide-slate-100">
                {d.report.by_exam.map((e) => (
                  <tr key={e.exam_code}>
                    <td className="px-3 py-2">{e.exam_name}</td><td className="px-3 py-2">{e.reviews}</td>
                    <td className="px-3 py-2">{pct(e.concur_rate ?? 0)}</td><td className="px-3 py-2">{e.minor}</td><td className="px-3 py-2"><Rate s={e} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
          <Card title="Recent completed reviews" padded={false}>
            <ul className="divide-y divide-slate-100">
              {d.recent.slice(0, 15).map((r) => (
                <li key={r.id} className="px-4 py-2 text-sm">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone={SCORE_TONE[r.score!]}>{r.score}</Badge>
                    <span className="font-medium text-slate-800">{r.exam_name}</span>
                    <span className="text-xs text-slate-500">read by {r.original_reader_name} · reviewed by {r.reviewer_name} · {dateTime(r.completed_at!)}</span>
                  </div>
                  {r.discrepancy_type && <p className="mt-0.5 text-xs text-slate-600">{d.discrepancy_types[r.discrepancy_type]}{r.comment && `: ${r.comment}`}</p>}
                </li>
              ))}
            </ul>
          </Card>
        </div>
        <div className="space-y-4">
          {cfg && (
            <Card title="Sampling schedule">
              <div className="space-y-2 text-sm">
                <label className="flex items-center justify-between gap-2">Sample rate
                  <span className="flex items-center gap-1"><input type="number" min={1} max={50} value={Math.round(cfg.sample_rate * 100)} onChange={(e) => setCfg({ ...cfg, sample_rate: Number(e.target.value) / 100 })}
                    className="h-8 w-16 rounded-lg border border-slate-300 px-2 text-right" aria-label="Sample rate percent" />%</span>
                </label>
                <label className="flex items-center justify-between gap-2">Nightly run at
                  <span className="flex items-center gap-1"><input type="number" min={0} max={23} value={cfg.run_hour} onChange={(e) => setCfg({ ...cfg, run_hour: Number(e.target.value) })}
                    className="h-8 w-16 rounded-lg border border-slate-300 px-2 text-right" aria-label="Run hour" />:00</span>
                </label>
                <label className="flex items-center gap-2"><input type="checkbox" checked={cfg.enabled} onChange={(e) => setCfg({ ...cfg, enabled: e.target.checked })} /> Scheduled sampling on</label>
                <p className="text-xs text-slate-500">Each run samples reports signed since the previous run and assigns them blind to another credentialed radiologist, never to the original reader.</p>
                <div className="flex gap-2">
                  <Button size="sm" variant="primary" loading={save.isPending} onClick={() => save.mutate()}>Save</Button>
                  <Button size="sm" loading={run.isPending} onClick={() => run.mutate()} data-testid="run-sampling"><Play className="size-3.5" /> Run sampling now</Button>
                </div>
                {(save.error || run.error) && <p className="text-sm text-rose-600" role="alert">{((save.error || run.error) as Error).message}</p>}
              </div>
            </Card>
          )}
          <Card title="Sampling runs" padded={false}>
            <ul className="divide-y divide-slate-100 text-xs">
              {d.runs.map((r) => (
                <li key={r.id} className="px-4 py-2" data-testid={`run-${r.id}`}>
                  <p className="font-medium text-slate-700">{dateTime(r.ts)} <Badge tone={r.trigger === "manual" ? "brand" : "slate"}>{r.trigger}</Badge></p>
                  <p className="text-slate-500">{r.sampled} of {r.candidates} reports sampled · {r.assigned} assigned{r.unassigned ? ` · ${r.unassigned} unassigned` : ""}{r.trigger === "manual" ? ` · by ${r.by}` : ""}</p>
                </li>
              ))}
            </ul>
          </Card>
          <Card title="Discrepancy types">
            <ul className="space-y-1 text-sm">
              {Object.entries(d.report.discrepancy_types).sort((a, b) => b[1] - a[1]).map(([k, n]) => (
                <li key={k} className="flex justify-between"><span>{d.discrepancy_types[k]}</span><span className="tabular text-slate-500">{n}</span></li>
              ))}
            </ul>
          </Card>
        </div>
      </div>
    </>
  );
}

export function PeerReviewPage() {
  const { user } = useAuth();
  const isLead = user?.role === "medical_director";
  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title={isLead ? "Peer review · QA report" : "My peer reviews"}
        subtitle={isLead
          ? "Sampled reports are re-read blind by another radiologist. Results are visible only to the QA lead."
          : "Blind second reads of reports sampled from the group. You never review your own reports."}
      />
      {isLead ? <QaReport /> : <MyReviews />}
    </div>
  );
}
