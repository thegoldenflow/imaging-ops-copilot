import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Network, Search } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Button, Card, EmptyState, ErrorState, Loading, PageHeader, Stat } from "../../components/ui";
import { api, post } from "../../lib/api";
import { dateTime } from "../../lib/format";
import { KnowledgeAnswer, type KgEntry } from "./KnowledgeAnswer";

const EXAMPLES = [
  "What tests are used for pulmonary embolism?",
  "What could cause fever, cough and dyspnea?",
  "Symptoms and complications of pneumonia",
  "肺栓塞需要做哪些检查？",
  "头痛和呕吐可能是什么病？",
];

function AskForm({ initial, requisitionId, onAnswer, compact = false }: {
  initial?: string; requisitionId?: string; onAnswer: (e: KgEntry) => void; compact?: boolean;
}) {
  const [question, setQuestion] = useState(initial ?? "");
  const ask = useMutation({
    mutationFn: () => post<KgEntry>("/api/clinical-kg/ask", { question, requisition_id: requisitionId ?? null }),
    onSuccess: onAnswer,
  });
  return (
    <form className="space-y-2" onSubmit={(e) => { e.preventDefault(); if (question.trim().length > 1) ask.mutate(); }}>
      <textarea value={question} onChange={(e) => setQuestion(e.target.value)} rows={compact ? 2 : 3} data-testid="kg-question"
        placeholder="Ask about a disease (symptoms, tests, drugs, treatments, complications) or list symptoms to see matching diseases. English or Chinese."
        className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-brand-500 focus:outline-none" />
      <div className="flex items-center justify-between gap-2">
        {ask.error ? <p className="text-xs text-rose-700">{(ask.error as Error).message}</p> : <span />}
        <Button type="submit" size="sm" variant="ai" loading={ask.isPending} disabled={question.trim().length < 2} data-testid="kg-ask">
          <Search className="size-4" /> Ask the knowledge graph
        </Button>
      </div>
    </form>
  );
}

/** Side panel on the requisition page, prefilled with the extracted clinical indication. */
export function KnowledgePanel({ requisitionId, indication }: { requisitionId: string; indication: string }) {
  const [entry, setEntry] = useState<KgEntry | null>(null);
  return (
    <Card title={<span className="flex items-center gap-2"><Network className="size-4 text-ai-700" /> Clinical knowledge</span>}
      actions={<Link to="/clinical-knowledge" className="text-xs text-brand-700 underline">Open full page</Link>}>
      <div data-testid="kg-panel" className="space-y-3">
        <AskForm compact initial={indication} requisitionId={requisitionId} onAnswer={setEntry} />
        {entry ? <KnowledgeAnswer entry={entry} compact /> : (
          <p className="text-xs text-slate-500">Look up what the medical knowledge graph lists for this indication before confirming priority and protocol. Reference only, not a diagnosis.</p>
        )}
      </div>
    </Card>
  );
}

interface Stats { diseases: number; facts: number; symptom_nodes: number; check_nodes: number; sources: string[]; notice: string }

export function ClinicalKnowledgePage() {
  const queryClient = useQueryClient();
  const stats = useQuery({ queryKey: ["kg-stats"], queryFn: () => api<Stats>("/api/clinical-kg/stats") });
  const history = useQuery({ queryKey: ["kg-history"], queryFn: () => api<{ entries: KgEntry[] }>("/api/clinical-kg/history") });
  const [entry, setEntry] = useState<KgEntry | null>(null);
  const [formKey, setFormKey] = useState(0);
  const [initial, setInitial] = useState("");
  const onAnswer = (e: KgEntry) => { setEntry(e); queryClient.invalidateQueries({ queryKey: ["kg-history"] }); };

  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader title="Clinical knowledge"
        subtitle="Ask a medical knowledge graph about diseases, symptoms and tests. Answers cite the graph facts they use. Reference for clinical staff, not a diagnosis." />
      {stats.isLoading && <Loading />}
      {stats.error && <ErrorState error={stats.error} onRetry={() => stats.refetch()} />}
      {stats.data && (
        <div className="mb-4 grid gap-3 sm:grid-cols-4">
          <Stat label="Diseases" value={stats.data.diseases.toLocaleString()} />
          <Stat label="Facts" value={stats.data.facts.toLocaleString()} />
          <Stat label="Symptoms" value={stats.data.symptom_nodes.toLocaleString()} />
          <Stat label="Tests and exams" value={stats.data.check_nodes.toLocaleString()} hint={stats.data.sources.join(", ")} />
        </div>
      )}
      <div className="grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,1fr)]">
        <div className="space-y-4">
          <Card title="Ask">
            <AskForm key={formKey} initial={initial} onAnswer={onAnswer} />
            <div className="mt-3 flex flex-wrap gap-1.5">
              {EXAMPLES.map((q) => (
                <button key={q} type="button" onClick={() => { setInitial(q); setFormKey((k) => k + 1); }}
                  className="rounded-full bg-slate-100 px-2.5 py-1 text-xs text-slate-600 hover:bg-slate-200">{q}</button>
              ))}
            </div>
          </Card>
          {entry && <Card title={entry.question}><KnowledgeAnswer entry={entry} /></Card>}
          {stats.data && <p className="text-xs text-slate-500">{stats.data.notice}</p>}
        </div>
        <Card title="Recent questions" padded={false}>
          {history.data?.entries.length ? (
            <ul className="divide-y divide-slate-100">
              {history.data.entries.map((e) => (
                <li key={e.id}>
                  <button type="button" onClick={() => setEntry(e)} className="w-full px-4 py-2 text-left hover:bg-slate-50">
                    <p className="line-clamp-2 text-sm text-slate-800">{e.question}</p>
                    <p className="text-xs text-slate-500">{e.asked_by} · {dateTime(e.asked_at)}{e.requisition_id && ` · ${e.requisition_id}`}</p>
                  </button>
                </li>
              ))}
            </ul>
          ) : <EmptyState title="No questions yet" />}
        </Card>
      </div>
    </div>
  );
}
