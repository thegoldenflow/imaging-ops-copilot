// The middle column: the patient banner, then the order with its review result — every rule finding with its rule
// name and source, the AI commentary (concerns with confidence and evidence, alternatives, guideline sections) — and
// the decision (accept the order, or ask the prescriber to change it; a reason either way). Only rules block; a
// blocking flag is the pharmacist's. For a reconciliation: the encounter's medication orders with their flags.

import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Loader2, Pill, ShieldCheck, Sparkles, TestTube, Wand2 } from "lucide-react";
import { useState } from "react";
import { AiBadge, Badge, Button, Card, EmptyState, ErrorState, Loading } from "../../components/ui";
import { ApiError } from "../../lib/api";
import {
  decideReview, hhmm, runCommentary, useEncounter, useReview, type Concern, type Finding, type PatientHeader, type ReviewDetail,
} from "./api";
import { SeverityPill, SourceChip, ViewEvidence } from "./kit";

export function PatientBanner({ p }: { p: PatientHeader }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white px-3 py-2 shadow-sm" data-testid="patient-banner">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="text-sm font-semibold text-slate-900">{p.name ?? "Patient"}</span>
        <span className="text-xs text-slate-500">MRN {p.mrn}</span>
        <span className="text-xs text-slate-500">{p.age != null ? `${p.age} y` : ""} {p.gender ?? ""}</span>
        {p.bed && <span className="text-xs text-slate-500">{p.unit_id} · bed {p.bed}</span>}
        {p.reason && <span className="text-xs text-slate-600">Admitted for {p.reason}</span>}
      </div>
      <div className="mt-1.5 flex flex-wrap gap-1.5 text-[11px]">
        <Badge tone={p.egfr && p.egfr.value < 30 ? "red" : "slate"}>eGFR {p.egfr ? `${p.egfr.value} (${p.egfr.at?.slice(0, 10)})` : "–"}</Badge>
        <Badge>Weight {p.weight ? `${p.weight.value} kg` : "–"}</Badge>
        {p.inr && <Badge tone={p.inr.value > 3 ? "red" : "slate"}>INR {p.inr.value}</Badge>}
        {p.allergies.length ? p.allergies.map((a) => (
          <Badge key={a.ref} tone="red"><AlertTriangle className="size-3" />Allergy: {a.display}{a.drug_class ? ` (${a.drug_class})` : ""}</Badge>
        )) : <Badge tone="green">No known allergies</Badge>}
      </div>
      {p.conditions.length > 0 && (
        <p className="mt-1.5 text-[11px] text-slate-500" data-testid="diagnoses">
          Diagnoses: {p.conditions.map((c) => c.display).join(" · ")}
        </p>
      )}
    </div>
  );
}

function OrderCard({ r }: { r: ReviewDetail }) {
  const o = r.order;
  const medication = r.kind === "medication";
  return (
    <div className="rounded-lg border border-slate-200 bg-slate-50/60 p-3" data-testid="order-card">
      <div className="flex items-center gap-2">
        {medication ? <Pill className="size-4 text-slate-600" /> : <TestTube className="size-4 text-slate-600" />}
        <span className="text-sm font-semibold text-slate-900">{String(o.name ?? o.display ?? r.order_ref)}</span>
        <span className="text-xs text-slate-600">{String(o.text ?? o.category ?? "")}</span>
        <span className="ml-auto text-[11px] text-slate-500">ordered {hhmm(r.authored_at)} · {String(o.status)}</span>
      </div>
      <p className="mt-1 text-[11px] text-slate-500">
        {r.order_ref}{o.drug_class ? ` · ${o.drug_class}` : ""}{o.note ? ` · note: ${o.note}` : ""} · the review never changes the order
      </p>
    </div>
  );
}

function FindingRow({ f }: { f: Finding }) {
  return (
    <li className="rounded-lg border border-slate-200 p-2.5" data-testid="finding">
      <div className="flex flex-wrap items-center gap-1.5">
        <SeverityPill severity={f.severity} />
        <SourceChip source={f.rule_name} title={`Rule table entry ${f.entry}. Source: ${f.source}`} />
        <ViewEvidence refs={f.evidence_refs} />
      </div>
      <p className="mt-1.5 text-sm font-medium text-slate-900">{f.title}</p>
      <p className="mt-0.5 text-xs text-slate-600">{f.detail}</p>
      <p className="mt-1 text-xs text-slate-800"><span className="font-medium">Suggested:</span> {f.recommendation}</p>
    </li>
  );
}

