// What the patient receives (mocks): the print preview and the patient portal page of signed instructions, in the
// patient's language (switchable), rendered from the same structure as the nurse reviewed.

import { Printer } from "lucide-react";
import { useState } from "react";
import { useParams } from "react-router-dom";
import { Button, ErrorState, Loading, Tabs } from "../../components/ui";
import { LANG_LABEL, useDocument, type Lang } from "./api";
import { RenderedInstructions } from "./InstructionsReview";

function useInstructions() {
  const { docId } = useParams();
  return useDocument(docId);
}

export function PrintPage() {
  const { data, isLoading, error, refetch } = useInstructions();
  const [lang, setLang] = useState<Lang | null>(null);
  if (isLoading) return <Loading />;
  if (error || !data?.renderings) return <ErrorState error={error} onRetry={refetch} />;
  const shown = lang ?? data.default_language ?? "en";
  return (
    <div className="mx-auto max-w-3xl space-y-3" data-testid="print-page">
      <div className="flex items-center justify-between print:hidden">
        <Tabs<Lang> value={shown} onChange={setLang} tabs={(["en", "zh-Hans", "zh-Hant"] as Lang[]).map((l) => ({ id: l, label: LANG_LABEL[l] }))} />
        <Button size="sm" onClick={() => window.print()}><Printer className="size-3.5" /> Print</Button>
      </div>
      <div className="rounded-lg border border-slate-200 bg-white p-6 print:border-0 print:p-0">
        <p className="mb-2 text-xs text-slate-500">{data.patient_name} · MRN {data.mrn} · {data.doc_status === "final" ? "signed" : "DRAFT — not signed"}</p>
        <RenderedInstructions r={data.renderings[shown]} testId="print-rendered" />
      </div>
    </div>
  );
}

export function PatientPortalPage() {
  const { data, isLoading, error, refetch } = useInstructions();
  const [lang, setLang] = useState<Lang | null>(null);
  if (isLoading) return <Loading />;
  if (error || !data?.renderings) return <ErrorState error={error} onRetry={refetch} />;
  const shown = lang ?? data.default_language ?? "en";
  return (
    <div className="mx-auto max-w-2xl space-y-3" data-testid="portal-page">
      <div className="rounded-xl bg-brand-700 px-4 py-3 text-white">
        <p className="text-xs opacity-80">Patient portal (mock) · what {data.patient_name} sees</p>
        <p className="text-lg font-semibold">{data.renderings[shown].title}</p>
      </div>
      <Tabs<Lang> value={shown} onChange={setLang} tabs={(["en", "zh-Hans", "zh-Hant"] as Lang[]).map((l) => ({ id: l, label: LANG_LABEL[l] }))} />
      <div className="rounded-lg border border-slate-200 bg-white p-4">
        {data.doc_status === "final" ? <RenderedInstructions r={data.renderings[shown]} testId="portal-rendered" />
          : <p className="text-sm text-slate-500">Not published: the instructions are not signed yet.</p>}
      </div>
    </div>
  );
}
