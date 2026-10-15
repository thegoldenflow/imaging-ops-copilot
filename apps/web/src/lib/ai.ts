// AI on demand (apps/api/app/agents/ondemand.py): every item that can get AI output carries the same AI block, and the
// AI runs only because a person did something: opened the item, pressed Ask AI, submitted new work, pressed a
// storyline step or asked for a batch. Each run records who caused it (the browser's demo session, sent as
// X-Demo-Session by lib/api.ts), which the "What the AI did in this demo" panel lists.

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, post } from "./api";

export type AiStateName = "not_requested" | "queued" | "running" | "done" | "unavailable" | "skipped";
export type AiTrigger = "opened" | "asked" | "action" | "batch" | "storyline" | "background";

export interface AiCause {
  trigger: AiTrigger;
  session?: string;
  user_id?: string;
  user_name?: string;
  role?: string;
  kind?: string;
  item_id?: string;
  label?: string;
  link?: string;
  note?: string;
}

export interface AiBlock {
  kind: string;
  item_id: string;
  state: AiStateName;
  auto: boolean; // opening the item runs it
  typical_s: number;
  steps: string[];
  running_text: string;
  error: string | null;
  started_at?: string | null;
  queued_at?: string | null;
  ahead?: number;
  step?: string | null;
  generated_at: string | null;
  duration_ms: number | null;
  run_id: string | null;
  cause: AiCause | null;
}

export interface AiAction {
  id: string;
  run_ids: string[];
  what: string;
  agents: string[];
  item: { kind: string | null; item_id: string | null; label: string | null; link: string | null } | null;
  trigger: AiTrigger;
  trigger_text: string;
  note: string | null;
  by: { id: string; name: string | null; role: string | null } | null;
  session: string | null;
  started_at: string;
  finished_at: string;
  duration_ms: number;
  calls: number;
  cost_usd: number;
  cost_estimated: boolean; // a mock call: its list-price estimate (nothing billed)
  tokens: number;
  outcome: string;
  mode: string | null;
  models: string[];
}

export interface AiLive {
  kind: string;
  item_id: string;
  started_at?: string;
  queued_at?: string;
  step?: string | null;
  label?: string | null;
  link?: string | null;
  cause: AiCause | null;
}

export interface AiActivity {
  scope: "session" | "all";
  session: string | null;
  mode: string;
  background_ai: "on_demand" | "eager";
  since: string | null;
  totals: { runs: number; calls: number; cost_usd: number; cost_estimated: boolean; actions: number; seconds: number };
  running: AiLive[];
  queued: AiLive[];
  actions: AiAction[];
  kinds: Record<string, { noun: string; calls: number; typical_s: number }>;
}

export interface AiKinds {
  concurrency: number;
  batch_max: number;
  background_ai: "on_demand" | "eager";
  kinds: Record<string, { noun: string; calls: number; typical_s: number; running_text: string; steps: string[] }>;
}

// ---------- running an item's AI ----------

export type RunAi = (trigger: "opened" | "asked") => Promise<unknown>;

/**
 * Runs an item's AI when it opens (once per item and opening; the owner's choice: opening runs it, no second
 * click) and on Ask AI / Retry. `busy`/`startedAt` cover this browser's own request; the block's own state covers
 * a run another person or the job loop started. A failure shows the button instead of asking again by itself.
 */
export function useAiRun(ai: AiBlock | null | undefined, run: RunAi, { auto = true }: { auto?: boolean } = {}) {
  const [busy, setBusy] = useState(false);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const asked = useRef<string | null>(null);
  const queryClient = useQueryClient();
  const key = ai ? `${ai.kind}:${ai.item_id}` : null;
  const runRef = useRef(run);
  runRef.current = run;

  const go = useCallback(async (trigger: "opened" | "asked") => {
    setBusy(true);
    setError(null);
    setStartedAt(Date.now());
    // the header's AI activity follows at once: running now, then the finished action
    setTimeout(() => void queryClient.invalidateQueries({ queryKey: ["ai-activity"] }), 300);
    try {
      await runRef.current(trigger);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The request did not reach the server");
    } finally {
      setBusy(false);
      setStartedAt(null);
      void queryClient.invalidateQueries({ queryKey: ["ai-activity"] });
    }
  }, [queryClient]);

  useEffect(() => {
    if (!ai || !key || !auto || !ai.auto || asked.current === key) return;
    if (ai.state === "not_requested" || ai.state === "queued") {
      asked.current = key; // once per opening
      void go("opened");
    }
  }, [ai, key, auto, go]);

  return { busy, startedAt, error, ask: () => void go("asked") };
}

/** Seconds since `since` (ms epoch or ISO), ticking while `active`. */
export function useElapsed(since: number | string | null | undefined, active: boolean): number {
  const start = since == null ? null : typeof since === "number" ? since : new Date(since).getTime();
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active || start == null) return;
    const t = setInterval(() => setNow(Date.now()), 200);
    return () => clearInterval(t);
  }, [active, start]);
  return start == null ? 0 : Math.max(0, (now - start) / 1000);
}

// ---------- batches and the activity panel ----------

export const useAiKinds = () => useQuery({ queryKey: ["ai-kinds"], queryFn: () => api<AiKinds>("/api/ai/kinds"), staleTime: 15_000 });

export function useAiActivity(scope: "session" | "all", { open }: { open: boolean }) {
  return useQuery({
    queryKey: ["ai-activity", scope],
    queryFn: () => api<AiActivity>(`/api/ai/activity?scope=${scope}`),
    refetchInterval: (q) => {
      const d = q.state.data;
      const busy = d && (d.running.length > 0 || d.queued.length > 0);
      return busy ? 1500 : open ? 4000 : 15_000;
    },
  });
}

export interface BatchResult {
  queued: number;
  skipped: number;
  calls: number;
  expected_s: number;
}

export const runBatch = (items: { kind: string; id: string }[]) => post<BatchResult>("/api/ai/batch", { items });

// ---------- words ----------

export function seconds(s: number | null | undefined): string {
  if (s == null) return "–";
  if (s < 10) return `${s.toFixed(1)} s`;
  if (s < 90) return `${Math.round(s)} s`;
  return `${Math.floor(s / 60)} min ${String(Math.round(s % 60)).padStart(2, "0")} s`;
}

export function clockTime(iso: string | null | undefined): string {
  if (!iso) return "–";
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

/** "when you opened it", "you pressed Ask AI", "your action: submitted in the portal", "Storyline step 5" … */
export function causeText(cause: AiCause | null | undefined, me?: string | null): string {
  if (!cause) return "in the background";
  const you = me && cause.user_id === me;
  const who = you ? "you" : cause.user_name ?? "someone";
  switch (cause.trigger) {
    case "opened":
      return you ? "when you opened it" : `when ${who} opened it`;
    case "asked":
      return you ? "you pressed Ask AI" : `${who} pressed Ask AI`;
    case "batch":
      return `${you ? "your" : `${who}'s`} batch${cause.note ? ` (${cause.note})` : ""}`;
    case "storyline":
      return cause.note ?? "a storyline step";
    case "action":
      return cause.note ? `${you ? "your action" : who}: ${cause.note}` : you ? "your action" : `${who}'s action`;
    default:
      return "in the background (eager mode)";
  }
}

export const usd = (v: number) => (v === 0 ? "$0" : v < 0.01 ? `$${v.toFixed(4)}` : `$${v.toFixed(3)}`);
