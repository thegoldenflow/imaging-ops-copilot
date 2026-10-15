// The left column: the review queue, blocking items first, then severity, then the longest wait (sorted by the
// server). Order reviews and reconciliations side by side; a search box narrows by MRN, name or medication.

import clsx from "clsx";
import { ClipboardCheck, Clock, Inbox, Pill, Search, Sparkles, TestTube } from "lucide-react";
import { useMemo, useState } from "react";
import { AiChip } from "../../components/ai";
import { AiBatchBar } from "../../components/AiActivity";
import { EmptyState, ErrorState, Loading } from "../../components/ui";
import { useQueue, waitText, type QueueItem, type QueueShow } from "./api";
import { SeverityPill } from "./kit";

export function QueuePanel({ selected, onSelect }: { selected: string | null; onSelect: (item: QueueItem) => void }) {
  const [show, setShow] = useState<QueueShow>("open");
  const [term, setTerm] = useState("");
  const queue = useQueue(show);
  const items = useMemo(() => {
    const t = term.trim().toLowerCase();
    const all = queue.data?.items ?? [];
    return t ? all.filter((i) => [i.mrn, i.name, i.title, i.detail, i.unit_id].some((v) => v?.toLowerCase().includes(t))) : all;
  }, [queue.data, term]);
  // the queue never runs the AI by itself: what the rules flagged waits for someone to open it or ask for a batch
  // (test orders without a rule flag get AI only from Ask AI on the item)
  const notRun = items.filter((i) => i.ai_state === "not_requested" && i.commentary_status !== "on_demand")
    .map((i) => ({ kind: i.kind === "medrec" ? "medrec" : "order_review", id: i.id }));
  return (
    <section className="flex min-h-0 flex-col rounded-xl border border-slate-200 bg-white shadow-sm" data-testid="ms-queue">
      <header className="space-y-2 border-b border-slate-100 px-3 py-2.5">
        <div className="flex items-center justify-between">
          <h2 className="flex items-center gap-1.5 text-sm font-semibold text-slate-900"><Inbox className="size-4 text-brand-600" />Queue</h2>
          {queue.data && (
            <span className="text-[11px] text-slate-500" data-testid="queue-counts">
              {queue.data.counts.blocking} blocking · {queue.data.counts.pending} to review
              {queue.data.counts.cosign ? ` · ${queue.data.counts.cosign} to co-sign` : ""}
            </span>
          )}
        </div>
        <div className="flex gap-1" role="tablist">
          {(["open", "all", "done"] as QueueShow[]).map((s) => (
            <button
              key={s}
              role="tab"
              aria-selected={show === s}
              onClick={() => setShow(s)}
              className={clsx("rounded-md px-2 py-1 text-xs font-medium", show === s ? "bg-brand-50 text-brand-700" : "text-slate-600 hover:bg-slate-100")}
            >
              {{ open: "Needs action", all: "All", done: "Done" }[s]}
            </button>
          ))}
        </div>
        <label className="flex items-center gap-1.5 rounded-lg border border-slate-200 px-2 py-1">
          <Search className="size-3.5 text-slate-400" />
          <input
            value={term}
            onChange={(e) => setTerm(e.target.value)}
            placeholder="MRN, name or medication"
            className="w-full bg-transparent text-xs outline-none"
            data-testid="queue-search"
          />
        </label>
      </header>
      {notRun.length > 0 && (
        <div className="border-b border-slate-100 p-2"><AiBatchBar items={notRun} noun={notRun.length === 1 ? "item" : "items"} checked="checked by the rules" /></div>
      )}
      <div className="min-h-0 flex-1 overflow-y-auto">
        {queue.isLoading && <Loading label="Reviewing new orders…" />}
        {queue.error && <ErrorState error={queue.error} onRetry={() => queue.refetch()} />}
        {queue.data && items.length === 0 && <EmptyState title="Nothing here" hint={term ? "No item matches the search." : "No orders or reconciliations wait for you."} />}
        <ul className="divide-y divide-slate-100">
          {items.map((item) => (
            <li key={`${item.kind}:${item.id}`}>
              <button
                onClick={() => onSelect(item)}
                className={clsx("w-full px-3 py-2 text-left hover:bg-slate-50", selected === item.id && "bg-brand-50/60")}
                data-testid={`queue-item-${item.id}`}
                data-kind={item.kind}
              >
                <div className="flex items-center gap-1.5">
                  <SeverityPill severity={item.blocking ? "blocking" : item.severity} />
                  {item.kind === "medrec" ? <ClipboardCheck className="size-3.5 text-slate-500" aria-label="Reconciliation" />
                    : item.order_kind === "service" ? <TestTube className="size-3.5 text-slate-500" aria-label="Test order" />
                      : <Pill className="size-3.5 text-slate-500" aria-label="Medication order" />}
                  {item.sources.includes("ai") && <Sparkles className="size-3 text-ai-600" aria-label="AI flag" />}
                  {item.commentary_status !== "on_demand" && <AiChip state={item.ai_state} />}
                  <span className="ml-auto inline-flex items-center gap-0.5 text-[11px] text-slate-500"><Clock className="size-3" />{waitText(item.wait_minutes)}</span>
                </div>
                <p className="mt-1 line-clamp-2 text-xs font-medium text-slate-900">{item.title}</p>
                <p className="mt-0.5 truncate text-[11px] text-slate-500">
                  {item.name ?? "Patient"} · MRN {item.mrn ?? "–"} · {item.unit_id ?? "–"}
                </p>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
