import { useQuery } from "@tanstack/react-query";
import { Badge, Card, EmptyState, ErrorState, Loading, PageHeader } from "../../components/ui";
import { api } from "../../lib/api";
import { dateTime, pct } from "../../lib/format";
import { llmVendor } from "../../lib/llm";

interface Result { task: string; run_at: string; mode: string; model: string; n: number; metrics: Record<string, number>; errors: Record<string, unknown>[]; seconds: number; notes: string }

const TITLES: Record<string, string> = {
  extraction: "Requisition extraction · 50 requisitions",
  triage: "Priority triage · 50 requisitions",
  protocol: "Protocol suggestion · 40 indications",
  implants: "MRI implant extraction · 30 answers in 4 languages",
  feedback: "Patient feedback sentiment and themes · 60 comments in 4 languages",
  referral_summary: "Referral weekly summary · number fidelity",
  policy_qa: "Policy Q&A · citations and refusals",
  clinical_kg: "Clinical knowledge Q&A · 30 questions in English and Chinese",
};

export function EvalsPage() {
  const q = useQuery({ queryKey: ["evals"], queryFn: () => api<{ results: Record<string, Result>; command: string; live_triage_agreement: { reviewed: number; agreement: number | null } }>("/api/evals") });
  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader title="AI evaluations" subtitle="Each AI feature has a synthetic eval set that runs with one command and writes results to evals/results." />
      {q.isLoading && <Loading />}
      {q.error && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.data && (
        <div className="space-y-4">
          <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
            These results come from the <b>rule-based baselines</b> (mock mode) unless the run is marked Claude or Gemini. The baselines were written alongside the
            synthetic generator, so their scores are optimistic. Run <code className="rounded bg-white px-1">{q.data.command}</code> with an API key (LLM_PROVIDER picks Claude or Gemini) for model numbers.
          </div>
          {Object.keys(q.data.results).length === 0 && <Card><EmptyState title="No eval results yet" hint={q.data.command} /></Card>}
          {Object.entries(q.data.results).map(([key, r]) => (
            <Card key={key} title={TITLES[key] ?? key} actions={<span className="flex items-center gap-2 text-xs text-slate-500"><Badge tone={llmVendor(r.mode) ? "ai" : "slate"}>{llmVendor(r.mode) ?? "baseline"}</Badge>{dateTime(r.run_at)}</span>}>
              <div className="grid grid-cols-2 gap-3 md:grid-cols-4" data-testid={`eval-${key}`}>
                {Object.entries(r.metrics).filter(([k]) => k !== "failed_calls").map(([k, v]) => (
                  <div key={k}><p className="text-xs text-slate-500">{k.replaceAll("_", " ")}</p><p className="tabular text-lg font-semibold">{pct(v, 1)}</p></div>
                ))}
              </div>
              <p className="mt-2 text-xs text-slate-500">{r.notes} n = {r.n}{r.metrics.failed_calls ? ` · ${r.metrics.failed_calls} failed calls` : ""} · <span data-testid={`eval-model-${key}`}>{r.model}</span>.</p>
              {r.errors.length > 0 && (
                <details className="mt-2 text-xs"><summary className="cursor-pointer text-slate-500">{r.errors.length} example misses</summary>
                  <pre className="mt-1 max-h-48 overflow-auto rounded bg-slate-50 p-2">{JSON.stringify(r.errors, null, 1)}</pre>
                </details>
              )}
            </Card>
          ))}
          <Card title="Live triage agreement (radiologist reviews in this demo)">
            <p className="text-sm">{q.data.live_triage_agreement.agreement == null ? "No reviews yet." : `${pct(q.data.live_triage_agreement.agreement)} agreement over ${q.data.live_triage_agreement.reviewed} reviewed requisitions.`}</p>
          </Card>
        </div>
      )}
    </div>
  );
}
