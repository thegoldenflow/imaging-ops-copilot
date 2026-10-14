// The daily AI budget (LLM_DAILY_BUDGET_USD) and the idle pause (IDLE_PAUSE_MINUTES) of the server: today's model
// spend against the cap, and what happens when it is reached. Numbers come from /api/aiops/overview.

import clsx from "clsx";
import { PiggyBank } from "lucide-react";
import { dateTime } from "../../lib/format";
import type { BudgetStatus, IdleStatus } from "./types";

export function BudgetStrip({ budget, idle }: { budget: BudgetStatus; idle: IdleStatus }) {
  const cap = budget.limit_usd;
  const share = cap ? Math.min(1, budget.spent_usd / cap) : 0;
  const pause = idle.pause_minutes > 0
    ? `Background AI work pauses after ${idle.pause_minutes} min without anyone using the demo.`
    : "Background AI work never pauses (IDLE_PAUSE_MINUTES=0).";
  return (
    <div
      className={clsx("flex flex-wrap items-center gap-x-4 gap-y-2 rounded-lg border px-3 py-2 text-sm",
        budget.reached ? "border-amber-300 bg-amber-50 text-amber-900" : "border-slate-200 bg-white text-slate-700")}
      data-testid="aiops-budget"
    >
      <PiggyBank className="size-4 shrink-0" />
      {cap == null ? (
        <span>No daily AI budget set · spent today <span className="tabular font-semibold">${budget.spent_usd.toFixed(2)}</span></span>
      ) : (
        <>
          <span>
            Daily AI budget: <span className="tabular font-semibold">${budget.spent_usd.toFixed(2)}</span> of ${cap.toFixed(2)} spent today
          </span>
          <div className="h-1.5 w-32 overflow-hidden rounded-full bg-slate-200" aria-hidden>
            <div className={clsx("h-full rounded-full", budget.reached ? "bg-amber-500" : "bg-brand-600")} style={{ width: `${Math.round(share * 100)}%` }} />
          </div>
          {budget.reached && (
            <span data-testid="aiops-budget-reached">
              Reached: new model calls are refused until {dateTime(budget.resets_at)} (server time); AI fields show
              "temporarily unavailable" and everything else works.{budget.refused_today ? ` ${budget.refused_today} call${budget.refused_today === 1 ? "" : "s"} refused today.` : ""}
            </span>
          )}
        </>
      )}
      <span className="text-xs text-slate-500">{pause}</span>
    </div>
  );
}
