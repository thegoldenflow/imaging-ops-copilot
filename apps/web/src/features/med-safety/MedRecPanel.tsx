// The right column: the reconciliation's discrepancy table — home medication | order | discrepancy type | action.
// Every discrepancy shows its source (the rule, or the AI with its confidence and evidence) and needs an action
// (accept, modify, reject) with a reason; the sign button is enabled only when every item is handled. The
// pharmacist signs, a physician co-signs (the signing service; the record becomes final). A discharge
// reconciliation also shows the discharge medication table (new / changed / stopped / continued).

import { useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { CheckCircle2, ClipboardCheck, FileSignature, Hospital, Loader2, PenLine, Sparkles } from "lucide-react";
import { useState } from "react";
import { AiBadge, Badge, Button, Card, ErrorState, Loading } from "../../components/ui";
import { ApiError } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import {
  acceptHospitalUse, handleItem, hhmm, signSession, TYPE_LABEL, useSession, type DischargeTable, type MedLine, type RecItem,
  type SessionDetail,
} from "./api";
import { SeverityPill, SourceChip, ViewEvidence } from "./kit";

function Line({ m, empty = "–" }: { m: MedLine | null; empty?: string }) {
  if (!m) return <span className="text-slate-400">{empty}</span>;
  return (
    <span>
      <span className="font-medium text-slate-900">{m.name}</span>
      <span className="block text-[11px] text-slate-500">{m.text}</span>
    </span>
  );
}

function ItemAction({ s, item }: { s: SessionDetail; item: RecItem }) {
  const queryClient = useQueryClient();
  const [action, setAction] = useState<"accept" | "modify" | "reject">(item.action ?? "accept");
  const [reason, setReason] = useState(item.reason ?? "");
  const [change, setChange] = useState(item.change ?? "");
  const [editing, setEditing] = useState(!item.action);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!item.discrepancy) return <span className="text-[11px] text-slate-400">no action needed</span>;
  if (!editing || !s.can_handle) {
    return item.action ? (
      <div className="text-[11px]" data-testid="item-handled">
        <span className="inline-flex items-center gap-1 font-medium text-emerald-700"><CheckCircle2 className="size-3" />{item.action}</span>
        <span className="block text-slate-600">{item.reason}</span>
        {item.change && <span className="block text-slate-800">Change: {item.change}</span>}
        <span className="block text-slate-400">{item.by_name} {hhmm(item.at)}</span>
        {s.can_handle && <button className="text-brand-700 hover:underline" onClick={() => setEditing(true)}>Change</button>}
      </div>
    ) : <span className="text-[11px] text-amber-700">waiting for the pharmacist</span>;
  }
  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      await handleItem(s.id, item.id, action, reason, action === "modify" ? change : undefined);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["ms-session", s.id] }),
        queryClient.invalidateQueries({ queryKey: ["ms-queue"] }),
      ]);
      setEditing(false);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not save");
    } finally {
      setBusy(false);
    }
  };
  const ready = reason.trim().length >= 5 && (action !== "modify" || change.trim().length >= 5);
  return (
    <div className="space-y-1" data-testid="item-form">
      <select
        value={action}
        onChange={(e) => setAction(e.target.value as typeof action)}
        className="w-full rounded-md border border-slate-300 px-1.5 py-1 text-[11px]"
        aria-label="Action"
        data-testid="item-action"
      >
        <option value="accept">Accept (intended)</option>
        <option value="modify">Modify (the list must change)</option>
        <option value="reject">Reject (not a discrepancy)</option>
      </select>
      {action === "modify" && (
        <input value={change} onChange={(e) => setChange(e.target.value)} placeholder="What should change"
          className="w-full rounded-md border border-slate-300 px-1.5 py-1 text-[11px]" data-testid="item-change" aria-label="Change" />
      )}
      <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Reason (required)"
        className="w-full rounded-md border border-slate-300 px-1.5 py-1 text-[11px]" data-testid="item-reason" aria-label="Reason" />
      {error && <p className="text-[11px] text-rose-700" role="alert">{error}</p>}
      <Button size="sm" className="h-7 w-full" onClick={save} disabled={!ready} loading={busy} data-testid="item-save">Save</Button>
    </div>
  );
}

