// The hospital flow Control Tower (spec 7.1): one screen for three questions — do we have enough beds today, where
// is the bottleneck, what do we do next. Three columns (ED / beds / OR) from FHIR, the exception stream at the
// bottom, the action drawer on the right, the simulator's control bar on top. The boards poll every second while
// the simulator runs (the API's hospital loop ticks every second), every three seconds otherwise.

import { useQueryClient } from "@tanstack/react-query";
import { Activity } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { hhmm, useBoard, type Board, type Severity } from "./api";
import { ActionDrawer } from "./ActionDrawer";
import { BedsColumn } from "./BedsColumn";
import { ControlBar } from "./ControlBar";
import { EdColumn } from "./EdColumn";
import { ExceptionStream } from "./ExceptionStream";
import { ErrorPanel, HeadlineBar } from "./kit";
import { OrColumn } from "./OrColumn";
import { UnitDrill } from "./UnitDrill";

const THEME_KEY = "ioc.ct.theme";
const RANK: Record<Severity, number> = { high: 2, med: 1, low: 0 };

function savedTheme(): "dark" | "light" {
  try {
    return localStorage.getItem(THEME_KEY) === "light" ? "light" : "dark";
  } catch {
    return "dark";
  }
}

function headline(board: Board): { severity: Severity | null; text: string } {
  const open = board.exceptions.filter((e) => e.status === "open");
  if (open.length === 0) {
    const k = board.kpis;
    return {
      severity: null,
      text: `No open exceptions. Hospital at ${Math.round(k.occupancy * 100)}%, ${k.ed_census} in the ED, ${k.or_done} of ${k.or_cases} OR cases done.`,
    };
  }
  const top = [...open].sort((a, b) => RANK[b.severity] - RANK[a.severity])[0];
  const high = open.filter((e) => e.severity === "high").length;
  return {
    severity: top.severity,
    text: `${open.length} open exception${open.length === 1 ? "" : "s"}${high ? ` (${high} high)` : ""}. Most urgent: ${top.title}.`,
  };
}

export function ControlTowerPage() {
  const [theme, setTheme] = useState<"dark" | "light">(savedTheme);
  const [fast, setFast] = useState(false);
  const board = useBoard(fast);
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<string | null>(null);
  const [unit, setUnit] = useState<string | null>(null);
  const data = board.data;
  const running = Boolean(data?.clock.running || data?.job?.status === "running");

  useEffect(() => setFast(running), [running]);
  useEffect(() => {
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch {
      /* storage unavailable: the theme lasts for this page only */
    }
  }, [theme]);

  const refresh = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ["ct-board"] });
    void queryClient.invalidateQueries({ queryKey: ["ct-unit"] });
  }, [queryClient]);
  const closeDrawer = useCallback(() => setSelected(null), []);
  const closeUnit = useCallback(() => setUnit(null), []);
  const head = data ? headline(data) : null;

  return (
    <div className="ct -mx-4 -my-5 min-h-[calc(100vh-57px)] bg-ct-bg p-3 text-ct-text md:-mx-6" data-theme={theme} data-testid="control-tower">
      <div className="space-y-3">
        <ControlBar board={data} theme={theme} onTheme={() => setTheme((t) => (t === "dark" ? "light" : "dark"))} onChanged={refresh} />
        {board.error && !data ? (
          <div className="rounded-xl border border-ct-border bg-ct-surface"><ErrorPanel error={board.error} onRetry={() => board.refetch()} /></div>
        ) : (
          <>
            <div className="grid gap-3 lg:grid-cols-[1fr_auto]">
              <HeadlineBar severity={head?.severity ?? null}>{head ? head.text : "Loading the hospital…"}</HeadlineBar>
              <div className="flex items-center gap-2 overflow-hidden rounded-xl border border-ct-border bg-ct-surface px-3 py-2 text-[11px] text-ct-muted lg:max-w-md" data-testid="event-ticker">
                <Activity className="size-3.5 shrink-0 text-ct-accent" />
                {data?.events.length ? (
                  <span className="truncate">
                    {data.events.slice(0, 3).map((e) => `${hhmm(e.at)} ${e.type}${e.encounter ? ` ${e.encounter}` : ""}`).join(" · ")}
                  </span>
                ) : (
                  <span>No events yet: run the simulator</span>
                )}
              </div>
            </div>
            <div className="grid gap-3 xl:grid-cols-3">
              <EdColumn board={data} />
              <BedsColumn board={data} onUnit={setUnit} />
              <OrColumn board={data} />
            </div>
            <ExceptionStream items={data?.exceptions} loading={board.isLoading} selected={selected} onSelect={setSelected} />
            {data && (
              <p className="px-1 text-[11px] text-ct-muted">
                Synthetic hospital. Predictions come from four models trained on synthetic data: they show that the pipeline works, not clinical or
                operational performance. AI narratives are drafts; a person decides. Board built in {data.build_ms} ms at hospital time {hhmm(data.now)}.
              </p>
            )}
          </>
        )}
      </div>
      <ActionDrawer id={selected} onClose={closeDrawer} />
      <UnitDrill unitId={unit} onClose={closeUnit} live={running} />
    </div>
  );
}
