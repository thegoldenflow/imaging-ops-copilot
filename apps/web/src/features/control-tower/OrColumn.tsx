// The OR board (spec 7.1): case, surgeon, booked and predicted minutes, overrun risk, pre-op readiness (consent,
// fasting, labs, blood group); block utilisation, cases at risk of cancellation, predicted end of the day. The
// room lanes show each case's booking (outline) against its prediction (filled).

import clsx from "clsx";
import { Scissors, Sparkles } from "lucide-react";
import { hhmm, PREOP_LABEL, pct, type Board, type OrCase } from "./api";
import { EmptyPanel, KpiTile, Panel, SeverityPill, SkeletonRows, StatusPill } from "./kit";

const START_HOUR = 7;
const END_HOUR = 20;

function minutesOfDay(iso: string, day: string) {
  const offset = iso.slice(0, 10) === day ? 0 : iso.slice(0, 10) > day ? 24 * 60 : -24 * 60;
  return offset + Number(iso.slice(11, 13)) * 60 + Number(iso.slice(14, 16));
}

function position(iso: string | null, day: string) {
  if (!iso) return null;
  const span = (END_HOUR - START_HOUR) * 60;
  return Math.min(100, Math.max(0, ((minutesOfDay(iso, day) - START_HOUR * 60) / span) * 100));
}

export function OrColumn({ board }: { board: Board | undefined }) {
  const k = board?.kpis;
  const cases = board?.or_cases.filter((c) => c.status !== "cancelled") ?? [];
  const cancelled = board?.or_cases.filter((c) => c.status === "cancelled").length ?? 0;
  return (
    <Panel title="Operating rooms" icon={<Scissors className="size-4 text-ct-accent" />} testId="board-or"
      actions={k && <span className="text-[11px] text-ct-muted">{k.or_done} done · {k.or_in_progress} in the OR · {k.or_cases} today</span>}>
      <div className="grid grid-cols-3 gap-2 p-2.5">
        <KpiTile label="Block utilisation" value={k ? pct(k.or_utilization) : "–"} tone={k?.or_utilization && k.or_utilization > 1 ? "critical" : undefined}
          hint={k ? `booked ${pct(k.or_booked_utilization)}` : undefined} title="Predicted minutes of the elective lists / block minutes (08:00–16:00)" testId="kpi-or-utilization" />
        <KpiTile label="Cancellation risk" value={k?.or_cancellation_risk ?? "–"} tone={k?.or_cancellation_risk ? "warning" : undefined} hint="pre-op gap or no bed" />
        <KpiTile label="Predicted end" value={k?.or_predicted_end ? hhmm(k.or_predicted_end) : "–"} hint="last case of the day" />
      </div>
      {!board ? (
        <SkeletonRows rows={5} />
      ) : cases.length === 0 ? (
        <EmptyPanel title="No OR cases today" hint={cancelled ? `${cancelled} cancelled` : "Weekend lists have emergency cases only."} />
      ) : (
        <>
          <Lanes board={board} cases={cases} />
          <ul className="max-h-[30vh] divide-y divide-ct-border overflow-y-auto" data-testid="or-list">
            {cases.map((c) => <CaseItem key={c.appointment_id} c={c} />)}
          </ul>
        </>
      )}
    </Panel>
  );
}

function Lanes({ board, cases }: { board: Board; cases: OrCase[] }) {
  const day = board.now.slice(0, 10);
  const now = position(board.now, day);
  const rooms = board.or_rooms.map((r) => r.id);
  return (
    <div className="px-3 pb-2" aria-label="Room timeline">
      <div className="relative mb-1 h-3 text-[10px] text-ct-muted">
        {[8, 10, 12, 14, 16, 18].map((h) => (
          <span key={h} className="absolute -translate-x-1/2" style={{ left: `${((h - START_HOUR) / (END_HOUR - START_HOUR)) * 100}%` }}>{h}:00</span>
        ))}
      </div>
      <div className="relative space-y-1">
        {now != null && <div className="absolute inset-y-0 z-10 w-px bg-ct-accent" style={{ left: `${now}%` }} title="now" />}
        <div className="pointer-events-none absolute inset-y-0 border-r border-dashed border-ct-muted/50" style={{ left: `${((16 - START_HOUR) / (END_HOUR - START_HOUR)) * 100}%` }} title="block end 16:00" />
        {rooms.map((room) => (
          <div key={room} className="flex items-center gap-2">
            <span className="w-11 shrink-0 text-[10px] font-medium text-ct-muted">{room}</span>
            <div className="relative h-4 flex-1 rounded bg-ct-raised">
              {cases.filter((c) => c.room === room).map((c) => {
                const b0 = position(c.booked_start, day) ?? 0;
                const b1 = position(c.booked_end, day) ?? b0;
                const p0 = position(c.predicted_start, day) ?? b0;
                const p1 = position(c.predicted_end, day) ?? b1;
                const tone = c.overrun_risk === "high" ? "bg-ct-critical" : c.overrun_risk === "med" ? "bg-ct-warning" : c.status === "fulfilled" ? "bg-ct-muted" : "bg-ct-ok";
                return (
                  <div key={c.appointment_id} title={`${c.procedure}: booked ${hhmm(c.booked_start)}–${hhmm(c.booked_end)}, predicted ${hhmm(c.predicted_start)}–${hhmm(c.predicted_end)}`}>
                    <div className="absolute inset-y-0 rounded border border-ct-muted/70" style={{ left: `${b0}%`, width: `${Math.max(0.5, b1 - b0)}%` }} />
                    <div className={clsx("absolute inset-y-1 rounded-sm opacity-80", tone)} style={{ left: `${p0}%`, width: `${Math.max(0.5, p1 - p0)}%` }} />
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function CaseItem({ c }: { c: OrCase }) {
  const items = Object.keys(PREOP_LABEL);
  return (
    <li className="px-3 py-2" data-testid="or-row">
      <div className="flex items-center justify-between gap-2">
        <p className="min-w-0 truncate text-xs text-ct-text">
          <span className="font-medium">{c.room} {hhmm(c.booked_start)}</span>
          <span className="text-ct-muted"> · {c.procedure} · {c.surgeon}</span>
        </p>
        {c.status === "arrived" ? <StatusPill tone="info">in the OR</StatusPill>
          : c.status === "fulfilled" ? <StatusPill tone="muted">done</StatusPill>
          : c.overrun_risk ? <SeverityPill severity={c.overrun_risk} /> : null}
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-ct-muted">
        <span className="tabular" title={c.predicted?.sentence}>
          {c.booked_minutes} booked → <b className={clsx("text-ct-text", (c.overrun_minutes ?? 0) >= 30 && "text-ct-critical")}>{c.predicted_minutes ?? "–"}</b> min
          {c.predicted && <Sparkles className="ml-1 inline size-3 text-ct-ai" aria-label="predicted by the model" />}
        </span>
        {c.status === "booked" && (
          <span className="flex items-center gap-1" aria-label="Pre-op checklist">
            {items.map((i) => (
              <span key={i} title={`${PREOP_LABEL[i]}: ${c.preop[i] ?? "no task"}`}
                className={clsx("rounded px-1 text-[10px] font-medium", c.preop[i] === "open" ? "bg-ct-critical-soft text-ct-critical" : "bg-ct-ok-soft text-ct-ok")}>
                {PREOP_LABEL[i]}
              </span>
            ))}
          </span>
        )}
        {c.cancellation_risk && <StatusPill tone="warning" title={c.cancellation_reasons.join("; ")}>cancellation risk</StatusPill>}
      </div>
    </li>
  );
}
