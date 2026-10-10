// Post-discharge follow-up (spec 7.4): the nurse follow-up queue and the follow-up calls.
// Queue: every call that needs a nurse, escalations first (an emergency, then urgent red flags, then the classifier's
// and the manual follow-ups), with the MRN, the discharge date, the call summary, the red flags and the patient's
// own words. The nurse writes a call-back note, closes the escalation or refers it to a physician; each is a
// Communication. Calls: the scheduled calls, placed here with the patient simulated (type or speak as them).

import { useMutation, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { AlertTriangle, Bell, Bot, CheckCircle2, ClipboardList, Phone, PhoneCall, PhoneMissed, RefreshCw, Send, Siren, Stethoscope } from "lucide-react";
import { useEffect, useState } from "react";
import { AiBadge, Badge, Button, Card, EmptyState, ErrorState, Loading, PageHeader, Stat, Tabs } from "../../components/ui";
import { useAuth } from "../../lib/auth";
import {
  answerFollowup, MISSING_LABEL, noAnswer, nurseAction, PRIORITY_LABEL, PRIORITY_TONE, startFollowup, sweep,
  useDemo, useEscalationSummary, useFollowup, useFollowups, type FollowupItem,
} from "./api";
import { CallerInput, SpeakerToggle, Transcript, useSpeech } from "./Console";

type Tab = "open" | "calls" | "all";

function day(iso?: string | null) {
  return iso ? new Date(iso).toLocaleString([], { weekday: "short", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "–";
}

function statusBadge(item: FollowupItem) {
  if (item.escalated && item.review_status === "open") {
    return <Badge tone={PRIORITY_TONE[item.priority ?? "routine"]}>{item.emergency ? <Siren className="size-3" /> : <AlertTriangle className="size-3" />}
      {PRIORITY_LABEL[item.priority ?? "routine"]}{item.escalated_by === "rules" ? " · red flag" : item.escalated_by === "model" ? " · AI" : ""}</Badge>;
  }
  if (item.status === "manual") return <Badge tone="slate"><ClipboardList className="size-3" />Manual follow-up</Badge>;
  if (item.review_status === "closed") return <Badge tone="green">Closed</Badge>;
  if (item.review_status === "referred") return <Badge tone="blue">Referred</Badge>;
  return <Badge tone={item.status === "completed" ? "green" : "slate"}>{item.status.replace("_", " ")}</Badge>;
}

export function FollowUpPage() {
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<Tab>("open");
  const [selected, setSelected] = useState<string | null>(null);
  const list = useFollowups(tab);
  const summary = useEscalationSummary(true);
  const scan = useMutation({ mutationFn: sweep, onSuccess: () => queryClient.invalidateQueries({ queryKey: ["voice"] }) });
  const items = list.data?.items ?? [];

  useEffect(() => {
    if (!selected && items.length) setSelected(items[0].id);
  }, [items, selected]);

  return (
    <div>
      <PageHeader title="Follow-up calls"
        subtitle="The AI calls discharged patients 48 hours after discharge with questions from their signed instructions. Red flags go to a nurse at once; the AI never gives medical advice."
        actions={<Button size="sm" onClick={() => scan.mutate()} loading={scan.isPending} title="Schedule the calls that are due now (the server also does this every few seconds)">
          <RefreshCw className="size-4" />Check for due calls</Button>} />
      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Need a nurse" value={summary.data?.open ?? "–"} />
        <Stat label="Escalated" value={summary.data?.escalated ?? "–"} tone={summary.data?.escalated ? "amber" : undefined} />
        <Stat label="Urgent" value={summary.data?.urgent ?? "–"} tone={summary.data?.urgent ? "amber" : undefined} />
        <Stat label="Emergency" value={summary.data?.stat ?? "–"} tone={summary.data?.stat ? "red" : undefined} />
      </div>
      <Tabs value={tab} onChange={(t) => { setTab(t); setSelected(null); }}
        tabs={[{ id: "open", label: "Queue" }, { id: "calls", label: "Calls" }, { id: "all", label: "All" }]} />
      <div className="grid gap-4 xl:grid-cols-[24rem_1fr]">
        <Card padded={false} title={tab === "open" ? "Needs a nurse (highest priority first)" : tab === "calls" ? "Calls to place and placed" : "Every follow-up"}>
          {list.isLoading ? <Loading /> : list.error ? <ErrorState error={list.error} onRetry={() => list.refetch()} /> : !items.length ? (
            <EmptyState title={tab === "open" ? "Nothing needs a nurse" : "No follow-up calls"} hint="Calls are scheduled 48 hours after discharge; check for due calls to schedule them now." />
          ) : (
            <ul className="divide-y divide-slate-100" data-testid="followup-list">
              {items.map((item) => (
                <li key={item.id}>
                  <button onClick={() => setSelected(item.id)} data-testid={`followup-${item.id}`}
                    className={clsx("w-full px-4 py-3 text-left hover:bg-slate-50", selected === item.id && "bg-brand-50")}>
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-sm font-medium text-slate-800">MRN {item.mrn ?? "?"} {item.language !== "en" && <span className="text-xs text-slate-500">· 中文</span>}</span>
                      {statusBadge(item)}
                    </div>
                    <p className="mt-0.5 text-xs text-slate-500">Discharged {day(item.discharged_at)} · {item.unit_id}</p>
                    {item.red_flags.length > 0 && <p className="mt-1 text-xs text-rose-700">{item.red_flags.map((f) => f.label).join(", ")}</p>}
                    {item.status === "manual" && <p className="mt-1 text-xs text-slate-600">{item.missing.map((m) => MISSING_LABEL[m] ?? m).join(", ")}</p>}
                    {item.restricted && <p className="mt-1 text-xs text-slate-600">{item.reason}</p>}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>
        {selected ? <Detail id={selected} /> : <Card><EmptyState title="Select a follow-up" /></Card>}
      </div>
    </div>
  );
}

function Detail({ id }: { id: string }) {
  const q = useFollowup(id);
  const { user } = useAuth();
  const nurse = user?.role === "nurse"; // nurses place the calls and handle escalations; physicians read
  if (q.isLoading) return <Card><Loading /></Card>;
  if (q.error || !q.data) return <Card><ErrorState error={q.error} onRetry={() => q.refetch()} /></Card>;
  const item = q.data;
  return (
    <div className="space-y-4">
      <Card title={<span className="flex items-center gap-2">MRN {item.mrn} · {item.patient_name}</span>} actions={statusBadge(item)}>
        <dl className="grid gap-3 text-sm md:grid-cols-3" data-testid="followup-detail">
          <div><dt className="text-xs text-slate-500">Discharged</dt><dd>{day(item.discharged_at)} ({item.unit_id})</dd></div>
          <div><dt className="text-xs text-slate-500">Call</dt><dd>{item.status.replace("_", " ")}{item.due_at ? ` · due ${day(item.due_at)}` : ""}</dd></div>
          <div><dt className="text-xs text-slate-500">Language</dt><dd>{item.language === "en" ? "English" : item.language === "zh-Hant" ? "繁體中文" : "简体中文"}</dd></div>
        </dl>
        {item.status === "manual" && (
          <p className="mt-3 rounded-md bg-slate-50 px-3 py-2 text-sm text-slate-700" data-testid="manual-reason">
            No automated call: {item.reason}. A nurse follows up by phone (Task {item.task?.id}).</p>
        )}
        {item.red_flags.length > 0 && (
          <div className="mt-3">
            <p className="text-xs font-medium text-slate-500">Red flags (rules, before any AI)</p>
            <ul className="mt-1 space-y-1" data-testid="red-flags">
              {item.red_flags.map((f) => (
                <li key={f.code} className="flex flex-wrap items-center gap-2 text-sm">
                  <Badge tone={f.emergency ? "red" : "amber"}>{f.label}</Badge>
                  <span className="text-xs text-slate-500">{f.label_zh}</span>
                  {f.temperature_c && <span className="text-xs">{f.temperature_c} °C</span>}
                </li>
              ))}
            </ul>
          </div>
        )}
        {item.patient_words.length > 0 && (
          <div className="mt-3">
            <p className="text-xs font-medium text-slate-500">The patient's words</p>
            <ul className="mt-1 space-y-1" data-testid="patient-words">
              {item.patient_words.map((w, i) => <li key={i} className="rounded-md bg-amber-50 px-2 py-1 text-sm text-slate-800">“{w}”</li>)}
            </ul>
          </div>
        )}
        {item.summary && (
          <div className="mt-3">
            <p className="flex items-center gap-1.5 text-xs font-medium text-slate-500">Call summary {item.classifier?.status === "ok" && <AiBadge agent="followup_calls" />}</p>
            <p className="mt-1 text-sm text-slate-700" data-testid="call-summary">{item.summary}</p>
            {item.classifier?.status === "ok" && (
              <p className="mt-1 text-xs text-slate-500">AI classifier: {item.classifier.escalate ? "escalate" : "no escalation"} ({item.classifier.reason}; confidence {item.classifier.confidence})</p>
            )}
            {item.classifier && item.classifier.status !== "ok" && <p className="mt-1 text-xs text-amber-700">AI classifier {item.classifier.status}: the rules and a nurse decide.</p>}
          </div>
        )}
      </Card>
      {(item.status === "scheduled" || item.status === "in_progress") && (nurse ? <CallPanel item={item} />
        : <Card><p className="text-sm text-slate-500">A nurse places this call.</p></Card>)}
      {item.transcript && item.transcript.length > 0 && item.status !== "in_progress" && <TranscriptCard item={item} />}
      {item.review_status !== "none" && <NursePanel item={item} canAct={nurse} />}
    </div>
  );
}

function TranscriptCard({ item }: { item: FollowupItem }) {
  const [deid, setDeid] = useState(false);
  return (
    <Card title="The call" actions={item.seen_by_model ? (
      <Button size="sm" variant="ghost" onClick={() => setDeid(!deid)}>{deid ? "Show the call" : "As the AI saw it (de-identified)"}</Button>) : undefined}>
      {deid ? <pre className="max-h-80 overflow-y-auto whitespace-pre-wrap rounded-lg bg-slate-50 p-3 text-xs text-slate-700" data-testid="seen-by-model">{item.seen_by_model}</pre>
        : <Transcript turns={item.transcript ?? []} callerLabel="Patient" testId="followup-transcript" />}
    </Card>
  );
}

const ANSWERS: Record<string, { label: string; text: string }[]> = {
  en: [{ label: "No", text: "No" }, { label: "Yes", text: "Yes" }, { label: "Feeling fine", text: "I'm feeling fine, thank you." },
    { label: "Fever 38.9", text: "My temperature was 38.9 last night." }, { label: "Wound leaking", text: "The wound keeps leaking a lot, the dressing is soaked." },
    { label: "Chest pain", text: "I have some chest pain." }, { label: "A nurse, please", text: "I want to talk to a nurse." }, { label: "That's all", text: "No, that's all." }],
  zh: [{ label: "没有", text: "没有" }, { label: "有", text: "有" }, { label: "还好", text: "还可以，谢谢" },
    { label: "伤口渗液", text: "还行，就是伤口一直在渗液，纱布都湿透了" }, { label: "发烧", text: "我昨天体温38度9" },
    { label: "胸口痛", text: "我胸口有点闷，有时候疼" }, { label: "找护士", text: "我想跟护士说话" }, { label: "没有了", text: "没有了" }],
};

function CallPanel({ item }: { item: FollowupItem }) {
  const queryClient = useQueryClient();
  const speech = useSpeech();
  const demo = useDemo();
  const [turns, setTurns] = useState(item.transcript ?? []);
  const [ended, setEnded] = useState(false);
  useEffect(() => setTurns(item.transcript ?? []), [item.id]); // eslint-disable-line react-hooks/exhaustive-deps
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["voice"] });
  const start = useMutation({
    mutationFn: () => startFollowup(item.id),
    onSuccess: (r) => { setTurns(r.item.transcript ?? []); speech.say(r.reply, r.item.language); refresh(); },
  });
  const answer = useMutation({
    mutationFn: (text: string) => answerFollowup(item.id, text),
    onSuccess: (r) => { setTurns(r.item.transcript ?? []); setEnded(r.ended); speech.say(r.reply, r.item.language); refresh(); },
  });
  const missed = useMutation({ mutationFn: () => noAnswer(item.id), onSuccess: refresh });
  const lang = item.language === "en" ? "en" : "zh";
  const birth = Object.values(demo.data?.followups ?? {}).find((f) => f.stay === item.encounter_id)?.birth_date_line;
  const quick = [...(birth ? [{ label: lang === "en" ? "Birth date" : "出生日期", text: birth[lang] }] : []), ...ANSWERS[lang]];
  const placed = turns.length > 0 || item.status === "in_progress";
  return (
    <Card title={<span className="flex items-center gap-2"><Bot className="size-4 text-ai-600" />Follow-up call <AiBadge agent="followup_calls" label="AI agent" /></span>}
      actions={<>
        <SpeakerToggle on={speech.on} setOn={speech.setOn} />
        {!placed && <Button size="sm" variant="secondary" onClick={() => missed.mutate()} loading={missed.isPending}><PhoneMissed className="size-4" />No answer</Button>}
        {!placed && <Button size="sm" variant="primary" onClick={() => start.mutate()} loading={start.isPending} data-testid="place-call"><PhoneCall className="size-4" />Place the call</Button>}
      </>}>
      {!placed ? (
        <EmptyState title="The call is ready" icon={<Phone className="size-8" />}
          hint={`${item.questions - 1} questions from the signed instructions, in ${item.language === "en" ? "English" : "Chinese"}. Place it and answer as the patient (type, speak or use the quick answers).`} />
      ) : (
        <>
          <Transcript turns={turns} pending={answer.isPending} callerLabel="Patient" testId="followup-call-transcript" />
          {!ended ? <CallerInput lang={item.language} busy={answer.isPending} onSend={(t) => answer.mutate(t)} quick={quick}
            placeholder={lang === "en" ? "Type what the patient says…" : "输入患者的回答…"} />
            : <p className="mt-3 text-sm text-slate-500" data-testid="followup-ended">The call has ended.</p>}
        </>
      )}
      {(start.error || answer.error) && <p className="mt-2 text-sm text-rose-600" role="alert">{((start.error || answer.error) as Error).message}</p>}
    </Card>
  );
}

function NursePanel({ item, canAct }: { item: FollowupItem; canAct: boolean }) {
  const queryClient = useQueryClient();
  const [note, setNote] = useState("");
  const act = useMutation({
    mutationFn: (kind: "callback" | "close" | "refer") => nurseAction(item.id, kind, note),
    onSuccess: () => { setNote(""); queryClient.invalidateQueries({ queryKey: ["voice"] }); },
  });
  const open = item.review_status === "open" && canAct;
  return (
    <Card title={<span className="flex items-center gap-2"><Stethoscope className="size-4" />Nurse follow-up</span>}
      actions={item.task ? <Badge tone={item.task.status === "completed" ? "green" : PRIORITY_TONE[item.task.priority] ?? "slate"}>Task {item.task.status}</Badge> : undefined}>
      {item.nurse_actions.length > 0 && (
        <ul className="mb-3 space-y-1.5" data-testid="nurse-actions">
          {item.nurse_actions.map((a, i) => (
            <li key={i} className="rounded-md bg-slate-50 px-2 py-1.5 text-xs text-slate-700">
              <span className="font-medium">{a.kind === "callback" ? "Call-back note" : a.kind === "close" ? "Closed" : "Referred to a physician"}</span> · {a.name} · {day(a.at)}<br />{a.note}
            </li>
          ))}
        </ul>
      )}
      {open ? (
        <div className="space-y-2">
          <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={3} placeholder="What you did or found on the call-back (recorded as a Communication)"
            className="w-full rounded-lg border border-slate-300 p-2 text-sm" aria-label="Nurse note" />
          <div className="flex flex-wrap gap-2">
            <Button size="sm" onClick={() => act.mutate("callback")} disabled={note.trim().length < 3 || act.isPending}><Send className="size-4" />Save call-back note</Button>
            <Button size="sm" variant="primary" onClick={() => act.mutate("close")} disabled={note.trim().length < 3 || act.isPending}><CheckCircle2 className="size-4" />Close</Button>
            <Button size="sm" variant="secondary" onClick={() => act.mutate("refer")} disabled={note.trim().length < 3 || act.isPending}><Stethoscope className="size-4" />Refer to a physician</Button>
          </div>
        </div>
      ) : <p className="text-sm text-slate-500">{item.review_status === "closed" ? "Closed." : item.review_status === "referred" ? "Referred to a physician (a Task for the physician)." : ""}</p>}
      {act.error && <p className="mt-2 text-sm text-rose-600" role="alert">{(act.error as Error).message}</p>}
    </Card>
  );
}

/** The nav's notification: open escalations for the nurse's units (polled). */
export function EscalationCount({ enabled }: { enabled: boolean }) {
  const q = useEscalationSummary(enabled);
  if (!enabled || !q.data?.escalated) return null;
  return (
    <span className={clsx("ml-auto inline-flex items-center gap-0.5 rounded-full px-1.5 text-[10px] font-semibold text-white", q.data.stat ? "bg-rose-600" : "bg-amber-500")}
      data-testid="escalation-count" title="Follow-up calls escalated to a nurse">
      <Bell className="size-2.5" />{q.data.escalated}
    </span>
  );
}
