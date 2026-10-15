// "What the AI did in this demo" (the header's AI activity button) and the list pages' "Ask AI for these N".
// Every model call in the demo is caused by a click: the panel lists each AI action of this browser (whatever roles it
// switched through) or everyone's since the last reset, with what caused it, when, how long it took and what it cost.

import { useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { AlertTriangle, ArrowUpRight, Clock3, Loader2, Sparkles, Wand2, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  causeText, clockTime, runBatch, seconds, usd, useAiActivity, useAiKinds, type AiAction, type AiActivity, type AiLive,
} from "../lib/ai";
import { ApiError } from "../lib/api";
import { useAuth } from "../lib/auth";
import { ROLE_LABEL, type Role } from "../lib/types";

// ---------- the header button and the panel ----------

export function AiActivityButton() {
  const [open, setOpen] = useState(false);
  const mine = useAiActivity("session", { open });
  const d = mine.data;
  const busy = d ? d.running.length + d.queued.length : 0;
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="relative inline-flex h-8 items-center gap-1.5 rounded-lg border border-ai-100 bg-ai-50 px-2.5 text-xs font-medium text-ai-700 hover:bg-ai-100"
        title="What the AI did in this demo"
        data-testid="ai-activity-button"
      >
        {busy ? <Loader2 className="size-3.5 animate-spin" /> : <Sparkles className="size-3.5" />}
        <span className="hidden sm:inline">AI activity</span>
        <span className="tabular rounded bg-white/80 px-1 text-[11px]" data-testid="ai-activity-count">{d?.totals.actions ?? 0}</span>
      </button>
      {open && <AiActivityPanel onClose={() => setOpen(false)} />}
    </>
  );
}

function AiActivityPanel({ onClose }: { onClose: () => void }) {
  const [scope, setScope] = useState<"session" | "all">("session");
  const q = useAiActivity(scope, { open: true });
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  const d = q.data;
  return (
    <div className="fixed inset-0 z-50" data-testid="ai-activity-panel">
      <button type="button" className="absolute inset-0 bg-slate-900/20" aria-label="Close" onClick={onClose} />
      <aside role="dialog" aria-label="What the AI did in this demo"
        className="absolute inset-y-0 right-0 flex w-full max-w-[460px] flex-col border-l border-slate-200 bg-white shadow-2xl">
        <header className="space-y-2 border-b border-slate-100 px-4 py-3">
          <div className="flex items-start justify-between gap-2">
            <div>
              <h2 className="flex items-center gap-1.5 text-sm font-semibold text-slate-900"><Sparkles className="size-4 text-ai-600" />What the AI did in this demo</h2>
              <p className="mt-0.5 text-xs text-slate-500">
                {d?.background_ai === "eager"
                  ? "This server also runs AI in the background (eager mode), before anyone looks."
                  : "The AI runs only when someone opens an item, presses Ask AI or does something that creates new work."}
              </p>
            </div>
            <button onClick={onClose} className="rounded-md p-1 text-slate-500 hover:bg-slate-100" aria-label="Close"><X className="size-4" /></button>
          </div>
          <div className="flex gap-1" role="tablist">
            {(["session", "all"] as const).map((s) => (
              <button key={s} role="tab" aria-selected={scope === s} onClick={() => setScope(s)} data-testid={`ai-scope-${s}`}
                className={clsx("rounded-md px-2 py-1 text-xs font-medium", scope === s ? "bg-ai-50 text-ai-700" : "text-slate-600 hover:bg-slate-100")}>
                {s === "session" ? "This browser" : "Everyone since the reset"}
              </button>
            ))}
          </div>
          {d && <Totals d={d} />}
        </header>
        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-3">
          {q.isLoading && <p className="flex items-center gap-2 text-xs text-slate-500"><Loader2 className="size-3.5 animate-spin" />Loading…</p>}
          {q.error && <p className="text-xs text-rose-600" role="alert">{(q.error as Error).message}</p>}
          {d && <Live d={d} />}
          {d && (d.actions.length ? (
            <ol className="space-y-2" data-testid="ai-actions">
              {d.actions.map((a) => <ActionRow key={a.id} a={a} onNavigate={onClose} />)}
            </ol>
          ) : !d.running.length && !d.queued.length && (
            <div className="rounded-lg border border-dashed border-slate-200 p-4 text-center text-xs text-slate-500" data-testid="ai-activity-empty">
              <Sparkles className="mx-auto mb-1 size-5 text-slate-300" />
              {scope === "session"
                ? "No AI has run for this browser yet. Open an order review, a Control Tower exception or a requisition, or press Ask AI: each model call shows up here with what caused it."
                : "No AI has run since the last reset."}
            </div>
          ))}
        </div>
        <footer className="border-t border-slate-100 px-4 py-2 text-[11px] text-slate-500">
          {d?.mode === "mock" ? "Mock model: costs are the model's list price for the tokens, nothing is billed." : `Model: ${d?.mode ?? "…"}; costs at list price.`} Ids and labels only: no patient details are kept here.
        </footer>
      </aside>
    </div>
  );
}

