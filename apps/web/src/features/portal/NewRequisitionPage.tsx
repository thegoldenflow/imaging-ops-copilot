import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2 } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { Button, Card, ErrorState, Loading, PageHeader } from "../../components/ui";
import { api, post } from "../../lib/api";
import type { PortalPatientRow, PortalRequisition } from "../../lib/types";

const input = "h-10 w-full rounded-lg border border-slate-300 px-3 text-sm";
const area = "w-full rounded-lg border border-slate-300 p-3 text-sm";

function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="block text-sm">
      <span className="font-medium text-slate-700">{label}</span>
      <div className="mt-1">{children}</div>
      {hint && <span className="text-xs text-slate-500">{hint}</span>}
    </label>
  );
}

export function NewRequisitionPage() {
  const queryClient = useQueryClient();
  const patients = useQuery({ queryKey: ["portal-patients"], queryFn: () => api<{ patients: PortalPatientRow[] }>("/api/portal/patients") });
  const options = useQuery({ queryKey: ["portal-options"], queryFn: () => api<{ exams: string[]; languages: Record<string, string> }>("/api/portal/form-options") });
  const [mode, setMode] = useState<"existing" | "new">("existing");
  const [patientId, setPatientId] = useState("");
  const [np, setNp] = useState({ given_name: "", family_name: "", dob: "", sex: "F", phone: "", health_card: "", health_card_version: "", preferred_language: "en" });
  const [form, setForm] = useState({ exam_requested: "", clinical_information: "", relevant_history: "", allergies: "", previous_imaging: "", urgent: false, notes: "" });
  const submit = useMutation({
    mutationFn: () => post<PortalRequisition>("/api/portal/requisitions", {
      ...form, ...(mode === "existing" ? { patient_id: patientId } : { new_patient: np }),
    }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["portal-reqs"] }),
  });
  const valid = form.exam_requested.trim().length >= 3 && form.clinical_information.trim().length >= 3
    && (mode === "existing" ? !!patientId : np.given_name && np.family_name && np.dob && /^\d{10}$/.test(np.health_card));

  if (submit.data) {
    return (
      <div className="mx-auto max-w-xl">
        <Card>
          <div className="flex flex-col items-center gap-2 py-6 text-center" data-testid="requisition-sent">
            <CheckCircle2 className="size-8 text-emerald-600" />
            <p className="font-semibold text-slate-900">Requisition {submit.data.id} received</p>
            <p className="text-sm text-slate-600">Our team triages it and assigns a protocol; you can follow its status under My patients. Urgent requests are reviewed first.</p>
            <div className="mt-2 flex gap-2">
              <Link to="/portal"><Button variant="primary">My patients</Button></Link>
              <Button onClick={() => submit.reset()}>Submit another</Button>
            </div>
          </div>
        </Card>
      </div>
    );
  }
  return (
    <div className="mx-auto max-w-2xl">
      <PageHeader title="New requisition" subtitle="Structured form plus free text. It enters the same intake pipeline as faxed requisitions." />
      {(patients.isLoading || options.isLoading) && <Loading />}
      {patients.error && <ErrorState error={patients.error} onRetry={() => patients.refetch()} />}
      <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); submit.mutate(); }}>
        <Card title="Patient">
          <div className="mb-3 flex gap-4 text-sm">
            <label className="flex items-center gap-1.5"><input type="radio" checked={mode === "existing"} onChange={() => setMode("existing")} /> One of my patients</label>
            <label className="flex items-center gap-1.5"><input type="radio" checked={mode === "new"} onChange={() => setMode("new")} /> New patient</label>
          </div>
          {mode === "existing" ? (
            <Field label="Patient">
              <select value={patientId} onChange={(e) => setPatientId(e.target.value)} className={input} aria-label="Patient">
                <option value="">Choose…</option>
                {patients.data?.patients.map((p) => <option key={p.id} value={p.id}>{p.name} (born {p.dob})</option>)}
              </select>
            </Field>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="First name"><input className={input} value={np.given_name} onChange={(e) => setNp({ ...np, given_name: e.target.value })} /></Field>
              <Field label="Last name"><input className={input} value={np.family_name} onChange={(e) => setNp({ ...np, family_name: e.target.value })} /></Field>
              <Field label="Date of birth"><input type="date" className={input} value={np.dob} onChange={(e) => setNp({ ...np, dob: e.target.value })} /></Field>
              <Field label="Sex">
                <select className={input} value={np.sex} onChange={(e) => setNp({ ...np, sex: e.target.value })}><option value="F">F</option><option value="M">M</option></select>
              </Field>
              <Field label="Health card number" hint="10 digits (synthetic numbers only)"><input className={input} inputMode="numeric" value={np.health_card} onChange={(e) => setNp({ ...np, health_card: e.target.value.replace(/\D/g, "").slice(0, 10) })} /></Field>
              <Field label="Version code"><input className={input} value={np.health_card_version} onChange={(e) => setNp({ ...np, health_card_version: e.target.value })} /></Field>
              <Field label="Phone"><input className={input} value={np.phone} onChange={(e) => setNp({ ...np, phone: e.target.value })} /></Field>
              <Field label="Preferred language">
                <select className={input} value={np.preferred_language} onChange={(e) => setNp({ ...np, preferred_language: e.target.value })}>
                  {Object.entries(options.data?.languages ?? { en: "English" }).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                </select>
              </Field>
            </div>
          )}
        </Card>
        <Card title="Exam">
          <div className="space-y-3">
            <Field label="Exam requested">
              <input list="exam-choices" className={input} value={form.exam_requested} onChange={(e) => setForm({ ...form, exam_requested: e.target.value })} />
              <datalist id="exam-choices">{options.data?.exams.map((x) => <option key={x} value={x} />)}</datalist>
            </Field>
            <Field label="Clinical information"><textarea rows={3} className={area} value={form.clinical_information} onChange={(e) => setForm({ ...form, clinical_information: e.target.value })} /></Field>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Relevant history" hint="e.g. diabetes, kidney disease, implants"><input className={input} value={form.relevant_history} onChange={(e) => setForm({ ...form, relevant_history: e.target.value })} /></Field>
              <Field label="Allergies"><input className={input} value={form.allergies} onChange={(e) => setForm({ ...form, allergies: e.target.value })} placeholder="NKDA" /></Field>
            </div>
            <Field label="Previous imaging (facility, date)"><input className={input} value={form.previous_imaging} onChange={(e) => setForm({ ...form, previous_imaging: e.target.value })} /></Field>
            <Field label="Anything else (free text)"><textarea rows={2} className={area} value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} /></Field>
            <label className="flex items-center gap-2 text-sm font-medium text-slate-700"><input type="checkbox" checked={form.urgent} onChange={(e) => setForm({ ...form, urgent: e.target.checked })} /> Urgent</label>
          </div>
        </Card>
        {submit.error && <p className="text-sm text-rose-600" role="alert">{(submit.error as Error).message}</p>}
        <div className="flex justify-end"><Button type="submit" variant="primary" disabled={!valid} loading={submit.isPending} data-testid="submit-requisition">Submit requisition</Button></div>
      </form>
    </div>
  );
}