function ConcernRow({ c }: { c: Concern }) {
  return (
    <li className="rounded-lg border border-ai-100 bg-ai-50/30 p-2.5" data-testid="ai-concern">
      <div className="flex flex-wrap items-center gap-1.5">
        <SeverityPill severity={c.severity} />
        <SourceChip source="ai" confidence={c.confidence} />
        <Badge>{c.type.replace(/_/g, " ")}</Badge>
        <ViewEvidence refs={c.evidence_refs} />
      </div>
      <p className="mt-1.5 text-xs text-slate-800">{c.explanation}</p>
    </li>
  );
}

function Commentary({ r }: { r: ReviewDetail }) {
  const queryClient = useQueryClient();
  const [busy, setBusy] = useState(false);
  const c = r.commentary;
  const status = r.commentary_status;
  if (status === "pending" || status === "on_demand") {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-dashed border-ai-200 p-3 text-xs text-slate-600">
        {status === "pending" ? <Loader2 className="size-3.5 animate-spin text-ai-600" /> : <Sparkles className="size-3.5 text-ai-600" />}
        {status === "pending" ? "The AI commentary is being written (the rules above stand on their own)." : "No AI commentary yet for this test order."}
        <Button
          size="sm"
          variant="ai"
          className="ml-auto"
          loading={busy}
          onClick={async () => {
            setBusy(true);
            try {
              await runCommentary(r.id);
              await queryClient.invalidateQueries({ queryKey: ["ms-review", r.id] });
            } finally {
              setBusy(false);
            }
          }}
        >
          <Wand2 className="size-3.5" />Ask the AI now
        </Button>
      </div>
    );
  }
  if (!c || status === "refused" || status === "unavailable" || status === "skipped") {
    return (
      <p className="rounded-lg border border-dashed border-slate-200 p-3 text-xs text-slate-500" data-testid="no-commentary">
        No AI commentary{c?.reason ? `: ${c.reason}` : status === "skipped" ? ": decided before it was written" : ""}. The rule findings stand on their own.
      </p>
    );
  }
  return (
    <div className="space-y-2" data-testid="commentary">
      <div className="flex flex-wrap items-center gap-1.5">
        <AiBadge agent="order_review" label="AI commentary" />
        <span className="text-[11px] text-slate-500">advice only — it never blocks or changes the order</span>
      </div>
      {c.concerns.length ? <ul className="space-y-2">{c.concerns.map((x, i) => <ConcernRow key={i} c={x} />)}</ul>
        : <p className="text-xs text-slate-600">The AI found no further concern.</p>}
      {c.alternatives.length > 0 && (
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Alternatives (AI)</p>
          <ul className="mt-1 space-y-1">
            {c.alternatives.map((a, i) => (
              <li key={i} className="text-xs text-slate-800"><span className="font-medium">{a.suggestion}.</span> <span className="text-slate-600">{a.rationale}</span></li>
            ))}
          </ul>
        </div>
      )}
      {c.guideline_refs.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-slate-500">
          Guidelines: {c.guideline_refs.map((g) => <code key={g}>{g}</code>)}
          <ViewEvidence refs={c.guideline_refs} label="Read" />
        </div>
      )}
      {(c.unresolved_refs?.length || c.dropped_refs?.length) ? (
        <p className="text-[11px] text-amber-700">{(c.unresolved_refs?.length ?? 0) + (c.dropped_refs?.length ?? 0)} reference(s) the AI gave did not resolve and are not shown.</p>
      ) : null}
    </div>
  );
}

