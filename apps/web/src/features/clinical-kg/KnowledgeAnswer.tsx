import clsx from "clsx";
import { Info } from "lucide-react";
import { useState } from "react";
import { AiBadge, Badge } from "../../components/ui";
import { llmVendor } from "../../lib/llm";

export const KG_ROLES = ["technologist", "radiologist", "medical_director", "admin"];

export interface KgFact {
  id: string;
  disease: string;
  disease_en: string | null;
  relation: string;
  relation_label: string;
  value: string;
  value_en: string | null;
}

export interface KgEntry {
  id: string;
  question: string;
  asked_by: string;
  asked_at: string;
  requisition_id: string | null;
  notice: string;
  language: "en" | "zh";
  intent: "disease_facts" | "symptoms_to_diseases" | "unsupported";
  parse_source: "ai" | "rules";
  entities: { text: string; kind: string; name: string | null; match: string }[];
  facts: KgFact[];
  found: boolean;
  statements: { text: string; fact_ids: string[] }[];
  answer: string | null;
  answer_status: string;
  model: string | null;
  prompt_version: string | null;
  mode: string;
  notes?: string[];
}

const term = (zh: string, en: string | null) => (en ? `${zh} (${en})` : zh);

function FactChip({ fact, active, onClick }: { fact: KgFact; active: boolean; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} data-testid="kg-citation"
      title={`${term(fact.disease, fact.disease_en)} → ${fact.relation_label} → ${term(fact.value, fact.value_en)}`}
      className={clsx("ml-1 rounded px-1 font-mono text-[10px] align-middle ring-1 ring-inset",
        active ? "bg-ai-100 text-ai-700 ring-ai-200" : "bg-slate-50 text-slate-500 ring-slate-200 hover:bg-ai-50")}>
      {fact.id}
    </button>
  );
}

/** Answer statements with their graph-fact citations, then the facts themselves. */
export function KnowledgeAnswer({ entry, compact = false }: { entry: KgEntry; compact?: boolean }) {
  const [active, setActive] = useState<string | null>(null);
  const byId = Object.fromEntries(entry.facts.map((f) => [f.id, f]));
  const unmatched = entry.entities.filter((e) => !e.name);
  const groups = entry.facts.reduce<Record<string, KgFact[]>>((acc, f) => {
    (acc[f.disease] ??= []).push(f);
    return acc;
  }, {});
  const aiAnswered = entry.statements.length > 0;
  return (
    <div className="space-y-3" data-testid="kg-answer">
      <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
        {aiAnswered && <AiBadge label={entry.mode === "mock" ? "Template answer" : `AI answer · ${llmVendor(entry.mode) ?? entry.mode}`} agent="clinical_kg_answer" />}
        {entry.entities.filter((e) => e.name).map((e) => (
          <Badge key={e.text} tone="blue">{e.text}{e.name !== e.text && ` → ${e.name}`}{e.match === "fuzzy" && " (approx.)"}</Badge>
        ))}
        {unmatched.map((e) => <Badge key={e.text} tone="amber">{e.text}: not in graph</Badge>)}
      </div>
      {entry.notes?.map((n) => <p key={n} className="rounded-md bg-amber-50 px-3 py-1.5 text-xs text-amber-800" data-testid="kg-note">{n}</p>)}
      {aiAnswered ? (
        <ul className="space-y-1.5 text-sm text-slate-800" data-testid="kg-statements">
          {entry.statements.map((s, i) => (
            <li key={i} className="leading-6">
              {s.text}
              {s.fact_ids.map((id) => byId[id] && <FactChip key={id} fact={byId[id]} active={active === id} onClick={() => setActive(active === id ? null : id)} />)}
            </li>
          ))}
        </ul>
      ) : (
        <p className={clsx("text-sm", entry.facts.length ? "text-amber-800" : "text-slate-600")} data-testid="kg-message">{entry.answer}</p>
      )}
      {entry.facts.length > 0 && (
        <details open={!aiAnswered || !compact} className="rounded-md border border-slate-200">
          <summary className="cursor-pointer px-3 py-1.5 text-xs font-medium text-slate-600">
            {entry.facts.length} graph facts{entry.intent === "symptoms_to_diseases" && " · diseases ranked by matching symptoms"}
          </summary>
          <div className={clsx("divide-y divide-slate-100 overflow-auto", compact ? "max-h-64" : "max-h-[28rem]")} data-testid="kg-facts">
            {Object.entries(groups).map(([disease, facts]) => (
              <div key={disease} className="px-3 py-2 text-xs">
                <p className="font-medium text-slate-800">{term(disease, facts[0].disease_en)}</p>
                <p className="mt-0.5 text-slate-600">
                  {facts.map((f) => (
                    <span key={f.id} className={clsx("mr-2 inline-block rounded px-0.5", active === f.id && "bg-ai-100")}>
                      <span className="text-slate-400">{f.relation_label}:</span> {term(f.value, f.value_en)}
                    </span>
                  ))}
                </p>
              </div>
            ))}
          </div>
        </details>
      )}
      <p className="flex items-start gap-1.5 text-xs text-slate-500">
        <Info className="mt-0.5 size-3.5 shrink-0" />
        <span>{entry.notice}{entry.model && entry.mode !== "mock" && ` · ${entry.model} · ${entry.prompt_version}`}{entry.parse_source === "rules" && entry.mode !== "mock" && " · question read by rules (AI unavailable)"}</span>
      </p>
    </div>
  );
}
