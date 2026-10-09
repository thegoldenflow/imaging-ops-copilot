import { useQuery } from "@tanstack/react-query";
import { BedDouble, Search } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { Button, Card, EmptyState, ErrorState, Loading, PageHeader } from "../../components/ui";
import { api } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { dateTime } from "../../lib/format";
import type { CensusEntry, UnitsResponse } from "./api";

/** The unit census (FhirGateway, shaped by role on the server) and lookup by MRN. */
export function PatientsPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [mrn, setMrn] = useState("");
  const units = useQuery({ queryKey: ["hospital-units"], queryFn: () => api<UnitsResponse>("/api/hospital/units") });
  const choices = units.data ? (units.data.scope === "unit" ? units.data.units.filter((u) => units.data!.mine.includes(u.id)) : units.data.units) : [];
  const [picked, setPicked] = useState<string | null>(null);
  const unit = picked ?? choices[0]?.id ?? null;
  const census = useQuery({
    queryKey: ["census", unit],
    queryFn: () => api<{ unit: string; patients: CensusEntry[] }>(`/api/hospital/census?unit=${unit}`),
    enabled: Boolean(unit),
  });

  const open = (e: FormEvent) => {
    e.preventDefault();
    if (mrn.trim()) navigate(`/hospital/patients/${encodeURIComponent(mrn.trim())}`);
  };

  return (
    <div>
      <PageHeader
        title="Patients"
        subtitle={
          units.data?.scope === "unit"
            ? `Your units: ${units.data.mine.join(", ") || "none"}. Patients elsewhere need emergency access (break-glass).`
            : "Hospital-wide. What you see of each patient depends on your role."
        }
      />
      <div className="mb-4 flex flex-wrap items-end gap-3">
        <label className="text-sm">
          <span className="mb-1 block text-xs font-medium text-slate-500">Unit</span>
          <select
            value={unit ?? ""}
            onChange={(e) => setPicked(e.target.value)}
            className="h-9 rounded-lg border border-slate-300 bg-white px-2 text-sm"
            data-testid="unit-select"
          >
            {choices.map((u) => (
              <option key={u.id} value={u.id}>{u.name} ({u.id})</option>
            ))}
          </select>
        </label>
        <form onSubmit={open} className="flex items-end gap-2">
          <label className="text-sm">
            <span className="mb-1 block text-xs font-medium text-slate-500">Open a patient by MRN</span>
            <input
              value={mrn}
              onChange={(e) => setMrn(e.target.value)}
              placeholder="8-digit MRN"
              inputMode="numeric"
              className="h-9 w-40 rounded-lg border border-slate-300 px-3 text-sm"
              data-testid="mrn-search"
            />
          </label>
          <Button type="submit" variant="primary"><Search className="size-4" />Open</Button>
        </form>
      </div>
      <Card title={census.data ? `${census.data.patients.length} patients on ${unit}` : "Census"} padded={false}>
        {(units.isLoading || census.isLoading) && <Loading />}
        {units.error && <ErrorState error={units.error} />}
        {census.error && <ErrorState error={census.error} onRetry={() => census.refetch()} />}
        {census.data && census.data.patients.length === 0 && <EmptyState title="No patients on this unit" icon={<BedDouble className="size-8" />} />}
        {census.data && census.data.patients.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm" data-testid="census">
              <thead className="bg-slate-50 text-xs text-slate-500">
                <tr>{["Bed", "MRN", "Patient", "Visit", "Since"].map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}</tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {census.data.patients.map((p) => (
                  <tr
                    key={p.encounter_id}
                    className="cursor-pointer hover:bg-slate-50"
                    onClick={() => p.mrn && navigate(`/hospital/patients/${p.mrn}`)}
                  >
                    <td className="px-3 py-2 font-medium">{p.bed_id ?? "waiting"}</td>
                    <td className="tabular px-3 py-2">{p.mrn}</td>
                    <td className="px-3 py-2">{p.name ?? <span className="text-slate-400">MRN only for your role</span>}</td>
                    <td className="px-3 py-2">{{ EMER: "ED visit", IMP: "Inpatient", AMB: "Day surgery" }[p.encounter_class ?? ""] ?? p.encounter_class}</td>
                    <td className="px-3 py-2 whitespace-nowrap">{p.since ? dateTime(p.since) : "–"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      {user && <p className="mt-3 text-xs text-slate-500">Synthetic hospital data. Every lookup is written to the audit log.</p>}
    </div>
  );
}
