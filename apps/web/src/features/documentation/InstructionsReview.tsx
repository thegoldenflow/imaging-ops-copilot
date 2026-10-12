// The nurse's review of the bilingual patient instructions (spec 7.3). English, Simplified and Traditional Chinese
// are rendered by the server from one structure (the model only picked codes), so switching language changes the
// words, never the content. The nurse can adjust the codes (what a medicine is for, when to take it, red flags,
// daily care), checks the readability panel and signs; then print, SMS (with the patient's consent) or the portal.

import { useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { ExternalLink, FileSignature, MessageSquare, PenLine, Printer, Save, Siren, Sparkles, Phone, X } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { AiBadge, Badge, Button, Card, ErrorState, Loading, Tabs } from "../../components/ui";
import { ApiError } from "../../lib/api";
import {
  LANG_LABEL, deliver, draftInstructions, isInsufficient, signDocument, saveDocument, useDocument,
  type DocView, type EncounterView, type InstructionsContent, type Lang, type Rendering,
} from "./api";
import { ValidationBar } from "./kit";

export function InstructionsPanel({ encounter }: { encounter: EncounterView }) {
  const queryClient = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [missing, setMissing] = useState<string[] | null>(null);
  if (encounter.instructions) return <InstructionsReview id={encounter.instructions.id} />;
  const summaryFinal = encounter.summary?.status === "final";
  async function draft() {
    setBusy(true);
    setError(null);
    try {
      const out = await draftInstructions(encounter.encounter_id);
      setMissing(isInsufficient(out) ? out.missing : null);
      await queryClient.invalidateQueries({ queryKey: ["doc-encounter", encounter.encounter_id] });
      await queryClient.invalidateQueries({ queryKey: ["doc-worklist"] });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not draft");
    } finally {
      setBusy(false);
    }
  }
  const template = encounter.ai_mode === "template_only";
  return (
    <Card title="Patient instructions">
      <div className="space-y-3" data-testid="instructions-start">
        <p className="text-sm text-slate-600">
          {summaryFinal
            ? template
              ? `No AI draft: ${encounter.ai_mode_reason}. Open the template: the signed summary's medicines and follow-ups are copied in; you choose the rest.`
              : "The discharge summary is signed. The AI drafts the instructions from it (and only from it) in English and Chinese; they appear here shortly, or draft them now."
            : "The instructions are written from the signed discharge summary only: the physician has not signed it yet."}
        </p>
        <Button variant={template ? "primary" : "ai"} onClick={draft} loading={busy} disabled={!summaryFinal} data-testid="draft-instructions">
          {template ? <PenLine className="size-4" /> : <Sparkles className="size-4" />}
          {template ? "Open the template" : "Draft now"}
        </Button>
        {missing && <p className="text-sm text-amber-800" data-testid="insufficient">Insufficient information: missing {missing.join("; ")}.</p>}
        {error && <p className="text-sm text-rose-600" role="alert">{error}</p>}
      </div>
    </Card>
  );
}

export function InstructionsReview({ id }: { id: string }) {
  const { data: doc, isLoading, error, refetch } = useDocument(id);
  if (isLoading) return <Loading />;
  if (error || !doc) return <ErrorState error={error} onRetry={refetch} />;
  return <Review doc={doc} />;
}

export function RenderedInstructions({ r, testId = "rendered" }: { r: Rendering; testId?: string }) {
  return (
    <article lang={r.lang === "en" ? "en" : r.lang === "zh-Hans" ? "zh-Hans" : "zh-Hant"} className="space-y-4 text-sm text-slate-800" data-testid={testId}>
      <h2 className="text-lg font-semibold text-slate-900">{r.title}</h2>
      {r.blocks.map((b) => (
        <section key={b.key} data-testid={`block-${b.key}`}>
          <h3 className="mb-1 font-semibold text-slate-900">{b.title}</h3>
          {b.lines?.map((l) => <p key={l.key} data-key={l.key} data-testid={b.key === "terms" ? "term" : "fact"}>{l.text}</p>)}
          {b.rows && (
            <table className="w-full table-fixed border-collapse text-left text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-xs text-slate-500">{b.columns?.map((c) => <th key={c} className="py-1 pr-2 font-medium">{c}</th>)}</tr>
              </thead>
              <tbody>
                {b.rows.map((row) => (
                  <tr key={row.key} data-key={row.key} data-testid="fact" className={clsx("border-b border-slate-100 align-top", row.category === "stopped" && "text-slate-500")}>
                    {row.cells.map((c, i) => <td key={i} className={clsx("py-1.5 pr-2", i === 0 && "font-medium")}>{c}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {b.groups?.map((g, i) => (
            <div key={i} className={clsx("mt-1 rounded-md p-2", g.action === "emergency" ? "bg-rose-50 ring-1 ring-inset ring-rose-200" : g.action ? "bg-amber-50 ring-1 ring-inset ring-amber-200" : "")}>
              {g.title && <h4 className="text-xs font-semibold text-slate-600">{g.title}</h4>}
              <ul className="list-disc pl-5">
                {g.items.map((item) => <li key={item.key} data-key={item.key} data-testid="fact">{item.text}</li>)}
              </ul>
              {g.text && (
                <p className={clsx("mt-1 flex items-center gap-1 font-semibold", g.action === "emergency" ? "text-rose-700" : "text-amber-800")}>
                  {g.action === "emergency" ? <Siren className="size-4" /> : <Phone className="size-4" />} {g.text}
                </p>
              )}
            </div>
          ))}
          {!b.lines?.length && !b.rows?.length && !b.groups?.length && <p className="text-slate-400">—</p>}
        </section>
      ))}
      <p className="text-xs text-slate-500">{r.ai_note}</p>
    </article>
  );
}

function Readability({ renderings }: { renderings: Record<Lang, Rendering> }) {
  const en = renderings.en.readability;
  const zh = renderings["zh-Hans"].readability;
  const zht = renderings["zh-Hant"].readability;
  return (
    <div className="grid gap-2 text-xs sm:grid-cols-3" data-testid="readability">
      <div className="rounded-md bg-slate-50 p-2">
        <p className="font-semibold text-slate-700">English reading level</p>
        <p data-testid="fk-grade">Grade {en.grade} (Flesch-Kincaid; target 6–8 or easier){en.band === "below" ? " · easier than grade 6" : ""}</p>
      </div>
      {([["简体中文", zh], ["繁體中文", zht]] as const).map(([label, r]) => (
        <div key={label} className="rounded-md bg-slate-50 p-2">
          <p className="font-semibold text-slate-700">{label}</p>
          <p>{r.no_abbreviations ? "No medical abbreviations" : `Abbreviations: ${r.abbreviations?.join(", ")}`}</p>
          <p>Terms explained at first use: {(r.terms ?? 0) - (r.unexplained_terms?.length ?? 0)} of {r.terms} ({r.from_knowledge_graph} from the knowledge graph)</p>
        </div>
      ))}
    </div>
  );
}

function Review({ doc }: { doc: DocView }) {
  const queryClient = useQueryClient();
  const renderings = doc.renderings!;
  const [lang, setLang] = useState<Lang>(doc.default_language ?? "en");
  const [editing, setEditing] = useState(false);
  const [content, setContent] = useState<InstructionsContent>(doc.content as InstructionsContent);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [delivered, setDelivered] = useState<string | null>(null);
  useEffect(() => {
    if (!editing) setContent(doc.content as InstructionsContent);
  }, [doc, editing]);
  const menus = doc.menus!;
  const final = doc.doc_status === "final";

  async function act(name: string, fn: () => Promise<unknown>) {
    setBusy(name);
    setError(null);
    try {
      const out = await fn();
      if (out && (out as DocView).kind) queryClient.setQueryData(["doc", doc.id], out);
      await queryClient.invalidateQueries({ queryKey: ["doc", doc.id] });
      await queryClient.invalidateQueries({ queryKey: ["doc-encounter", doc.encounter_id] });
      await queryClient.invalidateQueries({ queryKey: ["doc-worklist"] });
      return out;
    } catch (e) {
      setError(e instanceof ApiError ? e.message : `Could not ${name}`);
      return null;
    } finally {
      setBusy(null);
    }
  }
  const update = (fn: (c: InstructionsContent) => void) => {
    const next = structuredClone(content);
    fn(next);
    setContent(next);
  };
  async function send(channel: "print" | "sms" | "portal") {
    const out = (await act(channel, () => deliver(doc.id, channel))) as { sent: boolean; reason?: string; url?: string } | null;
    if (!out) return;
    if (channel === "print") window.open(`/documentation/print/${doc.id}`, "_blank");
    setDelivered(channel === "sms" ? (out.sent ? "SMS queued for the patient (mock)." : `SMS not sent: ${out.reason}. The desk will call instead.`)
      : channel === "portal" ? "Published to the patient portal (mock)." : "Print preview opened; the printing is recorded.");
  }

  return (
    <div className="space-y-3" data-testid="instructions-review">
      <Card
        title={<span className="flex items-center gap-2">Patient instructions <span className="font-mono text-xs font-normal text-slate-500">{doc.id}</span></span>}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            {doc.mode === "ai" ? <AiBadge label="AI draft" agent="patient_instructions" /> : <Badge tone="slate">Template (no AI)</Badge>}
            <Badge tone={final ? "green" : doc.blocking ? "red" : "amber"}>{final ? "Final" : doc.blocking ? "Blocked" : "Draft"}</Badge>
          </div>
        }
      >
        <div className="space-y-3">
          <ValidationBar validation={doc.validation} />
          <p className="text-xs text-slate-500">
            From the signed discharge summary <span className="font-mono">{doc.summary}</span> only. Patient's language: {doc.patient_language}
            {" "}(shown first: {LANG_LABEL[doc.default_language ?? "en"]}).
          </p>
          <Tabs<Lang> value={lang} onChange={setLang} tabs={(["en", "zh-Hans", "zh-Hant"] as Lang[]).map((l) => ({ id: l, label: LANG_LABEL[l] }))} />
          <div className="rounded-lg border border-slate-200 bg-white p-4">
            <RenderedInstructions r={renderings[lang]} />
          </div>
          <Readability renderings={renderings} />
          {doc.notes.length > 0 && <ul className="text-xs text-amber-700">{doc.notes.map((n) => <li key={n}>{n}</li>)}</ul>}
          {editing && (
            <div className="space-y-2 rounded-lg bg-slate-50 p-3 text-xs" data-testid="instructions-editor">
              <p className="font-semibold text-slate-700">Adjust the choices (the facts stay those of the signed summary)</p>
              {content.medicines.map((m, i) => (
                <div key={m.item} className="rounded bg-white p-2 ring-1 ring-slate-200">
                  <p className="font-medium">{m.name} {m.dose} <span className="text-slate-400">({m.category})</span></p>
                  {m.category !== "stopped" && (
                    <>
                      <label className="mt-1 block">What it is for{" "}
                        <select value={m.purpose} onChange={(e) => update((c) => { c.medicines[i].purpose = e.target.value; })} data-testid="purpose-select">
                          {Object.entries(menus.purposes).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                        </select>
                      </label>
                      <div className="mt-1 flex flex-wrap gap-2">
                        {Object.entries(menus.times).map(([k, v]) => (
                          <label key={k} className="flex items-center gap-1">
                            <input type="checkbox" checked={m.times.includes(k)}
                              onChange={() => update((c) => { const t = c.medicines[i].times; c.medicines[i].times = t.includes(k) ? t.filter((x) => x !== k) : [...t, k]; })} />
                            {v}
                          </label>
                        ))}
                      </div>
                    </>
                  )}
                </div>
              ))}
              <div className="rounded bg-white p-2 ring-1 ring-slate-200">
                <p className="font-medium">Red flags</p>
                <div className="mt-1 grid gap-1 sm:grid-cols-2">
                  {Object.entries(menus.red_flags).map(([k, v]) => (
                    <label key={k} className="flex items-center gap-1">
                      <input type="checkbox" checked={content.red_flags.some((f) => f.code === k)} data-testid={`flag-${k}`}
                        onChange={() => update((c) => { c.red_flags = c.red_flags.some((f) => f.code === k) ? c.red_flags.filter((f) => f.code !== k) : [...c.red_flags, { code: k, because: [] }]; })} />
                      {v}
                    </label>
                  ))}
                </div>
              </div>
              <div className="rounded bg-white p-2 ring-1 ring-slate-200">
                <p className="font-medium">Daily care</p>
                <div className="mt-1 grid gap-1 sm:grid-cols-2">
                  {Object.entries(menus.daily_care).map(([k, v]) => (
                    <label key={k} className="flex items-center gap-1">
                      <input type="checkbox" checked={content.daily_care.some((f) => f.code === k)}
                        onChange={() => update((c) => { c.daily_care = c.daily_care.some((f) => f.code === k) ? c.daily_care.filter((f) => f.code !== k) : [...c.daily_care, { code: k, because: [] }]; })} />
                      {v}
                    </label>
                  ))}
                </div>
              </div>
            </div>
          )}
          <div className="flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3">
            {!final && doc.can_edit && !editing && <Button size="sm" onClick={() => setEditing(true)} data-testid="edit-instructions"><PenLine className="size-3.5" /> Adjust</Button>}
            {editing && (
              <>
                <Button size="sm" variant="primary" loading={busy === "save"} data-testid="save-instructions"
                  onClick={async () => { if (await act("save", () => saveDocument(doc.id, content))) setEditing(false); }}>
                  <Save className="size-3.5" /> Save and re-render
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setEditing(false)}><X className="size-3.5" /> Cancel</Button>
              </>
            )}
            {!final && (
              <Button size="sm" variant="primary" disabled={!doc.can_sign || editing} loading={busy === "sign"} title={doc.why_not ?? undefined}
                onClick={() => act("sign", () => signDocument(doc.id))} data-testid="sign-instructions">
                <FileSignature className="size-3.5" /> Sign as nurse
              </Button>
            )}
            {!final && doc.why_not && <span className="text-xs text-slate-500">{doc.why_not}</span>}
            {final && (
              <>
                <span className="text-sm text-emerald-700" data-testid="signed">Signed by {doc.signatures.map((s) => s.name ?? s.by).join(", ")}.</span>
                <Button size="sm" onClick={() => send("print")} loading={busy === "print"} data-testid="deliver-print"><Printer className="size-3.5" /> Print preview</Button>
                <Button size="sm" onClick={() => send("sms")} loading={busy === "sms"} data-testid="deliver-sms"
                  title={doc.consent_sms === "permit" ? "SMS consent on file" : "No SMS consent: it will be recorded as not sent"}>
                  <MessageSquare className="size-3.5" /> Send SMS {doc.consent_sms !== "permit" && "(no consent)"}
                </Button>
                <Button size="sm" onClick={() => send("portal")} loading={busy === "portal"} data-testid="deliver-portal"><ExternalLink className="size-3.5" /> Patient portal</Button>
                <Link to={`/documentation/portal/${doc.id}`} className="text-xs text-brand-700 underline" data-testid="portal-link">Open the portal page</Link>
              </>
            )}
          </div>
          {delivered && <p className="text-sm text-slate-700" data-testid="delivered">{delivered}</p>}
          {error && <p className="text-sm text-rose-600" role="alert">{error}</p>}
        </div>
      </Card>
    </div>
  );
}
