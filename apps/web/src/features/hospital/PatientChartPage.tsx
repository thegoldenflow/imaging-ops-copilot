import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, FileSignature, Lock, ShieldAlert } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Badge, Button, Card, EmptyState, ErrorState, Loading, PageHeader } from "../../components/ui";
import { ApiError, api, post } from "../../lib/api";
import { dateTime, time } from "../../lib/format";
import { BreakGlassDialog } from "./BreakGlass";
import {
  CONSENT_FALLBACK,
  CONSENT_LABEL,
  TIER_LABEL,
  type Chart,
  type ChartDocument,
  type ConsentCategory,
  type ConsentState,
} from "./api";

const CONSENT_TONE: Record<ConsentState, "green" | "red" | "amber"> = { permit: "green", deny: "red", missing: "amber" };

/** One patient as the signed-in role may see them; sections the role cannot read are not sent. */
export function PatientChartPage() {
  const { mrn = "" } = useParams();
  const queryClient = useQueryClient();
  const [asking, setAsking] = useState(false);
  const chart = useQuery({
    queryKey: ["chart", mrn],
    queryFn: () => api<Chart>(`/api/hospital/patients/${encodeURIComponent(mrn)}`),
    retry: false,
  });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["chart", mrn] });
  const restricted = chart.error instanceof ApiError && chart.error.code === "break_glass_required";

  return (
    <div>
      <Link to="/hospital/patients" className="mb-3 inline-flex items-center gap-1 text-sm text-slate-500 hover:text-slate-800">
        <ArrowLeft className="size-4" /> Patients
      </Link>
      {chart.isLoading && <Loading />}
      {restricted && (
        <Card>
          <div className="flex flex-col items-center gap-3 py-8 text-center" data-testid="restricted">
            <div className="grid size-11 place-items-center rounded-full bg-rose-50 text-rose-600"><Lock className="size-5" /></div>
            <div>
              <p className="text-base font-semibold text-slate-900">MRN {mrn} is outside your units</p>
              <p className="mt-1 max-w-md text-sm text-slate-600">
                In an emergency you can open this record for 4 hours. You will be asked for a reason; the access is audited and reviewed.
              </p>
            </div>
            <Button variant="danger" onClick={() => setAsking(true)}>
              <ShieldAlert className="size-4" /> Emergency access (break-glass)
            </Button>
          </div>
        </Card>
      )}
      {chart.error && !restricted && <ErrorState error={chart.error} />}
      {asking && <BreakGlassDialog mrn={mrn} onClose={() => setAsking(false)} onGranted={() => { setAsking(false); refresh(); }} />}
      {chart.data && <ChartView chart={chart.data} onChange={refresh} />}
    </div>
  );
}

function ChartView({ chart, onChange }: { chart: Chart; onChange: () => void }) {
  const p = chart.patient;
  return (
    <>
      <PageHeader
        title={p.name ?? `MRN ${p.mrn}`}
        subtitle={
          <span data-testid="chart-header">
            MRN {p.mrn}
            {p.name_other ? ` · ${p.name_other}` : ""}
            {p.gender ? ` · ${p.gender}` : ""}
            {p.birth_date ? ` · born ${p.birth_date}` : ""}
            {p.language ? ` · ${p.language}` : ""}
          </span>
        }
        actions={chart.break_glass && <Badge tone="red"><ShieldAlert className="size-3.5" /> Break-glass access until {time(chart.break_glass.expires_at)}</Badge>}
      />
      {!p.name && <p className="mb-4 text-sm text-slate-500">Your role sees individual patients as MRN and bed only, without clinical content.</p>}
      <div className="grid gap-4 lg:grid-cols-2">
        {chart.encounter && (
          <Card title="Current or latest visit">
            <dl className="grid grid-cols-2 gap-2 text-sm">
              <dt className="text-slate-500">Visit</dt><dd>{chart.encounter.class} · {chart.encounter.status}</dd>
              <dt className="text-slate-500">Location</dt><dd>{chart.encounter.location ?? "–"}</dd>
              <dt className="text-slate-500">Since</dt><dd>{chart.encounter.start ? dateTime(chart.encounter.start) : "–"}</dd>
              {chart.encounter.reason && (<><dt className="text-slate-500">Reason</dt><dd>{chart.encounter.reason}</dd></>)}
            </dl>
          </Card>
        )}
        {chart.consents && <ConsentCard mrn={p.mrn} consents={chart.consents} canChange={Boolean(chart.can_change_consent)} onChange={onChange} />}
        {chart.medications && (
          <Card title="Medication orders">
            {chart.medications.length === 0 ? <EmptyState title="No medication orders" /> : (
              <ul className="space-y-1 text-sm">
                {chart.medications.map((m) => (
                  <li key={m.id} className="flex justify-between gap-2"><span>{m.name}</span><Badge>{m.status}</Badge></li>
                ))}
              </ul>
            )}
          </Card>
        )}
        {chart.observations && (
          <Card title="Latest observations">
            {chart.observations.length === 0 ? <EmptyState title="No observations" /> : (
              <ul className="space-y-1 text-sm">
                {chart.observations.map((o) => (
                  <li key={o.id}>
                    <span className="font-medium">{o.label}</span>: {o.value}
                    {o.at && <span className="ml-1 text-xs text-slate-500">{dateTime(o.at)}</span>}
                  </li>
                ))}
              </ul>
            )}
          </Card>
        )}
      </div>
      {chart.documents && (
        <Card title="Documents" className="mt-4">
          {chart.documents.length === 0 ? <EmptyState title="No documents for this visit" /> : (
            <div className="space-y-3">{chart.documents.map((d) => <DocumentRow key={d.id} doc={d} onSigned={onChange} />)}</div>
          )}
        </Card>
      )}
    </>
  );
}

