import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { X } from "lucide-react";
import { useEffect, useState } from "react";
import { LineChart } from "../../components/charts";
import { Badge, Button, Card, EmptyState, ErrorState, Loading, PageHeader, Stat, Tabs } from "../../components/ui";
import { api, post, put } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { dateTime, pct } from "../../lib/format";
import type { DoseOverview, DoseRecord } from "../../lib/types";

type Tab = "exceptions" | "trends" | "references";
const REVIEWERS = ["technologist", "radiologist", "medical_director"];

function Ratio({ value }: { value: number }) {
  return <Badge tone={value > 1.3 ? "red" : value > 1 ? "amber" : "green"}>{pct(value)} of ref</Badge>;
}

function RecordDrawer({ id, outcomes, onClose }: { id: string; outcomes: Record<string, string>; onClose: () => void }) {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const q = useQuery({ queryKey: ["dose-record", id], queryFn: () => api<DoseRecord>(`/api/dose/records/${id}`) });
  const [outcome, setOutcome] = useState("justified");
  const [note, setNote] = useState("");
  const review = useMutation({
    mutationFn: () => post(`/api/dose/records/${id}/review`, { outcome, note }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["dose"] });
      queryClient.invalidateQueries({ queryKey: ["dose-record", id] });
    },
  });
  const r = q.data;
  return (
    <div className="fixed inset-0 z-20 flex justify-end bg-slate-900/30" onClick={onClose}>
      <aside className="flex h-full w-full max-w-2xl flex-col bg-white shadow-xl" onClick={(e) => e.stopPropagation()} aria-label="Dose record" data-testid="dose-drawer">
        <header className="flex items-start justify-between border-b border-slate-100 px-5 py-4">
          <div>
            <p className="text-sm font-semibold text-slate-900">{r ? `${r.protocol_name} · ${r.patient_name}` : "Dose record"}</p>
            {r && <p className="text-xs text-slate-500">{dateTime(r.performed_at)} · {r.scanner_name} · {r.id}</p>}
          </div>
          <button onClick={onClose} className="rounded-md p-1 text-slate-500 hover:bg-slate-100" aria-label="Close"><X className="size-4" /></button>
        </header>
        <div className="flex-1 space-y-4 overflow-y-auto px-5 py-4">
          {q.isLoading && <Loading />}
          {q.error && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
          {r && (
            <>
              <div className="grid grid-cols-2 gap-3">
                <div className="rounded-lg border border-slate-200 p-3">
                  <p className="text-xs text-slate-500">CTDIvol</p>
                  <p className="tabular text-xl font-semibold">{r.ctdivol_mgy.toFixed(1)} <span className="text-sm font-normal text-slate-500">mGy</span></p>
                  <p className="text-xs text-slate-500">reference {r.reference.ctdivol_mgy} mGy</p>
                </div>
                <div className="rounded-lg border border-slate-200 p-3">
                  <p className="text-xs text-slate-500">DLP (accumulated)</p>
                  <p className="tabular text-xl font-semibold">{r.dlp_total_mgycm.toFixed(0)} <span className="text-sm font-normal text-slate-500">mGy·cm</span></p>
                  <p className="text-xs text-slate-500">reference {r.reference.dlp_mgycm} mGy·cm</p>
                </div>
              </div>
              <div>
                <p className="mb-1 text-xs font-semibold tracking-wide text-slate-500 uppercase">Irradiation events · {r.sr_template}</p>
                <div className="overflow-x-auto rounded-lg border border-slate-200">
                  <table className="w-full text-left text-xs">
                    <thead className="bg-slate-50 text-slate-500">
                      <tr>{["#", "Step", "Acquisition", "kVp", "mAs", "Length", "CTDIvol", "DLP", "Phantom"].map((h) => <th key={h} className="px-2 py-1.5 font-medium">{h}</th>)}</tr>
                    </thead>
                    <tbody className="tabular divide-y divide-slate-100">
                      {r.events!.map((e) => (
                        <tr key={e.sequence}>
                          <td className="px-2 py-1.5">{e.sequence}</td><td className="px-2 py-1.5">{e.protocol_step}</td><td className="px-2 py-1.5">{e.acquisition_type}</td>
                          <td className="px-2 py-1.5">{e.kvp}</td><td className="px-2 py-1.5">{e.exposure_mas}</td><td className="px-2 py-1.5">{e.scanning_length_mm} mm</td>
                          <td className="px-2 py-1.5">{e.ctdivol_mgy} mGy</td><td className="px-2 py-1.5">{e.dlp_mgycm} mGy·cm</td><td className="px-2 py-1.5">{e.phantom}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <p className="mt-1 text-xs text-slate-500">Source: {r.source}. Synthetic values; the structure follows the DICOM CT Radiation Dose SR.</p>
              </div>
              {r.exceedance && (
                r.review ? (
                  <div className="rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-800">Reviewed by {r.review.by} ({dateTime(r.review.at)}): {outcomes[r.review.outcome]}{r.review.note && `. ${r.review.note}`}</div>
                ) : user && REVIEWERS.includes(user.role) ? (
                  <div className="space-y-2 rounded-lg border border-amber-200 p-3">
                    <p className="text-sm font-medium text-slate-800">Above reference level ({r.exceedance.metrics.join(" and ")}): record the review</p>
                    <select value={outcome} onChange={(e) => setOutcome(e.target.value)} className="h-9 w-full rounded-lg border border-slate-300 px-2 text-sm" aria-label="Review outcome">
                      {Object.entries(outcomes).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                    </select>
                    <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2} placeholder="Note" className="w-full rounded-lg border border-slate-300 p-2 text-sm" aria-label="Review note" />
                    <div className="flex justify-end"><Button size="sm" variant="primary" loading={review.isPending} onClick={() => review.mutate()} data-testid="save-dose-review">Save review</Button></div>
                    {review.error && <p className="text-sm text-rose-600" role="alert">{(review.error as Error).message}</p>}
                  </div>
                ) : <p className="text-sm text-amber-700">Above reference level; waiting for a technologist or radiologist review.</p>
              )}
            </>
          )}
        </div>
      </aside>
    </div>
  );
}

function Exceptions({ data, onOpen }: { data: DoseOverview; onOpen: (id: string) => void }) {
  const [openOnly, setOpenOnly] = useState(true);
  const rows = data.exceptions.filter((r) => !openOnly || !r.review);
  return (
    <Card padded={false} title={`Exams above reference level (${rows.length})`}
      actions={<label className="flex items-center gap-1.5 text-xs text-slate-600"><input type="checkbox" checked={openOnly} onChange={(e) => setOpenOnly(e.target.checked)} /> Not yet reviewed</label>}>
      {rows.length === 0 ? <EmptyState title="Nothing to review" hint="Every exam above its reference level has been reviewed." /> : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-50 text-xs text-slate-500">
              <tr>{["Performed", "Patient", "Protocol", "Scanner", "CTDIvol", "DLP", "Review"].map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}</tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {rows.map((r) => (
                <tr key={r.id} className="cursor-pointer hover:bg-slate-50" onClick={() => onOpen(r.id)} data-testid={`dose-${r.id}`}>
                  <td className="tabular px-3 py-2 text-xs whitespace-nowrap text-slate-600">{dateTime(r.performed_at)}</td>
                  <td className="px-3 py-2 font-medium text-slate-800">{r.patient_name}</td>
                  <td className="px-3 py-2 text-xs">{r.protocol_name}</td>
                  <td className="px-3 py-2 text-xs text-slate-600">{r.scanner_id}</td>
                  <td className="tabular px-3 py-2 whitespace-nowrap">{r.ctdivol_mgy.toFixed(1)} mGy <Ratio value={r.exceedance!.ctdivol_ratio} /></td>
                  <td className="tabular px-3 py-2 whitespace-nowrap">{r.dlp_total_mgycm.toFixed(0)} <Ratio value={r.exceedance!.dlp_ratio} /></td>
                  <td className="px-3 py-2">{r.review ? <Badge tone="green">Reviewed</Badge> : <Badge tone="amber">Open</Badge>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function Trends({ data }: { data: DoseOverview }) {
  const fmt = (v: number) => `${v.toFixed(0)}%`;
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <Card title="Median CTDIvol by scanner, % of protocol reference (weekly)" className="lg:col-span-2">
        <LineChart label="Weekly median CTDIvol by scanner as a percentage of the reference level" labels={data.trend_by_scanner.labels}
          series={data.trend_by_scanner.series} reference={100} referenceLabel="Reference level" format={fmt} />
      </Card>
      <Card title="Scanners" padded={false} className="lg:row-span-2">
        <ul className="divide-y divide-slate-100">
          {data.scanners.map((s) => (
            <li key={s.scanner_id} className="px-4 py-2.5 text-sm">
              <div className="flex justify-between gap-2"><span className="font-medium text-slate-800">{s.scanner_id}</span>
                <span className={clsx("tabular text-xs", (s.recent_exceed_rate ?? 0) > 0.08 ? "font-semibold text-rose-600" : "text-slate-500")}>
                  {s.recent_exceed_rate == null ? "–" : pct(s.recent_exceed_rate, 1)} above ref (14 d)
                </span>
              </div>
              <p className="text-xs text-slate-500">{s.site} · {s.records} exams · {s.exceeding} above reference</p>
            </li>
          ))}
        </ul>
      </Card>
      <Card title="Median CTDIvol by protocol, % of reference (weekly)" className="lg:col-span-2">
        <LineChart label="Weekly median CTDIvol by protocol as a percentage of the reference level" labels={data.trend_by_protocol.labels}
          series={data.trend_by_protocol.series} reference={100} referenceLabel="Reference level" format={fmt} />
      </Card>
    </div>
  );
}

function References({ data }: { data: DoseOverview }) {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const canEdit = !!user && ["medical_director", "admin"].includes(user.role);
  const [draft, setDraft] = useState<Record<string, { ctdivol_mgy: number; dlp_mgycm: number }>>({});
  const key = JSON.stringify(data.protocols.map((p) => p.reference));
  useEffect(() => setDraft(Object.fromEntries(data.protocols.map((p) => [p.protocol_id, p.reference]))), [key]); // eslint-disable-line react-hooks/exhaustive-deps
  const save = useMutation({
    mutationFn: (pid: string) => put(`/api/dose/references/${pid}`, draft[pid]),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["dose"] }),
  });
  return (
    <Card title="Reference levels by protocol" padded={false}>
      <div className="mx-4 mt-3 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">Demo placeholder values. The medical director sets the clinic's reference levels; changes re-evaluate every record immediately.</div>
      <div className="overflow-x-auto">
        <table className="mt-2 w-full text-left text-sm">
          <thead className="bg-slate-50 text-xs text-slate-500">
            <tr>{["Protocol", "Exams", "Median CTDIvol", "Ref CTDIvol (mGy)", "Median DLP", "Ref DLP (mGy·cm)", "Above ref", ""].map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}</tr>
          </thead>
          <tbody className="tabular divide-y divide-slate-100">
            {data.protocols.map((p) => {
              const d = draft[p.protocol_id] ?? p.reference;
              const dirty = d.ctdivol_mgy !== p.reference.ctdivol_mgy || d.dlp_mgycm !== p.reference.dlp_mgycm;
              return (
                <tr key={p.protocol_id} data-testid={`ref-${p.protocol_id}`}>
                  <td className="px-3 py-2 font-medium text-slate-800">{p.name}</td>
                  <td className="px-3 py-2">{p.records}</td>
                  <td className="px-3 py-2">{p.median_ctdivol ?? "–"}</td>
                  <td className="px-3 py-2"><input type="number" min={0.5} step={0.5} value={d.ctdivol_mgy} disabled={!canEdit} aria-label={`${p.name} reference CTDIvol`}
                    onChange={(e) => setDraft({ ...draft, [p.protocol_id]: { ...d, ctdivol_mgy: Number(e.target.value) } })} className="h-8 w-20 rounded-lg border border-slate-300 px-2 text-right" /></td>
                  <td className="px-3 py-2">{p.median_dlp ?? "–"}</td>
                  <td className="px-3 py-2"><input type="number" min={10} step={10} value={d.dlp_mgycm} disabled={!canEdit} aria-label={`${p.name} reference DLP`}
                    onChange={(e) => setDraft({ ...draft, [p.protocol_id]: { ...d, dlp_mgycm: Number(e.target.value) } })} className="h-8 w-24 rounded-lg border border-slate-300 px-2 text-right" /></td>
                  <td className={clsx("px-3 py-2", p.exceeding && "text-rose-600")}>{p.exceeding}</td>
                  <td className="px-3 py-2 text-right">{canEdit && dirty && <Button size="sm" variant="primary" loading={save.isPending && save.variables === p.protocol_id} onClick={() => save.mutate(p.protocol_id)}>Save</Button>}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {save.error && <p className="px-4 py-2 text-sm text-rose-600" role="alert">{(save.error as Error).message}</p>}
    </Card>
  );
}

export function DosePage() {
  const [tab, setTab] = useState<Tab>("exceptions");
  const [open, setOpen] = useState<string | null>(null);
  const q = useQuery({ queryKey: ["dose"], queryFn: () => api<DoseOverview>("/api/dose/overview"), refetchInterval: 10_000 });
  const d = q.data;
  const worst = d?.scanners.reduce((a, b) => ((b.recent_exceed_rate ?? 0) > (a.recent_exceed_rate ?? 0) ? b : a), d.scanners[0]);
  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader title="CT dose monitoring" subtitle="Every CT exam's dose record (CTDIvol, DLP) from the scanner's dose report, checked against reference levels per protocol." />
      {q.isLoading && <Loading />}
      {q.error && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {d && (
        <>
          <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat label="CT exams with a dose record (90 d)" value={<span data-testid="dose-coverage">{d.coverage.with_record} / {d.coverage.ct_exams}</span>}
              tone={d.coverage.missing ? "red" : "green"} hint={d.coverage.missing ? `${d.coverage.missing} missing` : "complete"} />
            <Stat label="Above reference level" value={d.exceeding} hint={pct(d.exceeding / Math.max(1, d.records), 1)} />
            <Stat label="Waiting for review" value={d.open_exceptions} tone={d.open_exceptions ? "amber" : "green"} />
            <Stat label="Highest recent rate" value={worst?.scanner_id ?? "–"} hint={worst?.recent_exceed_rate != null ? `${pct(worst.recent_exceed_rate, 1)} of exams above reference, last 14 days` : undefined}
              tone={(worst?.recent_exceed_rate ?? 0) > 0.08 ? "red" : undefined} />
          </div>
          <Tabs value={tab} onChange={setTab} tabs={[{ id: "exceptions", label: "Exceptions" }, { id: "trends", label: "Trends" }, { id: "references", label: "Reference levels" }]} />
          {tab === "exceptions" && <Exceptions data={d} onOpen={setOpen} />}
          {tab === "trends" && <Trends data={d} />}
          {tab === "references" && <References data={d} />}
          {open && <RecordDrawer id={open} outcomes={d.review_outcomes} onClose={() => setOpen(null)} />}
        </>
      )}
    </div>
  );
}
