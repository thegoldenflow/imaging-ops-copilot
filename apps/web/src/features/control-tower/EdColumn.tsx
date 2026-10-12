// The ED board (spec 7.1): arrival, CTAS, waiting, admission probability, orders, target unit; in the ED, waiting
// for a bed, predicted wait in the next hour, at risk of leaving unseen.

import clsx from "clsx";
import { Ambulance, BedDouble, Sparkles } from "lucide-react";
import { hhmm, mins, type Board, type EdRow } from "./api";
import { EmptyPanel, KpiTile, Minutes, Panel, SkeletonRows, StatusPill } from "./kit";

const CTAS_TONE: Record<number, string> = {
  1: "bg-ct-critical text-white",
  2: "bg-ct-critical-soft text-ct-critical",
  3: "bg-ct-warning-soft text-ct-warning",
  4: "bg-ct-info-soft text-ct-info",
  5: "bg-ct-raised text-ct-muted",
};

function order(rows: EdRow[]) {
  return [...rows].sort((a, b) => {
    const boarding = (r: EdRow) => (r.bed_requested_at ? 0 : 1);
    if (boarding(a) !== boarding(b)) return boarding(a) - boarding(b);
    if (a.bed_requested_at && b.bed_requested_at) return (b.boarding_minutes ?? 0) - (a.boarding_minutes ?? 0);
    return (a.ctas ?? 9) - (b.ctas ?? 9) || b.minutes_in_ed - a.minutes_in_ed;
  });
}

export function EdColumn({ board }: { board: Board | undefined }) {
  const k = board?.kpis;
  return (
    <Panel title="Emergency department" icon={<Ambulance className="size-4 text-ct-accent" />} testId="board-ed"
      actions={k && <span className="text-[11px] text-ct-muted">{k.ed_census} patients</span>}>
      <div className="grid grid-cols-2 gap-2 p-2.5">
        <KpiTile label="In the ED" value={k?.ed_census ?? "–"} hint={k ? `${k.ed_waiting} waiting to be seen` : undefined} testId="kpi-ed-census" />
        <KpiTile label="Waiting for a bed" value={k?.ed_boarders ?? "–"} tone={k && k.ed_boarders_over_2h >= 3 ? "critical" : k && k.ed_boarders ? "warning" : undefined}
          hint={k ? `${k.ed_boarders_over_2h} over 2 h` : undefined} testId="kpi-ed-boarders" />
        <KpiTile label="Predicted wait, next hour" value={k?.ed_predicted_wait ? mins(Math.round(k.ed_predicted_wait.value)) : "–"}
          title={k?.ed_predicted_wait?.sentence} hint={k?.ed_predicted_wait ? <span className="inline-flex items-center gap-1"><Sparkles className="size-3 text-ct-ai" />model</span> : "no model"} testId="kpi-ed-wait" />
        <KpiTile label="At risk of leaving unseen" value={k?.lwbs_risk ?? "–"} tone={k?.lwbs_risk ? "warning" : undefined} hint="CTAS 4–5 waiting 2 h+" />
      </div>
      {!board ? (
        <SkeletonRows rows={6} />
      ) : board.ed.length === 0 ? (
        <EmptyPanel title="Nobody in the ED" hint="Arrivals appear here as the simulator runs." />
      ) : (
        <ul className="max-h-[46vh] divide-y divide-ct-border overflow-y-auto" data-testid="ed-list">
          {order(board.ed).map((row) => <EdItem key={row.encounter_id} row={row} />)}
        </ul>
      )}
    </Panel>
  );
}

function EdItem({ row }: { row: EdRow }) {
  const boarding = Boolean(row.bed_requested_at);
  return (
    <li className="px-3 py-2" data-testid="ed-row">
      <div className="flex items-center gap-2">
        <span className={clsx("grid size-6 shrink-0 place-items-center rounded-md text-xs font-bold", row.ctas ? CTAS_TONE[row.ctas] : "bg-ct-raised text-ct-muted")}
          title={row.ctas ? `CTAS ${row.ctas}` : "Not triaged yet"}>
          {row.ctas ?? "?"}
        </span>
        <div className="min-w-0 flex-1">
          <p className="flex items-center gap-1.5 text-xs text-ct-text">
            <span className="font-medium">{row.location && row.location !== "ED" ? row.location : "Waiting room"}</span>
            <span className="text-ct-muted">· {row.identified && row.mrn ? `MRN ${row.mrn}` : "MRN hidden"}</span>
          </p>
          <p className="text-[11px] text-ct-muted">
            arrived {hhmm(row.arrival)} · {row.orders_open}/{row.orders_total} orders open
          </p>
        </div>
        <div className="shrink-0 text-right text-xs">
          {boarding ? (
            <StatusPill tone={(row.boarding_minutes ?? 0) > 120 ? "critical" : "warning"} icon={BedDouble}>
              bed {row.target_unit ?? "?"} · {mins(row.boarding_minutes)}
            </StatusPill>
          ) : row.waiting_to_be_seen ? (
            <span className="text-ct-muted">wait <Minutes value={row.wait_minutes} warnAt={60} alertAt={120} /></span>
          ) : (
            <span className="text-ct-muted">seen · {mins(row.minutes_in_ed)}</span>
          )}
        </div>
      </div>
      {row.admit && !boarding && (
        <div className="mt-1.5 flex items-center gap-2" title={row.admit.sentence}>
          <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-ct-raised">
            <div className={clsx("h-full rounded-full", row.admit.value >= 0.7 ? "bg-ct-critical" : row.admit.value >= 0.4 ? "bg-ct-warning" : "bg-ct-info")}
              style={{ width: `${Math.max(3, Math.round(row.admit.value * 100))}%` }} />
          </div>
          <span className="tabular w-24 shrink-0 text-right text-[11px] text-ct-muted">
            admit {Math.round(row.admit.value * 100)}%{row.target_unit ? ` · ${row.target_unit}` : ""}
          </span>
        </div>
      )}
      {row.admit && !boarding && (
        <p className="mt-0.5 truncate text-[11px] text-ct-muted" data-testid="admit-explanation">{row.admit.sentence}</p>
      )}
      {row.lwbs_risk && <StatusPill tone="warning" className="mt-1">may leave without being seen</StatusPill>}
    </li>
  );
}
