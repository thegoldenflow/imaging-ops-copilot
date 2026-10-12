import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ShieldAlert } from "lucide-react";
import { useState } from "react";
import { Badge, Button, Card, EmptyState, ErrorState, Loading, PageHeader, Tabs } from "../../components/ui";
import { api, post } from "../../lib/api";
import { dateTime } from "../../lib/format";
import { ROLE_LABEL, type Role } from "../../lib/types";
import type { ReviewItem } from "./api";

/** Admin's 24-hour review queue for break-glass access (6.3); decisions go back to the audit log. */
export function BreakGlassReviewPage() {
  const [status, setStatus] = useState<"pending" | "all">("pending");
  const queue = useQuery({
    queryKey: ["break-glass-queue", status],
    queryFn: () => api<{ grants: ReviewItem[] }>(`/api/admin/break-glass?status=${status}`),
    refetchInterval: 10_000,
  });
  return (
    <div>
      <PageHeader
        title="Break-glass review"
        subtitle="Every emergency access to a patient outside the user's units, due for review within 24 hours. The decision is written to the audit log."
      />
      <Tabs value={status} onChange={setStatus} tabs={[{ id: "pending", label: "Waiting for review" }, { id: "all", label: "All" }]} />
      <Card padded={false}>
        {queue.isLoading && <Loading />}
        {queue.error && <ErrorState error={queue.error} onRetry={() => queue.refetch()} />}
        {queue.data && queue.data.grants.length === 0 && <EmptyState title="Nothing to review" icon={<ShieldAlert className="size-8" />} />}
        {queue.data && queue.data.grants.length > 0 && (
          <ul className="divide-y divide-slate-100">
            {queue.data.grants.map((g) => <ReviewRow key={g.id} item={g} />)}
          </ul>
        )}
      </Card>
    </div>
  );
}

function ReviewRow({ item }: { item: ReviewItem }) {
  const queryClient = useQueryClient();
  const [note, setNote] = useState("");
  const review = useMutation({
    mutationFn: (decision: "justified" | "not_justified") => post(`/api/admin/break-glass/${item.id}/review`, { decision, note }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["break-glass-queue"] }),
  });
  const accessed = Object.entries(item.accessed).map(([type, n]) => `${type} ${n}`).join(", ");
  return (
    <li className="p-4" data-testid={`review-${item.id}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold text-slate-900">{item.user_name}</span>
        <Badge>{ROLE_LABEL[item.role as Role] ?? item.role}</Badge>
        <span className="text-sm text-slate-600">opened patient {item.patient_id}</span>
        {item.active && <Badge tone="red">access active</Badge>}
        {item.review_status === "pending" && (item.overdue ? <Badge tone="red">review overdue</Badge> : <Badge tone="amber">review by {dateTime(item.review_due)}</Badge>)}
        {item.review_status !== "pending" && (
          <Badge tone={item.review_status === "justified" ? "green" : "red"}>
            {item.review_status === "justified" ? "Justified" : "Not justified"} · {item.reviewed_by_name}
          </Badge>
        )}
      </div>
      <p className="mt-1 text-sm text-slate-700"><span className="text-slate-500">Reason: </span>“{item.reason}”</p>
      <p className="mt-0.5 text-xs text-slate-500">
        {item.id} · granted {dateTime(item.granted_at)}, until {dateTime(item.expires_at)} · opened {item.accessed_count} record reads{accessed ? ` (${accessed})` : ""}
      </p>
      {item.review_note && <p className="mt-0.5 text-xs text-slate-600">Review note: {item.review_note}</p>}
      {item.review_status === "pending" && (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <input
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Review note (optional)"
            className="h-8 w-72 rounded-lg border border-slate-300 px-2 text-sm"
          />
          <Button size="sm" variant="primary" onClick={() => review.mutate("justified")} loading={review.isPending}>Justified</Button>
          <Button size="sm" variant="danger" onClick={() => review.mutate("not_justified")} loading={review.isPending}>Not justified</Button>
          {review.error && <span className="text-sm text-rose-600">{review.error.message}</span>}
        </div>
      )}
    </li>
  );
}
