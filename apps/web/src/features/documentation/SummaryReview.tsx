// The physician's discharge summary review (spec 7.3): the editable draft on the left, its sources on the right.
// Clicking an item highlights the resources it cites. Items the validator cannot trace to a source are red and the
// Sign button stays disabled until they are fixed or removed (the signing service refuses them too).

import { useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { FileSignature, Link2, PenLine, Plus, RefreshCw, Save, Sparkles, Trash2, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { AiBadge, Badge, Button, Card, ErrorState, Loading } from "../../components/ui";
import { ApiError } from "../../lib/api";
import {
  draftSummary, isInsufficient, issuesAt, saveDocument, signDocument, useDocument,
  type DocView, type EncounterView, type Fact, type Source, type SummaryContent,
} from "./api";
import { IssueList, SourcePanel, ValidationBar } from "./kit";

type ListKey = "hospital_course" | "discharge_diagnoses" | "procedures" | "pending_results" | "follow_up" | "red_flags";
const MED_KEYS = ["new", "changed", "stopped", "continued"] as const;
const SECTIONS: { key: ListKey; title: string; named?: "name" | "test" }[] = [
  { key: "hospital_course", title: "Hospital course" },
  { key: "discharge_diagnoses", title: "Discharge diagnoses", named: "name" },
  { key: "procedures", title: "Procedures", named: "name" },
];
const AFTER: { key: ListKey; title: string; named?: "name" | "test" }[] = [
  { key: "pending_results", title: "Pending results", named: "test" },
  { key: "follow_up", title: "Follow-up" },
  { key: "red_flags", title: "Red flags (return precautions)" },
];

function get(content: SummaryContent, path: string): Fact | undefined {
  const m = path.match(/^(\w+)(?:\.(\w+))?\[(\d+)\]$/);
  if (path === "reason_for_admission") return content.reason_for_admission;
  if (!m) return undefined;
  const [, a, b, i] = m;
  const list = (b ? (content as unknown as Record<string, Record<string, Fact[]>>)[a][b] : (content as unknown as Record<string, Fact[]>)[a]) ?? [];
  return list[Number(i)];
}

export function SummaryPanel({ encounter }: { encounter: EncounterView }) {
  const queryClient = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [missing, setMissing] = useState<string[] | null>(null);
  const doc = encounter.summary;
  async function draft() {
    setBusy(true);
    setError(null);
    try {
      const out = await draftSummary(encounter.encounter_id);
      if (isInsufficient(out)) setMissing(out.missing);
      else setMissing(null);
      await queryClient.invalidateQueries({ queryKey: ["doc-encounter", encounter.encounter_id] });
      await queryClient.invalidateQueries({ queryKey: ["doc-worklist"] });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not draft");
    } finally {
      setBusy(false);
    }
  }
  if (doc) return <SummaryReview id={doc.id} />;
  const template = encounter.ai_mode === "template_only";
  return (
    <Card title="Discharge summary" className="h-full">
      <div className="space-y-3" data-testid="summary-start">
        <p className="text-sm text-slate-600">
          {template
            ? `No AI draft: ${encounter.ai_mode_reason}. Open the template and write it from the record (the sources are listed for you to cite).`
            : "Draft it with AI from this stay's record: diagnoses, procedures, medication orders and the signed discharge medication table, vital signs, abnormal results, reports, open tasks and the care plan. Every item will cite its sources."}
        </p>
        <p className="text-xs text-slate-500">Discharge medication table (Medication safety): {encounter.discharge_table.replace("_", " ")}</p>
        <Button variant={template ? "primary" : "ai"} onClick={draft} loading={busy} data-testid="draft-summary">
          {template ? <PenLine className="size-4" /> : <Sparkles className="size-4" />}
          {template ? "Open the template" : "Draft with AI"}
        </Button>
        {missing && (
          <div className="rounded-lg bg-amber-50 p-3 text-sm text-amber-900 ring-1 ring-inset ring-amber-200" data-testid="insufficient">
            <p className="font-semibold">Insufficient information: nothing was drafted.</p>
            <ul className="mt-1 list-disc pl-5">
              {missing.map((m) => <li key={m}>Missing {m}</li>)}
            </ul>
            {missing.some((m) => m.includes("medication table")) && (
              <p className="mt-2 text-xs">
                The pharmacist reconciles the discharge medications in <Link className="text-brand-700 underline" to="/med-safety">Medication safety</Link>; a physician co-signs.
              </p>
            )}
          </div>
        )}
        {error && <p className="text-sm text-rose-600" role="alert">{error}</p>}
      </div>
    </Card>
  );
}

export function SummaryReview({ id }: { id: string }) {
  const { data: doc, isLoading, error, refetch } = useDocument(id);
  if (isLoading) return <Loading />;
  if (error || !doc) return <ErrorState error={error} onRetry={refetch} />;
  return <Review doc={doc} />;
}

function Review({ doc }: { doc: DocView }) {
  const queryClient = useQueryClient();
  const saved = doc.content as SummaryContent;
  const [content, setContent] = useState<SummaryContent>(saved);
  const [editing, setEditing] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [picked, setPicked] = useState<string[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!editing) setContent(doc.content as SummaryContent);
  }, [doc, editing]);
  const sources = useMemo(() => doc.sources ?? [], [doc.sources]);
  const highlight = selected ? (get(content, selected)?.source_refs ?? []) : picked;

  async function refresh(next?: DocView) {
    if (next) queryClient.setQueryData(["doc", doc.id], next);
    await queryClient.invalidateQueries({ queryKey: ["doc", doc.id] });
    await queryClient.invalidateQueries({ queryKey: ["doc-encounter", doc.encounter_id] });
    await queryClient.invalidateQueries({ queryKey: ["doc-worklist"] });
  }
  async function act(name: string, fn: () => Promise<DocView | unknown>) {
    setBusy(name);
    setError(null);
    try {
      const out = await fn();
      await refresh(out && (out as DocView).kind ? (out as DocView) : undefined);
      return true;
    } catch (e) {
      setError(e instanceof ApiError ? e.message : `Could not ${name}`);
      return false;
    } finally {
      setBusy(null);
    }
  }
  const save = async () => {
    if (await act("save", () => saveDocument(doc.id, content))) setEditing(false);
  };
  const update = (fn: (c: SummaryContent) => void) => {
    const next = structuredClone(content);
    fn(next);
    setContent(next);
  };
  const final = doc.doc_status === "final";
  const sourceByRef = Object.fromEntries(sources.map((s) => [s.ref, s]));

  function addItem(key: ListKey | `medications.${(typeof MED_KEYS)[number]}`) {
    const cited = picked.length ? picked : [];
    const first: Source | undefined = sourceByRef[cited[0]];
    update((c) => {
      const base: Fact = { text: first?.name ?? "", source_refs: cited };
      if (key.startsWith("medications.")) {
        const cat = key.split(".")[1] as (typeof MED_KEYS)[number];
        c.medications[cat].push({ ...base, name: first?.name ?? "", rxcui: first?.code ?? "", dose: "", note: "" });
        return;
      }
      const list = key as ListKey;
      if (list === "discharge_diagnoses") c.discharge_diagnoses.push({ ...base, name: first?.name ?? "", code: first?.code ?? "" });
      else if (list === "procedures") c.procedures.push({ ...base, name: first?.name ?? "", code: first?.code ?? "", date: first?.date ?? "" });
      else if (list === "pending_results") c.pending_results.push({ ...base, test: first?.name ?? "" });
      else if (list === "follow_up") c.follow_up.push({ ...base, code: "", when: "", department: first?.name ?? "", location: "" });
      else if (list === "hospital_course") c.hospital_course.push({ ...base, date: first?.date ?? "" });
      else c.red_flags.push(base);
    });
    setPicked([]);
  }

  // Render helpers (plain functions, not components, so an edited field keeps its focus between renders)
  function itemRow(path: string, fact: Fact, named?: string, onRemove?: () => void) {
    const issues = issuesAt(doc.validation, path);
    const red = issues.some((i) => i.severity === "red");
    const label = named ? String(fact[named] ?? "") : "";
    return (
      <li
        key={path}
        data-testid="summary-item"
        data-path={path}
        data-status={red ? "red" : "ok"}
        onClick={() => setSelected(path === selected ? null : path)}
        className={clsx(
          "cursor-pointer rounded-md border px-2 py-1.5 text-sm",
          red ? "border-rose-300 bg-rose-50" : path === selected ? "border-brand-300 bg-brand-50/60" : "border-slate-200 bg-white hover:border-slate-300",
        )}
      >
        {editing ? (
          <div className="space-y-1" onClick={(e) => e.stopPropagation()}>
            {named && (
              <input
                className="w-full rounded border border-slate-300 px-2 py-1 text-sm"
                value={label}
                aria-label="Name"
                onChange={(e) => update((c) => { const f = get(c, path); if (f) f[named] = e.target.value; })}
              />
            )}
            <textarea
              className="w-full rounded border border-slate-300 px-2 py-1 text-sm"
              rows={path.startsWith("hospital_course") ? 3 : 1}
              value={fact.text}
              aria-label="Text"
              data-testid="item-text"
              onChange={(e) => update((c) => { const f = get(c, path); if (f) f.text = e.target.value; })}
            />
            <div className="flex items-center justify-between text-[11px] text-slate-500">
              <span>Cites: {fact.source_refs.join(", ") || "nothing"}</span>
              {onRemove && (
                <button type="button" className="inline-flex items-center gap-1 text-rose-700 hover:underline" onClick={onRemove} data-testid="remove-item">
                  <Trash2 className="size-3" /> Remove
                </button>
              )}
            </div>
          </div>
        ) : (
          <div className="flex items-start justify-between gap-2">
            <span className="text-slate-800">
              {path.startsWith("hospital_course") && <span className="mr-1 font-mono text-xs text-slate-500">{String(fact.date ?? "")}</span>}
              {fact.text || <em className="text-slate-400">empty</em>}
            </span>
            <span className="inline-flex shrink-0 items-center gap-0.5 text-[11px] text-slate-500" title={fact.source_refs.join(", ")}>
              <Link2 className="size-3" /> {fact.source_refs.length}
            </span>
          </div>
        )}
        <IssueList issues={issues} />
      </li>
    );
  }

  function listSection(k: ListKey, title: string, named?: string) {
    const list = (content[k] as Fact[]) ?? [];
    return (
      <section key={k}>
        <div className="flex items-center justify-between">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">{title}</h3>
          {editing && (
            <button type="button" className="inline-flex items-center gap-1 text-[11px] text-brand-700 hover:underline" onClick={() => addItem(k)} data-testid={`add-${k}`}>
              <Plus className="size-3" /> Add{picked.length ? ` (citing ${picked.length})` : ""}
            </button>
          )}
        </div>
        <ul className="mt-1 space-y-1">
          {list.map((f, i) => itemRow(`${k}[${i}]`, f, named, () => update((c) => { (c[k] as Fact[]).splice(i, 1); })))}
          {!list.length && <li className="text-xs text-slate-400">None recorded.</li>}
        </ul>
      </section>
    );
  }

  return (
    <div className="grid gap-3 xl:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)]" data-testid="summary-review">
      <Card
        title={<span className="flex items-center gap-2">Discharge summary <span className="font-mono text-xs font-normal text-slate-500">{doc.id}</span></span>}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            {doc.mode === "ai" ? <AiBadge label="AI draft" agent="discharge_summary" /> : <Badge tone="slate">Template (no AI)</Badge>}
            <Badge tone={final ? "green" : doc.blocking ? "red" : "amber"}>{final ? "Final" : doc.blocking ? "Blocked" : "Draft"}</Badge>
          </div>
        }
      >
        <div className="space-y-3">
          <ValidationBar validation={doc.validation} />
          {doc.raw_validation && doc.raw_validation.issues.some((i) => i.severity === "red") && !doc.blocking && (
            <p className="text-xs text-slate-500" data-testid="raw-issues">
              The AI draft had {new Set(doc.raw_validation.issues.filter((i) => i.severity === "red").map((i) => i.path)).size} untraceable item(s); they were fixed or removed before signing.
            </p>
          )}
          <section>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Reason for admission</h3>
            <ul className="mt-1">{itemRow("reason_for_admission", content.reason_for_admission)}</ul>
          </section>
          {SECTIONS.map((s) => listSection(s.key, s.title, s.named))}
          <section>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Medications at discharge</h3>
            <div className="mt-1 grid gap-2 md:grid-cols-2">
              {MED_KEYS.map((cat) => (
                <div key={cat} className="rounded-md bg-slate-50 p-2" data-testid={`meds-${cat}`}>
                  <div className="flex items-center justify-between">
                    <span className="text-[11px] font-semibold uppercase text-slate-600">{cat}</span>
                    {editing && (
                      <button type="button" className="inline-flex items-center gap-1 text-[11px] text-brand-700 hover:underline" onClick={() => addItem(`medications.${cat}`)}>
                        <Plus className="size-3" /> Add
                      </button>
                    )}
                  </div>
                  <ul className="mt-1 space-y-1">
                    {content.medications[cat].map((m, i) => itemRow(`medications.${cat}[${i}]`, m, "name",
                      () => update((c) => { c.medications[cat].splice(i, 1); })))}
                    {!content.medications[cat].length && <li className="text-xs text-slate-400">None.</li>}
                  </ul>
                </div>
              ))}
            </div>
          </section>
          {AFTER.map((s) => listSection(s.key, s.title, s.named))}

          <div className="flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3">
            {!final && doc.can_edit && !editing && (
              <Button size="sm" onClick={() => setEditing(true)} data-testid="edit-summary"><PenLine className="size-3.5" /> Edit</Button>
            )}
            {editing && (
              <>
                <Button size="sm" variant="primary" onClick={save} loading={busy === "save"} data-testid="save-summary"><Save className="size-3.5" /> Save and re-check</Button>
                <Button size="sm" variant="ghost" onClick={() => { setEditing(false); setContent(doc.content as SummaryContent); }}><X className="size-3.5" /> Cancel</Button>
              </>
            )}
            {!final && doc.can_edit && doc.mode === "ai" && !editing && (
              <Button size="sm" variant="ghost" onClick={() => act("redraft", () => draftSummary(doc.encounter_id, true))} loading={busy === "redraft"}>
                <RefreshCw className="size-3.5" /> Redraft
              </Button>
            )}
            {!final && (
              <Button
                size="sm" variant="primary" disabled={!doc.can_sign || editing} loading={busy === "sign"}
                onClick={() => act("sign", () => signDocument(doc.id))}
                title={editing ? "Save your changes first" : doc.why_not ?? undefined}
                data-testid="sign-summary"
              >
                <FileSignature className="size-3.5" /> Sign as physician
              </Button>
            )}
            {!final && doc.why_not && <span className="text-xs text-slate-500" data-testid="why-not">{doc.why_not}</span>}
            {final && (
              <span className="text-sm text-emerald-700" data-testid="signed">
                Signed by {doc.signatures.map((s) => s.name ?? s.by).join(", ")}; final (authenticator {doc.authenticator}).
              </span>
            )}
          </div>
          {error && <p className="text-sm text-rose-600" role="alert">{error}</p>}
          {doc.edits.length > 0 && (
            <p className="text-[11px] text-slate-400">Edited {doc.edits.length} time(s); last by {doc.edits[doc.edits.length - 1].name}.</p>
          )}
        </div>
      </Card>
      <Card title="Sources" actions={<span className="text-[11px] text-slate-500">{selected ? `What ${selected} cites` : editing ? "Tick sources to cite in a new item" : "Click an item to see its sources"}</span>}>
        <div className="xl:max-h-[calc(100vh-230px)] xl:overflow-y-auto">
          <SourcePanel sources={sources} highlight={highlight} picked={editing ? picked : undefined}
            onPick={editing ? (ref) => setPicked((p) => (p.includes(ref) ? p.filter((x) => x !== ref) : [...p, ref])) : undefined} />
        </div>
      </Card>
    </div>
  );
}