function Totals({ d }: { d: AiActivity }) {
  const t = d.totals;
  return (
    <dl className="grid grid-cols-4 gap-2 rounded-lg bg-slate-50 p-2 text-center" data-testid="ai-totals">
      {[["Actions", String(t.actions)], ["Model calls", String(t.calls)], ["AI time", seconds(t.seconds)], [t.cost_estimated ? "Cost (est.)" : "Cost", `${t.cost_estimated ? "≈ " : ""}${usd(t.cost_usd)}`]].map(([k, v]) => (
        <div key={k}><dt className="text-[10px] uppercase tracking-wide text-slate-500">{k}</dt><dd className="tabular text-sm font-semibold text-slate-900">{v}</dd></div>
      ))}
    </dl>
  );
}

function Live({ d }: { d: AiActivity }) {
  if (!d.running.length && !d.queued.length) return null;
  const row = (x: AiLive, running: boolean) => (
    <li key={`${x.kind}:${x.item_id}`} className="flex items-center gap-2 text-xs text-slate-700">
      {running ? <Loader2 className="size-3.5 animate-spin text-ai-600" /> : <Clock3 className="size-3.5 text-slate-400" />}
      <span className="min-w-0 flex-1 truncate">{x.label ?? x.cause?.label ?? `${x.kind} ${x.item_id}`}</span>
      <span className="shrink-0 text-[11px] text-slate-500">{running ? "running" : "queued"}{x.step ? ` · ${x.step}` : ""}</span>
    </li>
  );
  return (
    <section className="rounded-lg border border-ai-100 bg-ai-50/50 p-2.5" data-testid="ai-live">
      <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-ai-700">Now</h3>
      <ul className="space-y-1">{d.running.map((x) => row(x, true))}{d.queued.map((x) => row(x, false))}</ul>
    </section>
  );
}

const TRIGGER_TONE: Record<string, string> = {
  opened: "bg-sky-50 text-sky-700", asked: "bg-ai-50 text-ai-700", action: "bg-emerald-50 text-emerald-700",
  batch: "bg-amber-50 text-amber-800", storyline: "bg-brand-50 text-brand-700", background: "bg-slate-100 text-slate-600",
};

function ActionRow({ a, onNavigate }: { a: AiAction; onNavigate: () => void }) {
  const { user } = useAuth();
  const failed = ["failed", "unavailable", "refused", "needs_human"].includes(a.outcome);
  const cause = { trigger: a.trigger, note: a.note ?? undefined, user_id: a.by?.id, user_name: a.by?.name ?? undefined };
  const label = a.item?.label ?? a.item?.item_id;
  return (
    <li className="rounded-lg border border-slate-200 p-2.5" data-testid="ai-action" data-trigger={a.trigger}>
      <div className="flex items-start justify-between gap-2">
        <p className="text-sm font-medium text-slate-900">{a.what}</p>
        <span className="tabular shrink-0 text-[11px] text-slate-500">{clockTime(a.started_at)}</span>
      </div>
      {label && (
        <p className="mt-0.5 text-xs text-slate-600">
          {a.item?.link ? (
            <Link to={a.item.link} onClick={onNavigate} className="inline-flex items-center gap-0.5 text-brand-700 hover:underline">{label}<ArrowUpRight className="size-3" /></Link>
          ) : label}
        </p>
      )}
      <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[11px] text-slate-500">
        <span className={clsx("rounded px-1.5 py-0.5 font-medium", TRIGGER_TONE[a.trigger] ?? TRIGGER_TONE.background)} data-testid="ai-action-trigger">
          {a.trigger === "background" ? "background" : causeText(cause, user?.id)}
        </span>
        {a.by?.name && <span>{a.by.name}{a.by.role && ROLE_LABEL[a.by.role as Role] ? ` · ${ROLE_LABEL[a.by.role as Role]}` : ""}</span>}
        <span className="tabular">· {seconds(a.duration_ms / 1000)}</span>
        <span className="tabular">· {a.calls} call{a.calls === 1 ? "" : "s"}</span>
        <span className="tabular" data-testid="ai-action-cost" title={a.cost_estimated ? "Mock model: the list price of the tokens, nothing billed" : undefined}>· {a.cost_estimated ? "≈ " : ""}{usd(a.cost_usd)}</span>
        {failed && <span className="inline-flex items-center gap-0.5 text-amber-700"><AlertTriangle className="size-3" />{a.outcome.replace("_", " ")}</span>}
      </div>
    </li>
  );
}

// ---------- "Ask AI for these N" ----------

/**
 * A list page never runs AI by itself: it says how many items the rules checked whose AI has not run, and offers
 * to run them, stating the time and calls first. Progress follows the session's activity.
 */