function ConsentCard({ mrn, consents, canChange, onChange }: { mrn: string; consents: Record<ConsentCategory, ConsentState>; canChange: boolean; onChange: () => void }) {
  const change = useMutation({
    mutationFn: (v: { category: ConsentCategory; decision: "permit" | "deny" }) => post(`/api/hospital/patients/${mrn}/consents`, v),
    onSuccess: onChange,
  });
  return (
    <Card title="Consent">
      <ul className="space-y-3 text-sm">
        {(Object.keys(CONSENT_LABEL) as ConsentCategory[]).map((c) => (
          <li key={c} data-testid={`consent-${c}`}>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="font-medium">{CONSENT_LABEL[c]}</span>
              <div className="flex items-center gap-2">
                <Badge tone={CONSENT_TONE[consents[c]]}>{consents[c] === "missing" ? "not on file" : consents[c] === "permit" ? "given" : "declined"}</Badge>
                {canChange && consents[c] !== "permit" && (
                  <Button size="sm" onClick={() => change.mutate({ category: c, decision: "permit" })} loading={change.isPending}>Record consent</Button>
                )}
                {canChange && consents[c] === "permit" && (
                  <Button size="sm" variant="ghost" onClick={() => change.mutate({ category: c, decision: "deny" })} loading={change.isPending}>Record withdrawal</Button>
                )}
              </div>
            </div>
            {consents[c] !== "permit" && <p className="mt-0.5 text-xs text-slate-500">{CONSENT_FALLBACK[c]}</p>}
          </li>
        ))}
      </ul>
      {change.error && <p className="mt-2 text-sm text-rose-600" role="alert">{change.error.message}</p>}
    </Card>
  );
}

function DocumentRow({ doc, onSigned }: { doc: ChartDocument; onSigned: () => void }) {
  const [open, setOpen] = useState(false);
  const sign = useMutation({
    mutationFn: () => post<{ doc_status: string; missing_roles: string[] }>(`/api/hospital/documents/${doc.id}/sign`),
    onSuccess: onSigned,
  });
  const signers = doc.required_signoff_role.join(doc.cosign ? " and " : " or ");
  return (
    <div className="rounded-lg border border-slate-200 p-3" data-testid={`doc-${doc.id}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-semibold text-slate-900">{doc.type}</span>
          <Badge tone={doc.doc_status === "final" ? "green" : "amber"}>{doc.doc_status === "final" ? "Final" : "Draft"}</Badge>
          {doc.tier && <Badge tone="blue">{TIER_LABEL[doc.tier]}</Badge>}
          <span className="text-xs text-slate-500">Signed by {signers || "nobody"} (registry: {doc.module})</span>
        </div>
        <div className="flex items-center gap-2">
          {doc.text && <Button size="sm" variant="ghost" onClick={() => setOpen((v) => !v)}>{open ? "Hide" : "Show"} text</Button>}
          {doc.can_sign && (
            <Button size="sm" variant="primary" onClick={() => sign.mutate()} loading={sign.isPending}>
              <FileSignature className="size-4" /> {doc.cosign ? "Co-sign" : "Sign"}
            </Button>
          )}
        </div>
      </div>
      {doc.description && <p className="mt-1 text-xs text-slate-500">{doc.description}</p>}
      {doc.signatures.length > 0 && (
        <p className="mt-1 text-xs text-slate-600">
          {doc.signatures.map((s) => `${s.role}: ${s.name ?? s.by}${s.at ? ` (${dateTime(s.at)})` : ""}`).join(" · ")}
        </p>
      )}
      {open && doc.text && <pre className="mt-2 whitespace-pre-wrap rounded bg-slate-50 p-2 text-xs text-slate-700">{doc.text}</pre>}
      {sign.error && <p className="mt-1 text-sm text-rose-600" role="alert">{sign.error.message}</p>}
    </div>
  );
}
