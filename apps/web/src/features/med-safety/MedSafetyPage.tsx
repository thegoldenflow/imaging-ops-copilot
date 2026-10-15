// The pharmacist workbench (spec 7.2): left the queue (blocking first, then severity and wait), middle the order with
// its review result, right the reconciliation's discrepancy table with the actions and the sign button. Physicians
// see their units' reviews (laboratory and imaging orders are theirs) and co-sign reconciliations.
// AI output is advice: only rules block, and every decision is a person's, with a reason.
// The patient chart links a MedRec record here (?encounter=…&document=…): it is signed only in this workbench.

import { useQueryClient } from "@tanstack/react-query";
import { ClipboardCheck, FileWarning } from "lucide-react";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Button, EmptyState, PageHeader } from "../../components/ui";
import { ApiError } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { startMedrec, useEncounter, useReview, type QueueItem } from "./api";
import { MedRecPanel } from "./MedRecPanel";
import { QueuePanel } from "./QueuePanel";
import { EncounterOrdersPanel, ReviewPanel } from "./ReviewPanel";

interface Selection {
  reviewId: string | null;
  sessionId: string | null;
  encounterId: string | null;
}

/** For an order review: the encounter's open reconciliation (discharge first), shown on the right. */
function ReviewSide({ reviewId }: { reviewId: string }) {
  const review = useReview(reviewId);
  const sessions = review.data?.medrec ?? [];
  const pick = sessions.find((s) => s.kind === "discharge" && s.status !== "final") ?? sessions.find((s) => s.kind === "admission") ?? sessions[0];
  if (!review.data) return null;
  if (!pick) {
    return <EmptyState icon={<ClipboardCheck className="size-8" />} title="No reconciliation for this encounter" hint="Admission reconciliations open for stays admitted in the last 48 hours; discharge ones when discharge prescriptions exist." />;
  }
  // shown beside the review the person opened: its AI waits for Ask AI (opening runs the AI of that item only)
  return <MedRecPanel id={pick.id} autoAi={false} />;
}

/** A chart's MedRec record that no reconciliation holds yet: the pharmacist opens one, which adopts the record. */
function OpenReconciliation({ encounterId, onOpened }: { encounterId: string; onOpened: (sessionId: string) => void }) {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const open = async (kind: "admission" | "discharge") => {
    setBusy(true);
    setError(null);
    try {
      const s = await startMedrec(encounterId, kind);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["ms-encounter", encounterId] }),
        queryClient.invalidateQueries({ queryKey: ["ms-queue"] }),
      ]);
      onOpened(s.id);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not open the reconciliation");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div data-testid="open-reconciliation">
      <EmptyState icon={<ClipboardCheck className="size-8" />} title="No reconciliation holds this record yet"
        hint={user?.role === "pharmacist" ? "Open the reconciliation: it takes over the draft record, and you sign it here once every discrepancy is decided." : "The pharmacist opens the reconciliation; you co-sign it here after the pharmacist has signed."} />
      {user?.role === "pharmacist" && (
        <div className="flex justify-center gap-2">
          <Button size="sm" variant="primary" loading={busy} onClick={() => open("admission")}>Open admission reconciliation</Button>
          <Button size="sm" loading={busy} onClick={() => open("discharge")}>Open discharge reconciliation</Button>
        </div>
      )}
      {error && <p className="mt-2 text-center text-sm text-rose-600" role="alert">{error}</p>}
    </div>
  );
}

export function MedSafetyPage() {
  const { user } = useAuth();
  const [params] = useSearchParams();
  const linkEncounter = params.get("encounter");
  const linkDocument = params.get("document");
  // ?review=ORV-… and ?medrec=MR-… (the AI activity panel links here) open that item
  const [sel, setSel] = useState<Selection>({ reviewId: params.get("review"), sessionId: params.get("medrec"), encounterId: linkEncounter });
  const linked = useEncounter(linkEncounter);
  const linkedSession = linked.data?.medrec.find((m) => m.document_id === linkDocument)?.id ?? null;
  const onLinkedEncounter = Boolean(linkEncounter) && sel.encounterId === linkEncounter && !sel.reviewId;
  useEffect(() => {
    // the chart's record: select its reconciliation once it is known (unless another item was picked meanwhile)
    if (linkedSession) setSel((s) => (s.encounterId === linkEncounter && !s.reviewId && !s.sessionId ? { ...s, sessionId: linkedSession } : s));
  }, [linkedSession, linkEncounter]);
  const select = (item: QueueItem) => {
    if (item.kind === "order_review") setSel({ reviewId: item.id, sessionId: null, encounterId: item.encounter_id });
    else setSel({ reviewId: null, sessionId: item.id, encounterId: item.encounter_id });
  };
  const selected = sel.reviewId ?? sel.sessionId;
  return (
    <div data-testid="med-safety">
      <PageHeader
        title="Medication safety"
        subtitle={user?.role === "physician"
          ? "Order reviews of your units' patients, and reconciliations to co-sign. Rules flag first; AI comments are advice."
          : "Order verification and medication reconciliation. Rules flag first (only rules block); AI comments are advice; you decide."}
      />
      <div className="grid gap-3 lg:grid-cols-[280px_minmax(0,1fr)] xl:grid-cols-[280px_minmax(0,0.9fr)_minmax(0,1.35fr)]">
        <div className="lg:h-[calc(100vh-170px)]"><QueuePanel selected={selected} onSelect={select} /></div>
        <div className="min-w-0 space-y-3 lg:h-[calc(100vh-170px)] lg:overflow-y-auto">
          {sel.reviewId && <ReviewPanel id={sel.reviewId} />}
          {!sel.reviewId && sel.encounterId && (
            <EncounterOrdersPanel encounterId={sel.encounterId} onReview={(id) => setSel({ ...sel, reviewId: id })} />
          )}
          {!selected && <EmptyState icon={<FileWarning className="size-8" />} title="Pick an item from the queue" hint="Blocking flags (allergies) come first, then the most severe and the longest waiting." />}
        </div>
        <div className="min-w-0 lg:col-span-2 xl:col-span-1 xl:h-[calc(100vh-170px)] xl:overflow-y-auto">
          {sel.sessionId && <MedRecPanel id={sel.sessionId} />}
          {!sel.sessionId && sel.reviewId && <ReviewSide reviewId={sel.reviewId} />}
          {!sel.sessionId && onLinkedEncounter && linked.data && !linkedSession && (
            <OpenReconciliation encounterId={linkEncounter!} onOpened={(id) => setSel((s) => ({ ...s, sessionId: id }))} />
          )}
        </div>
      </div>
      <p className="mt-3 text-[11px] text-slate-400">
        Synthetic hospital. The rule tables (renal dosing, interactions, dose bounds) are demo simplifications, not clinical guidance; production uses a commercial drug knowledge base. AI items show their confidence and evidence; a pharmacist or physician decides every item.
      </p>
    </div>
  );
}
