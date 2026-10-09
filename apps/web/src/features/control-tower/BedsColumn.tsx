// The bed board (spec 7.1): per unit occupied / total, beds waiting for housekeeping, ALC patients, expected
// discharges (24 h), ED patients waiting for the unit; hospital occupancy and the net gap (expected admissions -
// expected discharges - free beds). A unit opens its bed grid (drill-down).

import clsx from "clsx";
import { BedDouble, ChevronRight } from "lucide-react";
import { pct, type Board, type UnitRow } from "./api";
import { KpiTile, Panel, SkeletonRows } from "./kit";

export function BedsColumn({ board, onUnit }: { board: Board | undefined; onUnit: (unit: string) => void }) {
  const k = board?.kpis;
  return (
    <Panel title="Beds" icon={<BedDouble className="size-4 text-ct-accent" />} testId="board-beds"
      actions={k && <span className="text-[11px] text-ct-muted">{k.occupied} / {k.beds} occupied</span>}>
      <div className="grid grid-cols-2 gap-2 p-2.5">
        <KpiTile label="Hospital occupancy" value={k ? pct(k.occupancy) : "–"} tone={k && k.occupancy >= 0.95 ? "critical" : k && k.occupancy >= 0.9 ? "warning" : "ok"}
          hint={k ? `${k.free} free · ${k.cleaning} cleaning` : undefined} testId="kpi-occupancy" />
        <KpiTile label="Net gap (24 h)" value={k ? (k.net_gap > 0 ? `${k.net_gap} short` : `${-k.net_gap} spare`) : "–"}
          tone={k && k.net_gap > 0 ? "critical" : k ? "ok" : undefined}
          hint={k ? `${k.expected_admissions} in − ${k.expected_discharges} out − ${k.free} free` : undefined}
          title="Expected admissions − expected discharges − free beds" testId="kpi-net-gap" />
      </div>
      {!board ? (
        <SkeletonRows rows={5} />
      ) : (
        <ul className="divide-y divide-ct-border" data-testid="unit-list">
          {board.units.map((u) => <UnitItem key={u.id} unit={u} onOpen={() => onUnit(u.id)} />)}
        </ul>
      )}
    </Panel>
  );
}

function UnitItem({ unit: u, onOpen }: { unit: UnitRow; onOpen: () => void }) {
  const tone = u.occupancy >= 0.95 ? "bg-ct-critical" : u.occupancy >= 0.9 ? "bg-ct-warning" : "bg-ct-ok";
  return (
    <li>
      <button onClick={onOpen} className="group w-full px-3 py-2.5 text-left hover:bg-ct-raised" data-testid={`unit-${u.id}`}>
        <div className="flex items-center justify-between gap-2">
          <span className="text-sm font-medium text-ct-text">{u.name}</span>
          <span className="flex items-center gap-1 text-xs">
            <span className={clsx("tabular font-semibold", u.occupancy >= 0.95 ? "text-ct-critical" : u.occupancy >= 0.9 ? "text-ct-warning" : "text-ct-text")}>
              {pct(u.occupancy)}
            </span>
            <span className="tabular text-ct-muted">{u.occupied}/{u.beds}</span>
            <ChevronRight className="size-3.5 text-ct-muted group-hover:text-ct-text" />
          </span>
        </div>
        <div className="mt-1.5 flex h-2 overflow-hidden rounded-full bg-ct-raised" aria-hidden>
          <div className={tone} style={{ width: `${(u.occupied / u.beds) * 100}%` }} />
          <div className="bg-ct-warning opacity-50" style={{ width: `${(u.cleaning / u.beds) * 100}%` }} />
        </div>
        <div className="mt-1.5 grid grid-cols-4 gap-1 text-[11px] text-ct-muted">
          <span title="Free beds (and beds waiting for housekeeping)"><b className="tabular text-ct-text">{u.free}</b> free{u.cleaning ? ` +${u.cleaning}` : ""}</span>
          <span title="Expected discharges in the next 24 h (discharge-ready: probability ≥ 0.7)"><b className="tabular text-ct-text">{u.expected_discharges}</b> out{u.discharge_ready ? ` (${u.discharge_ready} ready)` : ""}</span>
          <span title="Expected admissions in 24 h (ED boarders, ED admission probabilities, elective cases)"><b className="tabular text-ct-text">{u.expected_admissions}</b> in{u.ed_boarders ? ` (${u.ed_boarders} ED)` : ""}</span>
          <span title="Net gap: expected admissions − expected discharges − free beds" className={u.net_gap > 0 ? "text-ct-critical" : ""}>
            gap <b className="tabular">{u.net_gap > 0 ? `+${u.net_gap}` : u.net_gap}</b>{u.alc ? ` · ${u.alc} ALC` : ""}
          </span>
        </div>
      </button>
    </li>
  );
}
