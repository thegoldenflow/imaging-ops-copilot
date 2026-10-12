import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { AiBadge, Badge, Button, Card, EmptyState, ErrorState, Loading, PageHeader } from "../../components/ui";
import { api, post } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { dateTime, LANGUAGE_LABEL } from "../../lib/format";
import type { MriScreening } from "../../lib/types";
import { MriBadge } from "../requisitions/badges";

function Review({ s }: { s: MriScreening }) {
  const queryClient = useQueryClient();
  const [note, setNote] = useState("");
  const review = useMutation({
    mutationFn: (decision: string) => post(`/api/mri-screening/${s.id}/review`, { decision, note }),
    onSuccess: () => queryClient.invalidateQueries(),
  });
  return (
    <div className="mt-3 space-y-2 border-t border-slate-100 pt-3">
      <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Review note (required), e.g. implant card checked" className="h-8 w-full rounded-lg border border-slate-300 px-2 text-sm" aria-label="Review note" />
      <div className="flex gap-2">
        <Button size="sm" variant="primary" disabled={note.trim().length < 3} loading={review.isPending && review.variables === "cleared"} onClick={() => review.mutate("cleared")}>Clear for MRI</Button>
        <Button size="sm" variant="danger" disabled={note.trim().length < 3} loading={review.isPending && review.variables === "not_cleared"} onClick={() => review.mutate("not_cleared")}>Not cleared</Button>
      </div>
      {review.error && <p className="text-sm text-rose-600">{(review.error as Error).message}</p>}
    </div>
  );
}

export function MriSafetyPage() {
  const { user } = useAuth();
  const q = useQuery({ queryKey: ["mri-screenings"], queryFn: () => api<{ screenings: MriScreening[]; questions: Record<string, string> }>("/api/mri-screening"), refetchInterval: 4000 });
  const canReview = user && ["technologist", "radiologist"].includes(user.role);
  return (
    <div className="mx-auto max-w-4xl">
      <PageHeader title="MRI safety screening" subtitle="Flags from requisitions and patient questionnaires. The system never clears anyone; a technologist or radiologist decides." />
      {q.isLoading && <Loading />}
      {q.error && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.data?.screenings.length === 0 && <Card><EmptyState title="No MRI screenings yet" hint="A questionnaire is sent when a radiologist approves an MRI protocol." /></Card>}
      <div className="space-y-3">
        {q.data?.screenings.map((s) => (
          <Card key={s.id} title={<span className="flex items-center gap-2">{s.patient_name} <Link to={`/requisitions/${s.requisition_id}`} className="text-xs font-normal text-brand-700 underline">{s.requisition_id}</Link></span>}
            actions={<MriBadge status={s.status} />}>
            <div className="space-y-2 text-sm" data-testid={`screening-${s.requisition_id}`}>
              <p className="text-xs text-slate-500">
                {LANGUAGE_LABEL[s.language]} · {s.submitted_at ? `answered ${dateTime(s.submitted_at)}` : "not answered yet"}
                {s.appointment_start && ` · exam ${dateTime(s.appointment_start)}`}
              </p>
              {s.flags.length > 0 && <ul className="list-disc pl-4 text-rose-700">{s.flags.map((f) => <li key={f}>{f}</li>)}</ul>}
              {Object.entries(s.answers).filter(([, v]) => v).length > 0 && (
                <div className="flex flex-wrap gap-1">{Object.entries(s.answers).filter(([, v]) => v).map(([k]) => <Badge key={k} tone="amber">{q.data!.questions[k]}</Badge>)}</div>
              )}
              {s.free_text && <p className="rounded-lg bg-slate-50 p-2 text-slate-700">Patient wrote: “{s.free_text}”</p>}
              {s.devices.map((d, i) => (
                <p key={i} className="text-xs"><AiBadge label="AI read" agent="mri_implant_extract" /> “{d.patient_words}” → <b>{d.device_name}</b> ({d.list_match ?? "not on list"}) · {d.mr_status}</p>
              ))}
              {s.reviewed_by && <p className="text-xs text-slate-600">{s.review_decision === "cleared" ? "Cleared" : "Not cleared"} by {s.reviewed_by} · “{s.review_note}”</p>}
              {canReview && (s.status === "flagged" || s.status === "no_flags") && <Review s={s} />}
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}