function ItemRow({ s, item }: { s: SessionDetail; item: RecItem }) {
  const order = item.order ?? item.inpatient;
  const flag = order ? s.order_flags[order.ref] : undefined;
  return (
    <tr className={clsx("align-top", item.discrepancy && !item.action && "bg-amber-50/40")} data-testid={`medrec-row-${item.id}`}>
      <td className="px-2 py-2 text-xs"><Line m={item.home} empty="not at home" /></td>
      <td className="px-2 py-2 text-xs">
        {item.type === "duplicate_therapy" ? (
          <span className="font-medium text-slate-900">{[item.order, ...item.others].filter(Boolean).map((m) => m!.name).join(" + ")}</span>
        ) : <Line m={item.order} empty={item.inpatient ? `in hospital: ${item.inpatient.name}` : "not ordered"} />}
        {flag && flag.severity !== "none" && (
          <span className="mt-1 block"><SeverityPill severity={flag.blocking ? "blocking" : flag.severity} /> <span className="text-[11px] text-slate-500">{flag.titles[0]}</span></span>
        )}
      </td>
      <td className="px-2 py-2 text-xs">
        <div className="flex flex-wrap items-center gap-1">
          {item.discrepancy ? <SeverityPill severity={item.severity} /> : null}
          <span className={clsx("font-medium", item.discrepancy ? "text-slate-900" : "text-slate-500")}>{TYPE_LABEL[item.type]}</span>
          {item.indication === "pending" && <span className="inline-flex items-center gap-1 text-[11px] text-ai-700"><Loader2 className="size-3 animate-spin" />AI checking the indication</span>}
          {item.hospital_use && <Badge><Hospital className="size-3" />hospital use</Badge>}
        </div>
        {(item.discrepancy || item.source === "ai" || item.indication === "rule") && (
          <div className="mt-1 flex flex-wrap items-center gap-1">
            <SourceChip source={item.source === "ai" ? "ai" : ruleName(item)} confidence={item.confidence} />
            <ViewEvidence refs={item.evidence_refs} />
          </div>
        )}
        <p className="mt-1 text-[11px] text-slate-600">{item.explanation}</p>
        {item.ai_note && <p className="mt-0.5 text-[11px] text-ai-700"><Sparkles className="mr-0.5 inline size-3" />{item.ai_note}</p>}
      </td>
      <td className="px-2 py-2"><ItemAction key={`${item.id}:${item.action}`} s={s} item={item} /></td>
    </tr>
  );
}

function ruleName(item: RecItem): string {
  if (item.type === "new" || item.type === "new_without_indication") return item.indication === "rule" ? "indication on the order" : "indication (AI unavailable)";
  return TYPE_LABEL[item.type].toLowerCase();
}

function DischargeTableView({ table, final }: { table: DischargeTable; final: boolean }) {
  return (
    <div className="mt-3" data-testid="discharge-table">
      <p className="mb-1 flex items-center gap-1.5 text-xs font-semibold text-slate-800">
        <ClipboardCheck className="size-3.5" />Discharge medication table {final ? "" : "(preview until signed)"}
      </p>
      <div className="grid gap-2 sm:grid-cols-2">
        {(["new", "changed", "stopped", "continued"] as const).map((c) => (
          <div key={c} className="rounded-lg border border-slate-200 p-2" data-testid={`discharge-${c}`}>
            <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">{c} ({table[c].length})</p>
            <ul className="mt-1 space-y-0.5">
              {table[c].map((m) => (
                <li key={m.rxcui} className="text-[11px] text-slate-800">
                  <span className="font-medium">{m.name}</span> {m.dose_text ?? ""}
                  {c === "changed" && m.previous_text && <span className="text-slate-500"> (was {m.previous_text})</span>}
                  {m.change_note && <span className="block text-ai-700">pharmacist: {m.change_note}</span>}
                </li>
              ))}
              {table[c].length === 0 && <li className="text-[11px] text-slate-400">none</li>}
            </ul>
          </div>
        ))}
      </div>
    </div>
  );
}

