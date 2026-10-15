// The AI states every item shares (lib/ai.ts): not requested (the rule results stand; Ask AI) → queued → running
// (what the AI is doing, how long so far against how long it usually takes) → done (when it was generated, how long
// it took, what caused it) → unavailable (why; Retry; the rules still shown). Two looks: the app's (slate and the AI
// purple) and the Control Tower's (`ct` tokens).

import clsx from "clsx";
import { AlertTriangle, Clock3, Loader2, RotateCcw, Sparkles, Wand2 } from "lucide-react";
import type { ReactNode } from "react";
import { causeText, clockTime, seconds, useElapsed, type AiBlock } from "../lib/ai";
import { useAuth } from "../lib/auth";

type Theme = "app" | "ct";

const LOOK = {
  app: {
    box: "rounded-lg border border-dashed border-ai-100 bg-ai-50/40 p-3 text-xs text-slate-700",
    muted: "text-slate-500",
    strong: "text-slate-900",
    icon: "text-ai-600",
    warnBox: "rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900",
    track: "bg-ai-100",
    bar: "bg-ai-600",
    button: "bg-ai-600 text-white hover:bg-ai-700",
    ghost: "border border-amber-300 bg-white text-amber-900 hover:bg-amber-100",
  },
  ct: {
    box: "rounded-lg border border-dashed border-ct-border bg-ct-ai-soft p-3 text-xs text-ct-text",
    muted: "text-ct-muted",
    strong: "text-ct-text",
    icon: "text-ct-ai",
    warnBox: "rounded-lg border border-ct-border bg-ct-warning-soft p-3 text-xs text-ct-text",
    track: "bg-ct-raised",
    bar: "bg-ct-ai",
    button: "bg-ct-ai text-white hover:opacity-90",
    ghost: "border border-ct-border text-ct-text hover:bg-ct-raised",
  },
};

function AskButton({ onClick, busy, theme, children, testId }: { onClick: () => void; busy?: boolean; theme: Theme; children: ReactNode; testId: string }) {
  return (
    <button type="button" onClick={onClick} disabled={busy}
      className={clsx("inline-flex h-7 shrink-0 items-center gap-1.5 rounded-md px-2.5 text-xs font-medium disabled:opacity-50", LOOK[theme].button)}
      data-testid={testId}>
      {busy ? <Loader2 className="size-3.5 animate-spin" /> : <Wand2 className="size-3.5" />}{children}
    </button>
  );
}

/**
 * The AI part of an item in every state but done (done: the caller renders the output under an <AiMeta/> line).
 * `busy`/`startedAt`: this browser's own request in flight; `error`: its failure.
 */
