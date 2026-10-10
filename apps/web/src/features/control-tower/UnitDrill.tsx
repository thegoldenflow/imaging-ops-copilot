// Drill-down (spec 7.1): unit -> bed grid (one cell per bed, colour = status) -> patient card. The card comes from
// FhirGateway as the signed-in user: the bed manager sees MRN, admission date and expected discharge only; a nurse
// or physician of the unit sees the clinical card; outside their units they need break-glass (Patients page).

import { useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { BedDouble, Brush, EyeOff, KeyRound, Lock, Sparkles, UserRound, Workflow, X } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../../lib/api";
import { dayTime, hhmm, pct, usePatientCard, useUnit, type BedCell, type PatientCard } from "./api";
import { ErrorPanel, SkeletonRows, StatusPill } from "./kit";

const STATUS: Record<BedCell["status"], { label: string; cell: string }> = {
  O: { label: "Occupied", cell: "border-ct-accent/60 bg-ct-info-soft text-ct-text" },
  U: { label: "Free", cell: "border-ct-ok/60 bg-ct-ok-soft text-ct-ok" },
  K: { label: "Housekeeping", cell: "border-ct-warning/60 bg-ct-warning-soft text-ct-warning" },
  C: { label: "Closed", cell: "border-ct-border bg-ct-raised text-ct-muted" },
};

export function UnitDrill({ unitId, onClose, live }: { unitId: string | null; onClose: () => void; live: boolean }) {
  const unit = useUnit(unitId, live);
  const [bed, setBed] = useState<BedCell | null>(null);
  useEffect(() => setBed(null), [unitId]);
  useEffect(() => {
    if (!unitId) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [unitId, onClose]);
  if (!unitId) return null;
  const u = unit.data?.unit;
  return (
    <div className="fixed inset-0 z-30 flex items-start justify-center bg-black/50 p-4 pt-12" onClick={onClose}>
      <div role="dialog" aria-label={`Beds of ${u?.name ?? unitId}`} onClick={(e) => e.stopPropagation()}
        className="flex max-h-[85vh] w-full max-w-5xl flex-col overflow-hidden rounded-xl border border-ct-border bg-ct-surface shadow-2xl" data-testid="unit-drill">
        <header className="flex items-center justify-between gap-3 border-b border-ct-border px-4 py-3">
          <div>
            <h2 className="flex items-center gap-2 text-sm font-semibold text-ct-text"><BedDouble className="size-4 text-ct-accent" />{u?.name ?? unitId}</h2>
            {u && <p className="text-[11px] text-ct-muted">{pct(u.occupancy)} occupied · {u.free} free · {u.cleaning} housekeeping · {u.alc} ALC · {u.expected_discharges} expected discharges (24 h)</p>}
          </div>
          <button onClick={onClose} className="rounded-md p-1 text-ct-muted hover:bg-ct-raised hover:text-ct-text" aria-label="Close"><X className="size-4" /></button>
        </header>
        <div className="flex min-h-0 flex-1 flex-col md:flex-row">
          <div className="min-h-0 flex-1 overflow-y-auto p-4">
            <div className="mb-3 flex flex-wrap gap-2 text-[11px] text-ct-muted">
              {Object.entries(STATUS).map(([k, s]) => <span key={k} className={clsx("rounded border px-1.5 py-0.5", s.cell)}>{s.label}</span>)}
              <span className="rounded border border-ct-ok px-1.5 py-0.5">discharge-ready ring</span>
            </div>
            {unit.isLoading && <SkeletonRows rows={4} />}
            {unit.error && <ErrorPanel error={unit.error} onRetry={() => unit.refetch()} />}
            {unit.data && (
              <div className="grid grid-cols-3 gap-2 sm:grid-cols-4 lg:grid-cols-6" data-testid="bed-grid">
                {unit.data.beds.map((b) => (
                  <button key={b.id} onClick={() => b.encounter_id && setBed(b)} disabled={!b.encounter_id}
                    className={clsx("relative rounded-lg border p-2 text-left text-[11px] transition-colors", STATUS[b.status].cell,
                      b.encounter_id && "hover:border-ct-accent", bed?.id === b.id && "ring-2 ring-ct-accent",
                      (b.discharge?.value ?? 0) >= 0.7 && "outline outline-2 outline-offset-1 outline-ct-ok")}
                    data-testid={`bed-${b.id}`} title={STATUS[b.status].label}>
                    <span className="block font-semibold">{b.id.split("-").slice(1).join("-")}</span>
                    {b.encounter_id ? (
                      <>
                        <span className="block truncate text-ct-muted">{b.identified && b.mrn ? b.mrn : "MRN hidden"}</span>
                        <span className="block text-ct-muted">day {b.days_in ?? "–"}</span>
                        {b.discharge && <span className="tabular block font-medium">out {Math.round(b.discharge.value * 100)}%</span>}
                      </>
                    ) : (
                      <span className="block">{b.status === "K" ? <Brush className="inline size-3" /> : null} {STATUS[b.status].label.toLowerCase()}</span>
                    )}
                    {b.alc && <span className="absolute right-1 top-1 rounded bg-ct-warning-soft px-1 text-[9px] font-bold text-ct-warning">ALC</span>}
                  </button>
                ))}
              </div>
            )}
          </div>
          {bed && <PatientPanel bed={bed} onClose={() => setBed(null)} />}
        </div>
      </div>
    </div>
  );
}

const FIELD_LABEL: Record<string, string> = {
  name: "Name", gender: "Sex", age: "Age", reason: "Reason for admission", attending: "Attending", flags: "Flags",
  vitals: "Latest vital signs", open_orders: "Open orders", discharge: "Discharge within 24 h",
};

function PatientPanel({ bed, onClose }: { bed: BedCell; onClose: () => void }) {
  const card = usePatientCard(bed.encounter_id);
  const queryClient = useQueryClient();
  useEffect(() => () => void queryClient.removeQueries({ queryKey: ["ct-patient", bed.encounter_id] }), [bed.encounter_id, queryClient]);
  const restricted = card.error instanceof ApiError && card.error.code === "break_glass_required";
  return (
    <aside className="w-full shrink-0 overflow-y-auto border-t border-ct-border bg-ct-raised p-4 md:w-80 md:border-l md:border-t-0" data-testid="patient-card">
      <div className="mb-2 flex items-center justify-between">
        <h3 className="flex items-center gap-1.5 text-sm font-semibold text-ct-text"><UserRound className="size-4" />Bed {bed.id}</h3>
        <button onClick={onClose} className="rounded p-1 text-ct-muted hover:text-ct-text" aria-label="Close patient card"><X className="size-3.5" /></button>
      </div>
      {card.isLoading && <SkeletonRows rows={4} />}
      {restricted && (
        <div className="space-y-2 rounded-lg border border-ct-border bg-ct-surface p-3 text-xs text-ct-text" data-testid="card-restricted">
          <p className="flex items-center gap-1.5 font-medium"><Lock className="size-3.5 text-ct-critical" />Outside your units</p>
          <p className="text-ct-muted">This patient is not on one of your units. Emergency access (break-glass) opens the record for 4 hours, with a reason, and is reviewed.</p>
          <Link to="/hospital/patients" className="inline-flex items-center gap-1 text-ct-accent hover:underline"><KeyRound className="size-3" />Patients · break-glass</Link>
        </div>
      )}
      {card.error && !restricted && <ErrorPanel error={card.error} />}
      {card.data && <CardBody c={card.data} />}
    </aside>
  );
}

function CardBody({ c }: { c: PatientCard }) {
  const rows: [string, ReactNode][] = [
    ["MRN", c.mrn ?? "–"],
    ["Bed", c.bed ?? "–"],
    ["Admitted", c.admitted_at ? dayTime(c.admitted_at) : "–"],
    ["Expected discharge", c.expected_discharge ? dayTime(c.expected_discharge) : "–"],
  ];
  if (!c.hidden.length) {
    rows.push(
      ["Name", c.name ?? "–"],
      ["Age / sex", `${c.age ?? "–"} · ${c.gender ?? "–"}`],
      ["Reason for admission", c.reason ?? "–"],
      ["Flags", c.flags?.length ? c.flags.join(", ") : "none"],
      ["Open orders", c.open_orders ?? 0],
    );
    if (c.vitals) {
      const v = c.vitals;
      rows.push(["Vital signs " + hhmm(c.vitals_at ?? null), `HR ${v.heart_rate ?? "–"} · BP ${v.systolic_bp ?? "–"} · RR ${v.resp_rate ?? "–"} · SpO₂ ${v.spo2 ?? "–"}% · ${v.temperature ?? "–"}°C`]);
    }
  }
  return (
    <div className="space-y-3">
      <dl className="space-y-1.5 text-xs" data-testid="card-fields">
        {rows.map(([k, v]) => (
          <div key={k} className="flex justify-between gap-3"><dt className="text-ct-muted">{k}</dt><dd className="text-right font-medium text-ct-text" data-field={k}>{v}</dd></div>
        ))}
      </dl>
      {c.discharge && (
        <p className="rounded-lg bg-ct-surface p-2 text-[11px] text-ct-text" title="Discharge-readiness model (synthetic data)">
          <Sparkles className="mr-1 inline size-3 text-ct-ai" />{c.discharge.sentence}
        </p>
      )}
      {c.hidden.length > 0 && (
        <div className="rounded-lg border border-dashed border-ct-border p-2.5 text-[11px] text-ct-muted" data-testid="card-hidden">
          <p className="mb-1 flex items-center gap-1 font-medium text-ct-text"><EyeOff className="size-3.5" />Hidden for your role</p>
          <p>{c.hidden.map((f) => FIELD_LABEL[f] ?? f).join(" · ")}</p>
          <p className="mt-1">The bed manager sees a patient as MRN, bed, admission and expected discharge only (spec 6.3).</p>
        </div>
      )}
      {!c.hidden.length && c.mrn && (
        <Link to={`/hospital/patients/${c.mrn}`} className="inline-flex text-xs text-ct-accent hover:underline">Open the chart</Link>
      )}
      {c.journey && (
        <Link to={`/workflows/${encodeURIComponent(c.journey.id)}`} className="flex items-center gap-1.5 rounded-lg border border-ct-border bg-ct-surface p-2 text-xs text-ct-text hover:border-ct-accent" data-testid="journey-link">
          <Workflow className="size-3.5 text-ct-accent" />
          <span className="min-w-0 flex-1">
            <span className="block font-medium">Patient journey · {c.journey.done} of {c.journey.total} steps</span>
            <span className="block text-ct-muted">{c.journey.status === "running" ? `${c.journey.current_label ?? "–"} (${(c.journey.current_status ?? "").replace("waiting", "waiting for sign-off")})` : c.journey.status}</span>
          </span>
        </Link>
      )}
      <StatusPill tone="muted" icon={null}>read as {c.role.replace("_", " ")} through the FHIR gateway</StatusPill>
    </div>
  );
}