export function AiBatchBar({ items, noun, checked, theme = "app", onDone }: {
  items: { kind: string; id: string }[];
  /** "order reviews", "exceptions" … */
  noun: string;
  /** what the rules did, e.g. "checked by the rules" */
  checked?: string;
  theme?: "app" | "ct";
  onDone?: () => void;
}) {
  const kinds = useAiKinds();
  const queryClient = useQueryClient();
  const activity = useAiActivity("session", { open: false });
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState<{ ids: Set<string>; total: number; at: number } | null>(null);
  const max = kinds.data?.batch_max ?? 50;
  const take = items.slice(0, max);
  const estimate = useMemo(() => {
    const k = kinds.data;
    if (!k) return null;
    const calls = take.reduce((n, i) => n + (k.kinds[i.kind]?.calls ?? 1), 0);
    const s = take.reduce((n, i) => n + (k.kinds[i.kind]?.typical_s ?? 5), 0) / (k.concurrency || 1);
    return { calls, s };
  }, [kinds.data, take]);
  const live = activity.data ? [...activity.data.running, ...activity.data.queued] : [];
  const left = sent ? live.filter((x) => sent.ids.has(`${x.kind}:${x.item_id}`)).length : 0;
  useEffect(() => {
    if (sent && activity.data && left === 0 && Date.now() - sent.at > 1500) {
      setSent(null);
      onDone?.();
      void queryClient.invalidateQueries();
    }
  }, [sent, left, activity.data, onDone, queryClient]);

  const ct = theme === "ct";
  const shell = ct ? "border-ct-border bg-ct-surface text-ct-text" : "border-ai-100 bg-ai-50/50 text-slate-700";
  const muted = ct ? "text-ct-muted" : "text-slate-500";
  if (sent) {
    const done = sent.total - left;
    return (
      <div className={clsx("rounded-lg border px-3 py-2 text-xs", shell)} role="status" data-testid="ai-batch-progress">
        <p className="flex items-center gap-2"><Loader2 className={clsx("size-3.5 animate-spin", ct ? "text-ct-ai" : "text-ai-600")} />
          AI: {done} of {sent.total} {noun} done{left ? ` · ${left} to go` : ""}</p>
        <div className={clsx("mt-1.5 h-1 overflow-hidden rounded-full", ct ? "bg-ct-raised" : "bg-ai-100")}>
          <div className={clsx("h-full rounded-full transition-[width]", ct ? "bg-ct-ai" : "bg-ai-600")} style={{ width: `${Math.round((done / Math.max(1, sent.total)) * 100)}%` }} />
        </div>
      </div>
    );
  }
  if (!items.length) return null;
  const run = async () => {
    setError(null);
    try {
      const out = await runBatch(take);
      setConfirming(false);
      setSent({ ids: new Set(take.map((i) => `${i.kind}:${i.id}`)), total: out.queued, at: Date.now() });
      void queryClient.invalidateQueries({ queryKey: ["ai-activity"] });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The request did not reach the server");
    }
  };
  return (
    <div className={clsx("rounded-lg border px-3 py-2 text-xs", shell)} data-testid="ai-batch">
      <div className="flex flex-wrap items-center gap-2">
        <Sparkles className={clsx("size-3.5", ct ? "text-ct-ai" : "text-ai-600")} />
        <span className="min-w-[11rem] flex-1" data-testid="ai-batch-count">
          <span className="font-medium">{items.length}</span> {noun} {checked ?? "checked by the rules"}, AI not run
        </span>
        {!confirming && (
          <button type="button" onClick={() => setConfirming(true)} data-testid="ai-batch-open"
            className={clsx("inline-flex h-7 items-center gap-1 rounded-md px-2 font-medium", ct ? "border border-ct-border hover:bg-ct-raised" : "border border-ai-100 bg-white text-ai-700 hover:bg-ai-50")}>
            <Wand2 className="size-3.5" />Ask AI for {take.length < items.length ? `the first ${take.length}` : `these ${take.length}`}
          </button>
        )}
      </div>
      {confirming && (
        <div className="mt-2 space-y-2" data-testid="ai-batch-confirm">
          <p className={muted}>
            {estimate
              ? <>About <span className="font-medium">{estimate.calls} model call{estimate.calls === 1 ? "" : "s"}</span>, roughly <span className="font-medium">{seconds(estimate.s)}</span> ({kinds.data?.concurrency ?? 2} at a time). Results appear on each item as they finish, and each call shows in AI activity.</>
              : "Working out how long it takes…"}
          </p>
          <div className="flex gap-2">
            <button type="button" onClick={() => void run()} data-testid="ai-batch-run"
              className={clsx("inline-flex h-7 items-center gap-1 rounded-md px-2.5 font-medium text-white", ct ? "bg-ct-ai" : "bg-ai-600 hover:bg-ai-700")}>
              <Wand2 className="size-3.5" />Run {take.length}
            </button>
            <button type="button" onClick={() => setConfirming(false)} className={clsx("h-7 rounded-md px-2.5", muted)}>Cancel</button>
          </div>
        </div>
      )}
      {error && <p className="mt-1 text-rose-600" role="alert">{error}</p>}
    </div>
  );
}