function Decision({ r }: { r: ReviewDetail }) {
  const queryClient = useQueryClient();
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  if (r.decision) {
    const d = r.decision;
    return (
      <div className="flex items-start gap-2 rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-xs text-emerald-900" data-testid="decision">
        <CheckCircle2 className="mt-0.5 size-4" />
        <div>
          <p className="font-medium">{d.decision === "accept_order" ? "Order accepted" : "Change requested from the prescriber"} by {d.name} ({d.role}) at {hhmm(d.at)}</p>
          <p>Reason: {d.reason}</p>
          {d.change_task_id && <p>Prescriber task {d.change_task_id}</p>}
        </div>
      </div>
    );
  }
  const decide = async (decision: "accept_order" | "request_change") => {
    setBusy(decision);
    setError(null);
    try {
      await decideReview(r.id, decision, reason);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["ms-review", r.id] }),
        queryClient.invalidateQueries({ queryKey: ["ms-queue"] }),
      ]);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not record the decision");
    } finally {
      setBusy(null);
    }
  };
  const short = reason.trim().length < 5;
  return (
    <div className="space-y-2 rounded-lg border border-slate-200 p-3" data-testid="decision-form">
      <label className="block text-xs font-medium text-slate-700">
        Reason (required)
        <textarea
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          rows={2}
          className="mt-1 w-full rounded-lg border border-slate-300 p-2 text-xs"
          placeholder="Why the order stands, or what should change"
          data-testid="decision-reason"
          disabled={!r.can_decide}
        />
      </label>
      {!r.can_decide && r.why_not && <p className="text-xs text-rose-700" data-testid="why-not"><ShieldCheck className="mr-1 inline size-3.5" />{r.why_not}</p>}
      {error && <p className="text-xs text-rose-700" role="alert">{error}</p>}
      <div className="flex gap-2">
        <Button size="sm" onClick={() => decide("accept_order")} disabled={!r.can_decide || short} loading={busy === "accept_order"} data-testid="accept-order">
          Accept the order
        </Button>
        <Button size="sm" variant="primary" onClick={() => decide("request_change")} disabled={!r.can_decide || short} loading={busy === "request_change"} data-testid="request-change">
          Ask the prescriber to change it
        </Button>
      </div>
    </div>
  );
}

export function ReviewPanel({ id }: { id: string }) {
  const review = useReview(id);
  if (review.isLoading) return <Loading label="Loading the review…" />;
  if (review.error) return <ErrorState error={review.error} onRetry={() => review.refetch()} />;
  const r = review.data!;
  return (
    <div className="space-y-3" data-testid="review-panel">
      <PatientBanner p={r.patient} />
      <Card
        title={<span className="flex items-center gap-2">Order review {r.id} <SeverityPill severity={r.blocking ? "blocking" : r.severity} /></span>}
        actions={<span className="text-[11px] text-slate-500">reviewed {hhmm(r.reviewed_at)} · for {r.reviewer_roles.join(" or ")}</span>}
      >
        <div className="space-y-3">
          <OrderCard r={r} />
          <div>
            <p className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-500">Rule findings</p>
            {r.findings.length ? <ul className="space-y-2">{r.findings.map((f, i) => <FindingRow key={i} f={f} />)}</ul>
              : <p className="text-xs text-slate-600" data-testid="no-findings">No rule fired (allergy, interaction, renal dosing, dose bounds{r.kind === "service" ? ", duplicate test" : ""}).</p>}
          </div>
          <Commentary r={r} />
          <Decision r={r} />
        </div>
      </Card>
    </div>
  );
}

export function EncounterOrdersPanel({ encounterId, onReview }: { encounterId: string; onReview: (id: string) => void }) {
  const enc = useEncounter(encounterId);
  if (enc.isLoading) return <Loading label="Loading the orders…" />;
  if (enc.error) return <ErrorState error={enc.error} onRetry={() => enc.refetch()} />;
  const e = enc.data!;
  return (
    <div className="space-y-3" data-testid="encounter-orders">
      <PatientBanner p={e.patient} />
      <Card title="Medication orders and prescriptions" padded={false}>
        {e.orders.length === 0 ? <EmptyState title="No medication orders" /> : (
          <ul className="divide-y divide-slate-100">
            {e.orders.map((o) => (
              <li key={o.ref} className="flex items-center gap-2 px-3 py-2">
                <Pill className="size-3.5 text-slate-500" />
                <div className="min-w-0 flex-1">
                  <p className="text-xs font-medium text-slate-900">{o.name} <span className="font-normal text-slate-600">{o.text}</span></p>
                  <p className="text-[11px] text-slate-500">{o.intent === "plan" ? "discharge prescription" : "order"} · {o.status} · {o.ref}</p>
                </div>
                {o.review ? (
                  <button onClick={() => onReview(o.review!.id)} className="rounded-md hover:bg-slate-50" title="Open the order review">
                    <SeverityPill severity={o.review.blocking ? "blocking" : o.review.severity} />
                  </button>
                ) : <span className="text-[11px] text-slate-400">not reviewed</span>}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