function SignBar({ s }: { s: SessionDetail }) {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const physician = user?.role === "physician";
  const left = s.items.filter((i) => i.discrepancy && !i.action).length;
  const sign = async () => {
    setBusy(true);
    setError(null);
    try {
      await signSession(s.id);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["ms-session", s.id] }),
        queryClient.invalidateQueries({ queryKey: ["ms-queue"] }),
      ]);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not sign");
    } finally {
      setBusy(false);
    }
  };
  const status = s.document?.doc_status;
  return (
    <div className="flex flex-wrap items-center gap-2 border-t border-slate-100 px-3 py-2.5">
      <div className="min-w-0 flex-1 text-[11px] text-slate-600" data-testid="medrec-status">
        {s.status === "open" && (left ? `${left} discrepancy${left === 1 ? "" : "ies"} still to handle` : "Every item is handled: ready to sign")}
        {s.status === "pharmacist_signed" && "Signed by the pharmacist; a physician's co-signature makes the record final"}
        {s.status === "final" && "Signed and co-signed: the record is final"}
        {s.document && (
          <span className="block" data-testid="doc-status">
            Record DocumentReference/{s.document.id}: <span className="font-semibold">{status}</span>
            {s.document.signatures.length > 0 && ` · ${s.document.signatures.map((x) => `${x.role} ${x.name}`).join(", ")}`}
            {s.document.authenticator && ` · authenticated by ${s.document.authenticator}`}
          </span>
        )}
        {error && <span className="block text-rose-700" role="alert">{error}</span>}
      </div>
      {s.status !== "final" && (
        <Button size="sm" variant="primary" onClick={sign} disabled={!s.can_sign} loading={busy} data-testid="sign-button"
          title={s.can_sign ? undefined : s.status === "open" ? "Every discrepancy needs an action and a reason first" : "Waiting for the other signature"}>
          <FileSignature className="size-3.5" />{physician ? "Co-sign as physician" : "Sign as pharmacist"}
        </Button>
      )}
    </div>
  );
}

export function MedRecPanel({ id }: { id: string }) {
  const session = useSession(id);
  const queryClient = useQueryClient();
  const [bulkBusy, setBulkBusy] = useState(false);
  if (session.isLoading) return <Loading label="Comparing the medication lists…" />;
  if (session.error) return <ErrorState error={session.error} onRetry={() => session.refetch()} />;
  const s = session.data!;
  const hospitalUse = s.items.filter((i) => i.hospital_use && i.discrepancy && !i.action);
  const discrepancies = s.items.filter((i) => i.discrepancy).length;
  const rows = [...s.items].sort((a, b) => Number(b.discrepancy) - Number(a.discrepancy));
  return (
    <Card
      padded={false}
      title={<span className="flex items-center gap-2"><PenLine className="size-4 text-brand-600" />{s.kind === "admission" ? "Admission" : "Discharge"} reconciliation {s.id}</span>}
      actions={<span className="flex items-center gap-1.5">{s.items.some((i) => i.source === "ai") && <AiBadge agent="medication_reconciliation" label="AI judgements" />}<SeverityPill severity={s.severity} /></span>}
    >
      <div data-testid="medrec-panel">
        <p className="px-3 pt-2 text-[11px] text-slate-500">
          {s.kind === "admission" ? "Home medications (provincial drug history) against the admission orders." : "Discharge prescriptions against the home medications and the inpatient orders."}
          {" "}{discrepancies} discrepanc{discrepancies === 1 ? "y" : "ies"}; {s.unhandled.length} to handle.
          {s.ai_status !== "done" && s.ai_detail?.reason ? ` AI: ${s.ai_detail.reason}.` : ""}
        </p>
        {hospitalUse.length > 1 && s.can_handle && (
          <div className="px-3 pt-2">
            <Button size="sm" disabled={bulkBusy} loading={bulkBusy} data-testid="accept-hospital-use" onClick={async () => {
              setBulkBusy(true);
              try {
                await acceptHospitalUse(s.id, "Hospital-use medication; stops at discharge");
                await queryClient.invalidateQueries({ queryKey: ["ms-session", s.id] });
              } finally {
                setBulkBusy(false);
              }
            }}>
              Accept the {hospitalUse.length} hospital-use stops
            </Button>
          </div>
        )}
        <div className="overflow-x-auto">
          <table className="mt-2 w-full table-fixed break-words text-left" data-testid="medrec-table">
            <colgroup><col className="w-[20%]" /><col className="w-[21%]" /><col className="w-[36%]" /><col className="w-[23%]" /></colgroup>
            <thead className="border-y border-slate-100 bg-slate-50 text-[11px] uppercase tracking-wide text-slate-500">
              <tr><th className="px-2 py-1.5">Home medication</th><th className="px-2 py-1.5">{s.kind === "admission" ? "Order" : "Discharge prescription"}</th><th className="px-2 py-1.5">Discrepancy type</th><th className="px-2 py-1.5">Action</th></tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {rows.map((item) => <ItemRow key={item.id} s={s} item={item} />)}
            </tbody>
          </table>
        </div>
        {s.kind === "discharge" && (s.discharge_table ?? s.discharge_preview) && (
          <div className="px-3 pb-2"><DischargeTableView table={(s.discharge_table ?? s.discharge_preview)!} final={s.status === "final"} /></div>
        )}
        <SignBar s={s} />
      </div>
    </Card>
  );
}