export function AiStatus({ ai, busy, startedAt, error, onAsk, theme = "app", rulesNote = "The rule results stand on their own.", what, canRetry = true }: {
  ai: AiBlock;
  busy?: boolean;
  startedAt?: number | null;
  error?: string | null;
  onAsk: () => void;
  theme?: Theme;
  rulesNote?: string;
  /** what the AI writes here, e.g. "a commentary on this order" */
  what?: string;
  /** false: a failed run is not asked for again (a reconciliation keeps the rules' fallback) */
  canRetry?: boolean;
}) {
  const look = LOOK[theme];
  const running = busy || ai.state === "running";
  const since = busy ? startedAt : ai.started_at;
  const elapsed = useElapsed(since ?? null, running);
  if (running) {
    const typical = Math.max(0.5, ai.typical_s);
    const share = Math.min(0.95, elapsed / typical);
    const slow = elapsed > typical * 2 + 2;
    const stepIndex = ai.step ? ai.steps.indexOf(ai.step) : -1;
    return (
      <div className={look.box} role="status" aria-live="polite" data-testid="ai-running">
        <p className={clsx("flex items-center gap-2 font-medium", look.strong)}>
          <Loader2 className={clsx("size-3.5 animate-spin", look.icon)} />
          {ai.running_text}…
          {stepIndex >= 0 && <span className={clsx("font-normal", look.muted)}>{ai.step} · step {stepIndex + 1} of {ai.steps.length}</span>}
        </p>
        <div className={clsx("mt-2 h-1.5 overflow-hidden rounded-full", look.track)}>
          <div className={clsx("h-full rounded-full transition-[width] duration-200", look.bar)} style={{ width: `${Math.round(share * 100)}%` }} />
        </div>
        <p className={clsx("mt-1 flex flex-wrap justify-between gap-2 tabular-nums", look.muted)}>
          <span data-testid="ai-elapsed">{seconds(elapsed)}</span>
          <span>{slow ? "taking longer than usual: the model is slow right now" : `typically ~${seconds(typical)}`}</span>
        </p>
        <p className={clsx("mt-1", look.muted)}>{rulesNote}</p>
      </div>
    );
  }
  if (ai.state === "queued") {
    return (
      <div className={look.box} role="status" data-testid="ai-queued">
        <p className={clsx("flex items-center gap-2", look.strong)}>
          <Clock3 className={clsx("size-3.5", look.icon)} />
          Queued for the AI{ai.ahead ? ` (${ai.ahead} ahead)` : ""}: it starts in a moment, typically ~{seconds(ai.typical_s)} once it does.
        </p>
        <p className={clsx("mt-1", look.muted)}>{rulesNote}</p>
      </div>
    );
  }
  if (ai.state === "unavailable" || error) {
    const why = error ?? ai.error ?? "the model did not answer";
    return (
      <div className={look.warnBox} role="alert" data-testid="ai-unavailable">
        <div className="flex items-start gap-2">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
          <p className="min-w-0 flex-1"><span className="font-medium">AI unavailable:</span> {why}. {rulesNote}</p>
          {canRetry && <button type="button" onClick={onAsk}
            className={clsx("inline-flex h-7 shrink-0 items-center gap-1.5 rounded-md px-2.5 text-xs font-medium", look.ghost)}
            data-testid="ai-retry">
            <RotateCcw className="size-3.5" />Retry
          </button>}
        </div>
      </div>
    );
  }
  if (ai.state === "skipped") {
    return <p className={clsx("rounded-lg border border-dashed p-3 text-xs", look.muted)} data-testid="ai-skipped">No AI output: decided before it was written. {rulesNote}</p>;
  }
  // not requested
  return (
    <div className={clsx(look.box, "flex flex-wrap items-center gap-2")} data-testid="ai-not-requested">
      <Sparkles className={clsx("size-3.5 shrink-0", look.icon)} />
      <p className="min-w-[12rem] flex-1">
        <span className={clsx("font-medium", look.strong)}>AI not run yet.</span>{" "}
        {what ? `Ask for ${what}` : "Ask the AI"} (typically ~{seconds(ai.typical_s)}). {rulesNote}
      </p>
      <AskButton onClick={onAsk} busy={busy} theme={theme} testId="ai-ask">Ask AI</AskButton>
    </div>
  );
}

/** "AI draft · generated 10:42:05 · took 3.8 s · when you opened it": the line over an AI output. */
export function AiMeta({ ai, theme = "app", className }: { ai: AiBlock | null | undefined; theme?: Theme; className?: string }) {
  const { user } = useAuth();
  if (!ai || ai.state !== "done") return null;
  const look = LOOK[theme];
  return (
    <p className={clsx("flex flex-wrap items-center gap-x-1.5 text-[11px] tabular-nums", look.muted, className)} data-testid="ai-meta">
      <Clock3 className="size-3" />
      <span>generated {clockTime(ai.generated_at)}</span>
      {ai.duration_ms != null && <span>· took {seconds(ai.duration_ms / 1000)}</span>}
      <span>· {causeText(ai.cause, user?.id)}</span>
    </p>
  );
}

/** A small chip for lists: what the item's AI is doing. */
export function AiChip({ state, theme = "app" }: { state: string | undefined | null; theme?: Theme }) {
  if (!state || state === "done" || state === "skipped") return null;
  const text = { not_requested: "AI not run", queued: "AI queued", running: "AI running", unavailable: "AI unavailable" }[state] ?? state;
  const tone = theme === "ct"
    ? state === "unavailable" ? "text-ct-warning" : "text-ct-muted"
    : state === "unavailable" ? "text-amber-700" : state === "running" || state === "queued" ? "text-ai-700" : "text-slate-500";
  return (
    <span className={clsx("inline-flex items-center gap-1 whitespace-nowrap text-[11px]", tone)} data-testid="ai-chip" data-state={state}>
      {state === "running" ? <Loader2 className="size-3 animate-spin" /> : <Sparkles className="size-3" />}{text}
    </span>
  );
}
