import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Pencil, Sparkles } from "lucide-react";
import { useState } from "react";
import { AiBadge, Badge, Button, Card, ErrorState, Loading, PageHeader } from "../../components/ui";
import { api, post, put } from "../../lib/api";
import { useAuth } from "../../lib/auth";

interface Translation { text: string; status: string; source: string; approved_by: string | null }
interface Template { key: string; name: string; english: string; english_approved_by: string; exam_codes: string[]; translations: Record<string, Translation> }

function TranslationRow({ tkey, lang, label, t, canApprove }: { tkey: string; lang: string; label: string; t: Translation | undefined; canApprove: boolean }) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(t?.text ?? "");
  const done = () => { queryClient.invalidateQueries({ queryKey: ["prep-templates"] }); setEditing(false); };
  const draft = useMutation({ mutationFn: () => post(`/api/prep/templates/${tkey}/${lang}/draft`), onSuccess: done });
  const approve = useMutation({ mutationFn: () => post(`/api/prep/templates/${tkey}/${lang}/approve`), onSuccess: done });
  const edit = useMutation({ mutationFn: () => put(`/api/prep/templates/${tkey}/${lang}`, { text }), onSuccess: done });
  return (
    <div className="rounded-lg border border-slate-200 p-3" data-testid={`prep-${tkey}-${lang}`}>
      <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
        <span className="flex items-center gap-2 text-sm font-medium">
          {label}
          {t && <Badge tone={t.status === "approved" ? "green" : "amber"}>{t.status === "approved" ? `approved · ${t.approved_by}` : "draft — cannot be sent"}</Badge>}
          {t?.source === "ai" && t.status !== "approved" && <AiBadge label="AI draft" agent="prep_translation" />}
        </span>
        {canApprove && !editing && (
          <span className="flex gap-1">
            <Button size="sm" variant="ghost" loading={draft.isPending} onClick={() => draft.mutate()}><Sparkles className="size-3.5" /> Redraft with AI</Button>
            <Button size="sm" variant="ghost" onClick={() => { setText(t?.text ?? ""); setEditing(true); }}><Pencil className="size-3.5" /> Edit</Button>
            {t && t.status !== "approved" && <Button size="sm" variant="primary" loading={approve.isPending} onClick={() => approve.mutate()}><Check className="size-3.5" /> Approve</Button>}
          </span>
        )}
      </div>
      {editing ? (
        <div className="space-y-2">
          <textarea value={text} onChange={(e) => setText(e.target.value)} rows={3} className="w-full rounded-lg border border-slate-300 p-2 text-sm" />
          <div className="flex gap-2"><Button size="sm" variant="primary" loading={edit.isPending} onClick={() => edit.mutate()}>Save as draft</Button><Button size="sm" variant="ghost" onClick={() => setEditing(false)}>Cancel</Button></div>
        </div>
      ) : <p className="text-sm text-slate-700">{t?.text ?? <span className="text-slate-400">No translation yet</span>}</p>}
      {(draft.error || approve.error) && <p className="text-sm text-rose-600">{((draft.error || approve.error) as Error).message}</p>}
    </div>
  );
}

export function PrepPage() {
  const { user } = useAuth();
  const q = useQuery({ queryKey: ["prep-templates"], queryFn: () => api<{ templates: Template[]; languages: Record<string, string> }>("/api/prep/templates") });
  const canApprove = !!user && ["medical_director", "radiologist", "admin"].includes(user.role);
  return (
    <div className="mx-auto max-w-4xl">
      <PageHeader title="Prep instructions" subtitle="Approved English templates and their translations. Only approved text is ever sent; translations are drafted ahead of time, never at send time." />
      {q.isLoading && <Loading />}
      {q.error && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      <div className="space-y-4">
        {q.data?.templates.map((t) => (
          <Card key={t.key} title={t.name} actions={<span className="text-xs text-slate-500">{t.exam_codes.join(", ") || "all other exams"}</span>}>
            <div className="mb-3 rounded-lg bg-slate-50 p-3">
              <p className="mb-1 flex items-center gap-2 text-sm font-medium">English <Badge tone="green">approved · {t.english_approved_by}</Badge></p>
              <p className="text-sm text-slate-700">{t.english}</p>
            </div>
            <div className="grid gap-2 md:grid-cols-3">
              {Object.entries(q.data!.languages).filter(([k]) => k !== "en").map(([lang, label]) => (
                <TranslationRow key={`${lang}-${t.translations[lang]?.status}-${t.translations[lang]?.text}`} tkey={t.key} lang={lang} label={label} t={t.translations[lang]} canApprove={canApprove} />
              ))}
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}
