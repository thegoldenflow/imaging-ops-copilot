// The pharmacist workbench (spec 7.2): left the queue (blocking first, then severity and wait), middle the order with
// its review result, right the reconciliation's discrepancy table with the actions and the sign button. Physicians
// see their units' reviews (laboratory and imaging orders are theirs) and co-sign reconciliations.
// AI output is advice: only rules block, and every decision is a person's, with a reason.

import { ClipboardCheck, FileWarning } from "lucide-react";
import { useState } from "react";
import { EmptyState, PageHeader } from "../../components/ui";
import { useAuth } from "../../lib/auth";
import { useReview, type QueueItem } from "./api";
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
  return <MedRecPanel id={pick.id} />;
}

export function MedSafetyPage() {
  const { user } = useAuth();
  const [sel, setSel] = useState<Selection>({ reviewId: null, sessionId: null, encounterId: null });
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
        </div>
      </div>
      <p className="mt-3 text-[11px] text-slate-400">
        Synthetic hospital. The rule tables (renal dosing, interactions, dose bounds) are demo simplifications, not clinical guidance; production uses a commercial drug knowledge base. AI items show their confidence and evidence; a pharmacist or physician decides every item.
      </p>
    </div>
  );
}
