// The exception stream (spec 7.1): what the rule engine found, newest and most severe first, and what was decided.

import clsx from "clsx";
import { AlarmClock, BellRing, CheckCircle2, CircleSlash, Radar, Sparkles, XCircle } from "lucide-react";
import { hhmm, RULE_LABEL, type ExceptionItem } from "./api";
import { EmptyPanel, Panel, SeverityPill, SkeletonRows, StatusPill } from "./kit";

export function ExceptionStream({ items, loading, selected, onSelect }: {
  items: ExceptionItem[] | undefined;
  loading: boolean;
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  const live = items?.filter((e) => e.status === "open" || e.status === "deferred") ?? [];
  return (
    <Panel title="Exceptions" icon={<Radar className="size-4 text-ct-accent" />} testId="exception-stream"
      actions={items && <span className="text-[11px] text-ct-muted">{live.length} open · rules: occupancy ≥ 95%, ≥ 3 boarders over 2 h, OR +30 min, pre-op gap &lt; 4 h</span>}>
      {loading && !items ? (
        <SkeletonRows rows={3} />
      ) : !items || items.length === 0 ? (
        <EmptyPanel title="No exceptions" hint="The rule engine checks the boards after every event." />
      ) : (
        <div className="flex gap-2 overflow-x-auto p-2.5">
          {items.map((e) => <ExceptionCard key={e.id} e={e} active={selected === e.id} onClick={() => onSelect(e.id)} />)}
        </div>
      )}
    </Panel>
  );
}

function StatusLine({ e }: { e: ExceptionItem }) {
  if (e.status === "approved") return <StatusPill tone="ok" icon={CheckCircle2}>approved by {e.decision?.name}</StatusPill>;
  if (e.status === "rejected") return <StatusPill tone="muted" icon={XCircle}>rejected</StatusPill>;
  if (e.status === "deferred") return <StatusPill tone="info" icon={AlarmClock}>deferred to {hhmm(e.remind_at)}</StatusPill>;
  if (e.status === "cleared") return <StatusPill tone="muted" icon={CircleSlash}>cleared {hhmm(e.cleared_at)}</StatusPill>;
  return e.reminders ? <StatusPill tone="warning" icon={BellRing}>reminder</StatusPill> : <StatusPill tone="critical">open</StatusPill>;
}

function ExceptionCard({ e, active, onClick }: { e: ExceptionItem; active: boolean; onClick: () => void }) {
  const done = e.status !== "open" && e.status !== "deferred";
  return (
    <button
      onClick={onClick}
      className={clsx(
        "flex w-72 shrink-0 flex-col gap-1.5 rounded-lg border p-2.5 text-left transition-colors",
        active ? "border-ct-accent bg-ct-raised" : "border-ct-border bg-ct-surface hover:bg-ct-raised",
        done && "opacity-60",
      )}
      data-testid="exception-card"
      data-exception={e.id}
      data-rule={e.rule}
    >
      <div className="flex items-center justify-between gap-2">
        <SeverityPill severity={e.severity} />
        <span className="text-[11px] text-ct-muted">{RULE_LABEL[e.rule]} · {hhmm(e.detected_at)}</span>
      </div>
      <p className="line-clamp-2 text-sm font-medium text-ct-text">{e.title}</p>
      <p className="line-clamp-2 text-[11px] text-ct-muted">
        {e.narrative ? <><Sparkles className="mr-1 inline size-3 text-ct-ai" />{e.narrative}</> : e.summary}
      </p>
      <div className="mt-auto"><StatusLine e={e} /></div>
    </button>
  );
}
