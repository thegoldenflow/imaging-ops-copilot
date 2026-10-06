import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { CheckCircle2, Clock, Send, Star, XCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { Badge, Button, Card, EmptyState, ErrorState, Loading, UrgencyBadge } from "../../components/ui";
import { api, post } from "../../lib/api";
import { dateTime, LANGUAGE_LABEL } from "../../lib/format";
import type { BackfillCase, Candidate } from "../../lib/types";

const CASE_TONE: Record<string, "red" | "amber" | "green" | "slate"> = { open: "red", offered: "amber", filled: "green", expired: "slate" };

function ScoreBreakdown({ c }: { c: Candidate }) {
  const parts = [
    ["Wait", c.breakdown.wait_time],
    ["Exam value", c.breakdown.exam_value],
    ["Key referrer", c.breakdown.key_referrer],
  ] as const;
  return (
    <details>
      <summary className="tabular cursor-pointer font-medium">{c.score.toFixed(2)}</summary>
      <dl className="mt-1 space-y-0.5 text-xs text-slate-600">
        <div className="flex justify-between gap-3"><dt>Urgency tier</dt><dd>{c.urgency} (ranked first)</dd></div>
        {parts.map(([label, v]) => (
          <div key={label} className="flex justify-between gap-3"><dt>{label}</dt><dd className="tabular">+{v.toFixed(2)}</dd></div>
        ))}
      </dl>
    </details>
  );
}

export function BackfillTab({ focusId }: { focusId: string | null }) {
  const queryClient = useQueryClient();
  const list = useQuery({
    queryKey: ["backfill"],
    queryFn: () => api<{ cases: BackfillCase[] }>("/api/scheduling/backfill"),
    refetchInterval: 3000,
  });
  const [selectedId, setSelectedId] = useState<string | null>(focusId);
  const [chosen, setChosen] = useState<string[]>([]);
  useEffect(() => { if (focusId) setSelectedId(focusId); }, [focusId]);
  const cases = list.data?.cases ?? [];
  const selected = cases.find((c) => c.id === selectedId) ?? cases[0];
  useEffect(() => { setChosen(selected?.candidates[0] ? [selected.candidates[0].waitlist_id] : []); }, [selected?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const send = useMutation({
    mutationFn: () => post(`/api/scheduling/backfill/${selected!.id}/offers`, { waitlist_ids: chosen }),
    onSuccess: () => queryClient.invalidateQueries(),
  });
  const reply = useMutation({
    mutationFn: (messageId: string) => post<{ action: string; detail: string }>(`/api/frontdesk/outbox/${messageId}/reply`, { text: "YES" }),
    onSuccess: () => queryClient.invalidateQueries(),
  });

  if (list.isLoading) return <Loading />;
  if (list.error) return <ErrorState error={list.error} onRetry={() => list.refetch()} />;
  if (!selected) {
    return (
      <Card>
        <EmptyState title="No cancellations yet" hint="When an appointment is cancelled (by staff, by the phone agent or by an SMS reply), a ranked list of waitlist candidates appears here within seconds." />
      </Card>
    );
  }
  const offered = new Set(selected.offers.map((o) => o.waitlist_id));

  return (
    <div className="grid gap-4 lg:grid-cols-[16rem_1fr]">
      <Card title="Cases" padded={false}>
        <ul className="divide-y divide-slate-100">
          {cases.map((c) => (
            <li key={c.id}>
              <button onClick={() => setSelectedId(c.id)} className={clsx("w-full px-4 py-3 text-left hover:bg-slate-50", c.id === selected.id && "bg-brand-50")}>
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium">{c.slot.modality} · {c.slot.site_id}</span>
                  <Badge tone={CASE_TONE[c.status]}>{c.status}</Badge>
                </div>
                <p className="text-xs text-slate-500">{dateTime(c.slot.start)}</p>
              </button>
            </li>
          ))}
        </ul>
      </Card>

      <div className="space-y-4">
        <Card
          title={<>Freed slot · {selected.slot.scanner_id} · {dateTime(selected.slot.start)}</>}
          actions={<span className="text-xs text-slate-500">Ranked in {selected.compute_ms < 1 ? "<1" : selected.compute_ms} ms</span>}
          padded={false}
        >
          {selected.candidates.length === 0 ? (
            <EmptyState title="No eligible waitlist patients" hint="See exclusions below." />
          ) : (
            <table className="w-full text-left text-sm">
              <thead className="bg-slate-50 text-xs text-slate-500">
                <tr>{["", "#", "Patient", "Exam", "Urgency", "Waiting", "Score", "Offer"].map((h, i) => <th key={i} className="px-3 py-2 font-medium">{h}</th>)}</tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {selected.candidates.map((c, i) => {
                  const offer = selected.offers.find((o) => o.waitlist_id === c.waitlist_id);
                  return (
                    <tr key={c.waitlist_id} data-testid={`candidate-${c.patient_id}`}>
                      <td className="px-3 py-2">
                        <input
                          type="checkbox"
                          aria-label={`Select ${c.patient_name}`}
                          disabled={offered.has(c.waitlist_id) || selected.status === "filled"}
                          checked={chosen.includes(c.waitlist_id)}
                          onChange={(e) => setChosen((prev) => (e.target.checked ? [...prev, c.waitlist_id] : prev.filter((x) => x !== c.waitlist_id)))}
                        />
                      </td>
                      <td className="tabular px-3 py-2 text-slate-400">{i + 1}</td>
                      <td className="px-3 py-2">
                        <p className="flex items-center gap-1 font-medium">
                          {c.patient_name}
                          {c.key_referrer && <Star className="size-3 fill-amber-400 text-amber-400" aria-label="Key referrer" />}
                        </p>
                        <p className="text-xs text-slate-500">{LANGUAGE_LABEL[c.language]} · accepts {c.acceptable_sites.join(", ")}</p>
                      </td>
                      <td className="px-3 py-2">{c.exam_name}</td>
                      <td className="px-3 py-2"><UrgencyBadge urgency={c.urgency} /></td>
                      <td className="tabular px-3 py-2">{c.wait_days.toFixed(0)} d</td>
                      <td className="px-3 py-2"><ScoreBreakdown c={c} /></td>
                      <td className="px-3 py-2">
                        {offer ? (
                          <div className="flex items-center gap-2">
                            <Badge tone={offer.status === "accepted" ? "green" : offer.status === "pending" ? "amber" : "slate"}>
                              {offer.status === "accepted" ? <CheckCircle2 className="size-3" /> : offer.status === "pending" ? <Clock className="size-3" /> : <XCircle className="size-3" />}
                              {offer.status}
                            </Badge>
                            {offer.status === "pending" && (
                              <Button size="sm" variant="ghost" loading={reply.isPending && reply.variables === offer.message_id} onClick={() => reply.mutate(offer.message_id)} title="Demo: simulate the patient texting YES">
                                Simulate “YES”
                              </Button>
                            )}
                          </div>
                        ) : (
                          <span className="text-xs text-slate-400">–</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-100 px-4 py-3">
            <p className="text-xs text-slate-500">
              Clinical urgency always ranks first. Within a tier: wait time, exam value and key referrer (weights in Settings).
              Offers go by SMS in each patient's language; the first to reply YES gets the slot.
            </p>
            {selected.status !== "filled" ? (
              <Button variant="primary" disabled={chosen.length === 0} loading={send.isPending} onClick={() => send.mutate()}>
                <Send className="size-4" />
                Send offer{chosen.length > 1 ? `s (${chosen.length})` : ""}
              </Button>
            ) : (
              <Badge tone="green"><CheckCircle2 className="size-3" /> Filled · {selected.new_appointment_id}</Badge>
            )}
          </div>
          {(send.error || reply.error) && <p className="px-4 pb-3 text-sm text-rose-600" role="alert">{((send.error || reply.error) as Error).message}</p>}
          {reply.data && <p className="px-4 pb-3 text-sm text-slate-600">Patient reply: {reply.data.detail}</p>}
        </Card>

        {selected.excluded.length > 0 && (
          <Card title="Not eligible for this slot">
            <ul className="space-y-1.5 text-sm">
              {selected.excluded.map((c) => (
                <li key={c.waitlist_id} className="flex flex-wrap items-center gap-2">
                  <UrgencyBadge urgency={c.urgency} />
                  <span className="font-medium">{c.patient_name}</span>
                  <span className="text-slate-500">— {c.exclusion}</span>
                </li>
              ))}
            </ul>
          </Card>
        )}
      </div>
    </div>
  );
}
