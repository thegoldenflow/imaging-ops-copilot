// Documentation (spec 7.3): the physician's discharge summaries, the nurse's bilingual patient instructions and
// shift handoffs, for the patients of their units (in hospital, or discharged in the last 72 hours). AI output is a
// draft a person signs; items that do not trace to the record block the signature.

import clsx from "clsx";
import { FileText } from "lucide-react";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { EmptyState, ErrorState, Loading, PageHeader, Tabs } from "../../components/ui";
import { useAuth } from "../../lib/auth";
import { useEncounter, useWorklist, type WorkItem } from "./api";
import { HandoffPanel } from "./HandoffPanel";
import { InstructionsPanel } from "./InstructionsReview";
import { DocStatusPill, TableStatus } from "./kit";
import { SummaryPanel } from "./SummaryReview";

type View = "summary" | "instructions" | "handoff";

function Worklist({ items, selected, onSelect, view }: { items: WorkItem[]; selected: string | null; onSelect: (id: string) => void; view: View }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white" data-testid="doc-worklist">
      <div className="border-b border-slate-100 px-3 py-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
        Your units · {items.length} patients
      </div>
      <ul className="max-h-[calc(100vh-230px)] divide-y divide-slate-100 overflow-y-auto">
        {items.map((i) => (
          <li key={i.encounter_id}>
            <button
              type="button"
              onClick={() => onSelect(i.encounter_id)}
              data-testid={`work-${i.encounter_id}`}
              className={clsx("w-full px-3 py-2 text-left text-sm hover:bg-slate-50", selected === i.encounter_id && "bg-brand-50")}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="font-medium text-slate-900">{i.name ?? i.mrn}</span>
                <DocStatusPill status={view === "summary" ? i.summary : view === "instructions" ? i.instructions : i.handoff}
                  testId={`status-${i.encounter_id}`} />
              </div>
              <div className="mt-0.5 flex flex-wrap items-center gap-x-2 text-[11px] text-slate-500">
                <span>MRN {i.mrn}</span>
                <span>{i.status === "finished" ? `discharged ${i.discharged?.slice(0, 10) ?? ""}` : i.bed ?? i.unit}</span>
                <span>{i.language}</span>
                {view === "summary" && i.discharge_planned && <span>· med table <TableStatus status={i.discharge_table} /></span>}
                {view === "instructions" && <span>· summary {i.summary}</span>}
              </div>
            </button>
          </li>
        ))}
        {!items.length && <li className="p-3 text-sm text-slate-500">No patients.</li>}
      </ul>
    </div>
  );
}

function EncounterPane({ id, view }: { id: string; view: View }) {
  const { data, isLoading, error, refetch } = useEncounter(id);
  if (isLoading) return <Loading />;
  if (error || !data) return <ErrorState error={error} onRetry={refetch} />;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 rounded-xl border border-slate-200 bg-white px-4 py-3" data-testid="doc-patient">
        <span className="text-base font-semibold text-slate-900">{data.patient_name}</span>
        <span className="text-xs text-slate-500">MRN {data.mrn} · {data.encounter_id} · {data.unit ?? "—"} · language {data.language}</span>
        <span className="text-xs text-slate-500">
          admitted {data.admitted?.slice(0, 10)}{data.discharged ? `, discharged ${data.discharged.slice(0, 10)}` : ", in hospital"}
          {data.reason ? ` · ${data.reason}` : ""}
        </span>
        {data.ai_mode === "template_only" && <span className="text-xs font-medium text-amber-700" data-testid="template-only">AI off for this patient: {data.ai_mode_reason}</span>}
      </div>
      {view === "summary" && <SummaryPanel encounter={data} />}
      {view === "instructions" && <InstructionsPanel encounter={data} />}
      {view === "handoff" && <HandoffPanel encounter={data} />}
    </div>
  );
}

export function DocumentationPage() {
  const { user } = useAuth();
  const nurse = user?.role === "nurse";
  const [params, setParams] = useSearchParams();
  const [chosen, setView] = useState<View>(nurse ? "instructions" : "summary");
  const selected = params.get("encounter");
  const { data, isLoading, error, refetch } = useWorklist();
  const tabs: { id: View; label: string }[] = nurse
    ? [{ id: "instructions", label: "Patient instructions" }, { id: "handoff", label: "Shift handoff" }]
    : [{ id: "summary", label: "Discharge summaries" }];
  // a view of this person's tabs: demo mode switches from the surgeon to the nurse on this same page (no new mount),
  // and the surgeon's "summary" stayed, with no tab for it and no instructions for the nurse
  const view = tabs.some((t) => t.id === chosen) ? chosen : tabs[0].id;
  return (
    <div data-testid="documentation">
      <PageHeader
        title="Documentation"
        subtitle={nurse ? "Bilingual patient instructions from the signed discharge summary, and the SBAR shift handoff: you review and sign."
          : "Discharge summaries drafted from the record: every item cites its sources; untraceable items block your signature."}
      />
      {tabs.length > 1 && <div className="mb-3"><Tabs<View> value={view} onChange={setView} tabs={tabs} /></div>}
      {isLoading && <Loading />}
      {error && <ErrorState error={error} onRetry={refetch} />}
      {data && (
        <div className="grid gap-3 lg:grid-cols-[300px_minmax(0,1fr)]">
          <Worklist items={data.items} selected={selected} view={view}
            onSelect={(id) => setParams((p) => { p.set("encounter", id); return p; })} />
          <div className="min-w-0">
            {selected ? <EncounterPane id={selected} view={view} /> :
              <EmptyState icon={<FileText className="size-8" />} title="Pick a patient" hint="Patients of your units, in hospital or discharged in the last 72 hours." />}
          </div>
        </div>
      )}
      <p className="mt-3 text-[11px] text-slate-400">Synthetic hospital. AI drafts are marked; a person signs every document. Demo, not for clinical use.</p>
    </div>
  );
}
