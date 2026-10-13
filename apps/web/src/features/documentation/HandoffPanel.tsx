// The nurse's shift handoff (spec 7.3): SBAR for one patient from the last 12 hours, every statement citing its
// sources. Confirming it is the one signature that makes it final.

import { useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { CheckCheck, Link2, PenLine, RefreshCw, Save, Sparkles, X } from "lucide-react";
import { useEffect, useState } from "react";
import { AiBadge, Badge, Button, Card, ErrorState, Loading } from "../../components/ui";
import { ApiError } from "../../lib/api";
import { draftHandoff, issuesAt, saveDocument, signDocument, useDocument, type DocView, type EncounterView, type Fact, type HandoffContent } from "./api";
import { IssueList, SourcePanel, ValidationBar } from "./kit";

const GAP_LABELS: Record<string, string> = {
  intake_output: "intake and output", vital_signs: "vital signs", pain: "pain score",
  laboratory_results: "laboratory results", open_tasks: "open tasks", flags: "flags",
};

const SECTIONS: { key: Exclude<keyof HandoffContent, "not_recorded">; letter: string; title: string }[] = [
  { key: "situation", letter: "S", title: "Situation" },
  { key: "background", letter: "B", title: "Background" },
  { key: "assessment", letter: "A", title: "Assessment (last 12 hours)" },
  { key: "recommendation", letter: "R", title: "Recommendation (next shift)" },
];

export function HandoffPanel({ encounter }: { encounter: EncounterView }) {
  const queryClient = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (encounter.status !== "in-progress") {
    return <Card title="Shift handoff"><p className="text-sm text-slate-500">This patient has left hospital: no handoff.</p></Card>;
  }
  if (encounter.handoff) return <HandoffReview id={encounter.handoff.id} />;
  async function draft() {
    setBusy(true);
    setError(null);
    try {
      await draftHandoff(encounter.encounter_id);
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
    <Card title={`Shift handoff (${encounter.shift})`}>
      <div className="space-y-3" data-testid="handoff-start">
        <p className="text-sm text-slate-600">
          {template ? `No AI draft: ${encounter.ai_mode_reason}. Open the SBAR template.` :
            "Draft the SBAR from the last 12 hours: vital signs, results, open tasks, flags, new or stopped orders."}
        </p>
        <Button variant={template ? "primary" : "ai"} onClick={draft} loading={busy} data-testid="draft-handoff">
          {template ? <PenLine className="size-4" /> : <Sparkles className="size-4" />} {template ? "Open the template" : "Draft handoff"}
        </Button>
        {error && <p className="text-sm text-rose-600" role="alert">{error}</p>}
      </div>
    </Card>
  );
}

function HandoffReview({ id }: { id: string }) {
  const { data: doc, isLoading, error, refetch } = useDocument(id);
  if (isLoading) return <Loading />;
  if (error || !doc) return <ErrorState error={error} onRetry={refetch} />;
  return <Review doc={doc} />;
}

function Review({ doc }: { doc: DocView }) {
  const queryClient = useQueryClient();
  const [content, setContent] = useState<HandoffContent>(doc.content as HandoffContent);
  const [editing, setEditing] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!editing) setContent(doc.content as HandoffContent);
  }, [doc, editing]);
  const final = doc.doc_status === "final";
  const facts: [string, Fact][] = [];
  for (const s of SECTIONS) {
    const v = content[s.key];
    if (Array.isArray(v)) v.forEach((f, i) => facts.push([`${s.key}[${i}]`, f]));
    else facts.push([s.key, v]);
  }
  const selectedFact = facts.find(([p]) => p === selected)?.[1];

  async function act(name: string, fn: () => Promise<unknown>) {
    setBusy(name);
    setError(null);
    try {
      const out = await fn();
      if (out && (out as DocView).kind) queryClient.setQueryData(["doc", doc.id], out);
      await queryClient.invalidateQueries({ queryKey: ["doc", doc.id] });
      await queryClient.invalidateQueries({ queryKey: ["doc-encounter", doc.encounter_id] });
      await queryClient.invalidateQueries({ queryKey: ["doc-worklist"] });
      return true;
    } catch (e) {
      setError(e instanceof ApiError ? e.message : `Could not ${name}`);
      return false;
    } finally {
      setBusy(null);
    }
  }
  const setText = (path: string, text: string) => {
    const next = structuredClone(content);
    const m = path.match(/^(\w+)\[(\d+)\]$/);
    if (m) (next[m[1] as "background"] as Fact[])[Number(m[2])].text = text;
    else (next[path as "situation"] as Fact).text = text;
    setContent(next);
  };

  return (
    <div className="grid gap-3 xl:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]" data-testid="handoff-review">
      <Card
        title={<span className="flex items-center gap-2">SBAR handoff <span className="font-mono text-xs font-normal text-slate-500">{doc.shift}</span></span>}
        actions={
          <div className="flex items-center gap-2">
            {doc.mode === "ai" ? <AiBadge label="AI draft" agent="nursing_handoff" /> : <Badge tone="slate">Template (no AI)</Badge>}
            <Badge tone={final ? "green" : "amber"}>{final ? "Confirmed" : "Draft"}</Badge>
          </div>
        }
      >
        <div className="space-y-3">
          <ValidationBar validation={doc.validation} />
          {SECTIONS.map((s) => {
            const v = content[s.key];
            const list: [string, Fact][] = Array.isArray(v) ? v.map((f, i) => [`${s.key}[${i}]`, f]) : [[s.key, v]];
            return (
              <section key={s.key} data-testid={`sbar-${s.key}`}>
                <h3 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
                  <span className="grid size-5 place-items-center rounded bg-slate-800 text-[11px] text-white">{s.letter}</span> {s.title}
                </h3>
                <ul className="mt-1 space-y-1">
                  {list.map(([path, f]) => {
                    const issues = issuesAt(doc.validation, path);
                    return (
                      <li key={path} onClick={() => setSelected(path === selected ? null : path)} data-testid="sbar-item"
                        className={clsx("cursor-pointer rounded-md border px-2 py-1.5 text-sm",
                          issues.some((i) => i.severity === "red") ? "border-rose-300 bg-rose-50" : path === selected ? "border-brand-300 bg-brand-50/60" : "border-slate-200")}>
                        {editing ? (
                          <textarea className="w-full rounded border border-slate-300 px-2 py-1 text-sm" rows={2} value={f.text}
                            onClick={(e) => e.stopPropagation()} onChange={(e) => setText(path, e.target.value)} />
                        ) : (
                          <div className="flex items-start justify-between gap-2">
                            <span>{f.text || <em className="text-slate-400">empty</em>}</span>
                            <span className="inline-flex shrink-0 items-center gap-0.5 text-[11px] text-slate-500"><Link2 className="size-3" /> {f.source_refs.length}</span>
                          </div>
                        )}
                        <IssueList issues={issues} />
                      </li>
                    );
                  })}
                </ul>
                {s.key === "assessment" && (content.not_recorded ?? []).length > 0 && (
                  <div className="mt-1 text-xs text-slate-500" data-testid="sbar-not-recorded">
                    Not recorded: {(content.not_recorded ?? []).map((t) => GAP_LABELS[t] ?? t).join(", ")}
                    <IssueList issues={(doc.validation?.issues ?? []).filter((i) => i.path.startsWith("not_recorded["))} />
                  </div>
                )}
              </section>
            );
          })}
          <div className="flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3">
            {!final && doc.can_edit && !editing && <Button size="sm" onClick={() => setEditing(true)}><PenLine className="size-3.5" /> Edit</Button>}
            {editing && (
              <>
                <Button size="sm" variant="primary" loading={busy === "save"} onClick={async () => { if (await act("save", () => saveDocument(doc.id, content))) setEditing(false); }}>
                  <Save className="size-3.5" /> Save
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setEditing(false)}><X className="size-3.5" /> Cancel</Button>
              </>
            )}
            {!final && doc.mode === "ai" && !editing && (
              <Button size="sm" variant="ghost" loading={busy === "redraft"} onClick={() => act("redraft", () => draftHandoff(doc.encounter_id, true))}>
                <RefreshCw className="size-3.5" /> Redraft
              </Button>
            )}
            {!final && (
              <Button size="sm" variant="primary" disabled={!doc.can_sign || editing} title={doc.why_not ?? undefined} loading={busy === "confirm"}
                onClick={() => act("confirm", () => signDocument(doc.id))} data-testid="confirm-handoff">
                <CheckCheck className="size-3.5" /> Confirm handoff
              </Button>
            )}
            {final && <span className="text-sm text-emerald-700" data-testid="confirmed">Confirmed by {doc.signatures.map((s) => s.name ?? s.by).join(", ")}: final.</span>}
          </div>
          {error && <p className="text-sm text-rose-600" role="alert">{error}</p>}
        </div>
      </Card>
      <Card title="Sources (last 12 hours)">
        <div className="xl:max-h-[calc(100vh-230px)] xl:overflow-y-auto">
          <SourcePanel sources={doc.sources ?? []} highlight={selectedFact?.source_refs ?? []} />
        </div>
      </Card>
    </div>
  );
}
