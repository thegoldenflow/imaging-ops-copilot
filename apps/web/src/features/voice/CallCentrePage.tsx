// Hospital call centre (spec 7.4). The clerk runs a text-simulated inbound call (type or speak as the caller, in
// English or Chinese), sees what the AI recognised and did, confirms the reschedules and cancellations the call
// centre requested (one click: the approval of the privileged booking tool), and reads the call log.

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, CheckCircle2, PhoneCall, PhoneOff, ShieldCheck, XCircle } from "lucide-react";
import { useState } from "react";
import { AiBadge, Badge, Button, Card, EmptyState, ErrorState, Loading, PageHeader } from "../../components/ui";
import {
  decide, endCall, OUTCOME_TONE, sendTurn, startCall, useCalls, useDemo, useRequests, voiceKeys,
  type CallRequest, type HospitalCall,
} from "./api";
import { useDemoMode } from "../storyline/demo";
import { CallerInput, NoCall, SpeakerToggle, Transcript, useSpeech } from "./Console";

const QUICK_EN = [
  { label: "Visiting hours?", text: "What are the visiting hours?" },
  { label: "Parking", text: "Where do I park?" },
  { label: "Reschedule", text: "I need to reschedule my appointment." },
  { label: "Cancel", text: "I want to cancel my appointment." },
  { label: "When is it?", text: "When is my next appointment?" },
  { label: "How to prepare", text: "How should I prepare for my appointment?" },
  { label: "Medical question", text: "My ankles are swollen, is that normal?" },
  { label: "The first one", text: "The first one" },
  { label: "Yes", text: "Yes, please" },
  { label: "That's all", text: "No, that's all, thanks" },
  { label: "A person", text: "Can I speak to a real person?" },
];
const QUICK_ZH = [
  { label: "探视时间", text: "探视时间是几点？" },
  { label: "停车", text: "医院哪里可以停车？" },
  { label: "改期", text: "我想改一下预约时间" },
  { label: "取消", text: "我要取消预约" },
  { label: "查预约", text: "我想查一下我的预约" },
  { label: "准备事项", text: "预约前需要准备什么？" },
  { label: "医疗问题", text: "我最近头晕，药要不要减量？" },
  { label: "第一个", text: "第一个" },
  { label: "好的", text: "好的" },
  { label: "没有了", text: "没有了，谢谢" },
  { label: "转人工", text: "请帮我转人工" },
];

const ACTION_LABEL: Record<string, string> = {
  identity_verified: "Identity verified", identity_failed: "Identity check failed", lookup: "Appointments read out",
  reschedule_requested: "Reschedule requested (clerk confirms)", cancel_requested: "Cancellation requested (clerk confirms)",
  prep_given: "Prep instructions given (Communication)", nurse_line: "Nurse line: Communication + Task",
  faq: "Answered from the FAQ", faq_unanswered: "Not in the FAQ", transfer: "Transfer Task for the call desk",
};

function when(iso?: string) {
  return iso ? new Date(iso).toLocaleString([], { weekday: "short", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "";
}

export function CallCentrePage() {
  const queryClient = useQueryClient();
  const speech = useSpeech();
  const [call, setCall] = useState<HospitalCall | null>(null);
  const [ended, setEnded] = useState(false);
  const demo = useDemo();
  const presenting = useDemoMode().active;

  const start = useMutation({
    mutationFn: startCall,
    onSuccess: (c) => { setCall(c); setEnded(false); speech.say(c.transcript?.[0]?.text ?? "", "en"); },
  });
  const turn = useMutation({
    mutationFn: (text: string) => sendTurn(call!.id, text),
    onSuccess: (r) => {
      setCall(r.call);
      setEnded(r.ended);
      speech.say(r.reply, r.language);
      queryClient.invalidateQueries({ queryKey: voiceKeys.requests });
      if (r.ended) queryClient.invalidateQueries({ queryKey: voiceKeys.calls });
    },
  });
  const end = useMutation({
    mutationFn: () => endCall(call!.id),
    onSuccess: (c) => { setCall(c); setEnded(true); queryClient.invalidateQueries({ queryKey: voiceKeys.calls }); },
  });

  const active = !!call && !ended && !call.ended_at;
  const callers = Object.entries(demo.data?.callers ?? {}).filter(([, c]) => c.identity_line);
  const lang = call?.language ?? "en";
  // the seeded callers' identity lines, labelled by who is calling and what they have booked ("I'm George Gordon
  // (fracture clinic, CT scan)"): hidden while the storyline is presented in demo mode, where the guide's Say it
  // buttons speak the caller's lines
  const quick = [...(lang === "en" ? QUICK_EN : QUICK_ZH),
    ...(presenting ? [] : callers.filter(([, c]) => (lang === "en") === (c.language === "en"))
      .map(([key, c]) => ({ label: c.label ?? key, text: (lang === "en" ? c.identity_line : c.identity_line_zh) ?? "" })))];

  return (
    <div>
      <PageHeader title="Call centre"
        subtitle="Hospital call line: the AI recognises what the caller wants, checks identity before anything personal and never answers a medical question. Calls are text-simulated (type or speak as the caller); no audio is stored." />
      <div className="grid gap-4 xl:grid-cols-[1fr_22rem]">
        <Card
          title={<span className="flex items-center gap-2">Inbound call {call && <AiBadge agent="call_center" label="AI agent" />}
            {call && <Badge tone="slate">{call.language === "en" ? "English" : call.language === "zh-Hant" ? "繁體中文" : "简体中文"}</Badge>}</span>}
          actions={<>
            <SpeakerToggle on={speech.on} setOn={speech.setOn} />
            {active ? (
              <Button size="sm" variant="danger" onClick={() => end.mutate()} loading={end.isPending}><PhoneOff className="size-4" /> End call</Button>
            ) : (
              <Button size="sm" variant="primary" onClick={() => start.mutate()} loading={start.isPending} data-testid="start-call"><PhoneCall className="size-4" /> Start a call</Button>
            )}
          </>}
        >
          {!call ? (
            <NoCall title="No call in progress" icon={<PhoneCall className="size-8" />}
              hint="Start a call and play the caller. Ask about visiting hours or parking (no identity needed), or about an appointment: the agent asks for the patient's name, birth date and the last four digits of the health card first. Speak Chinese and it answers in Chinese." />
          ) : (
            <>
              <Transcript turns={call.transcript ?? []} pending={turn.isPending} callerLabel="Caller" testId="call-transcript" />
              {active ? (
                <CallerInput lang={lang} busy={turn.isPending} onSend={(t) => turn.mutate(t)} quick={quick}
                  placeholder="Type what the caller says…" />
              ) : (
                <p className="mt-3 text-sm text-slate-500" data-testid="call-ended">The call has ended ({call.outcome}).</p>
              )}
              {turn.error && <p className="mt-2 text-sm text-rose-600" role="alert">{(turn.error as Error).message}</p>}
            </>
          )}
        </Card>
        <CallDetails call={call} />
      </div>
      <div className="mt-4 grid gap-4 xl:grid-cols-[1fr_1fr]">
        <Requests />
        <RecentCalls />
      </div>
    </div>
  );
}

function CallDetails({ call }: { call: HospitalCall | null }) {
  if (!call) {
    return <Card title="Call details"><p className="text-sm text-slate-500">Details appear once a call starts.</p></Card>;
  }
  return (
    <Card title="Call details">
      <dl className="space-y-3 text-sm" data-testid="call-details">
        <div className="flex items-center justify-between"><dt className="text-xs text-slate-500">Identity</dt>
          <dd>{call.patient_id ? <Badge tone="green"><ShieldCheck className="size-3" />Verified{call.mrn ? ` · MRN ${call.mrn}` : ""}</Badge>
            : <Badge tone="amber">Not verified{call.identity_failures ? ` (${call.identity_failures} failed)` : ""}</Badge>}</dd></div>
        {call.caller_relation && <div className="flex items-center justify-between"><dt className="text-xs text-slate-500">Caller</dt><dd>the patient's {call.caller_relation}</dd></div>}
        <div><dt className="flex items-center gap-1.5 text-xs text-slate-500">Recognised <AiBadge agent="call_center" label="AI" /></dt>
          <dd className="mt-1 flex flex-wrap gap-1">{call.intents.length ? call.intents.map((i, n) => (
            <Badge key={n} tone={i.source === "model" ? "ai" : "slate"}>{i.intent}{i.source === "rules" ? " · rule" : ""}</Badge>)) : "–"}</dd></div>
        <div><dt className="text-xs text-slate-500">What the call did</dt>
          <dd><ul className="mt-1 space-y-1" data-testid="call-actions">{call.actions.length ? call.actions.map((a, n) => (
            <li key={n} className="text-xs text-slate-700">• {ACTION_LABEL[a.action] ?? a.action}{a.status === "approval_required" ? " — waiting for a clerk" : ""}</li>))
            : <li className="text-xs text-slate-500">Nothing yet</li>}</ul></dd></div>
        <div className="flex items-center justify-between"><dt className="text-xs text-slate-500">Outcome</dt>
          <dd><Badge tone={OUTCOME_TONE[call.outcome] ?? "slate"}>{call.outcome}</Badge></dd></div>
        {call.transfer_reason && <div><dt className="text-xs text-slate-500">Put through because</dt><dd className="text-xs">{call.transfer_reason}</dd></div>}
        {call.model_status && call.model_status !== "ok" && (
          <p className="rounded-md bg-amber-50 px-2 py-1 text-xs text-amber-800">AI {call.model_status === "not_used" ? "not used (rules only)" : "unavailable: keyword rules recognised the intent"}</p>
        )}
        {call.summary && <div><dt className="text-xs text-slate-500">Call log summary</dt><dd className="mt-1 text-xs text-slate-700" data-testid="call-summary">{call.summary}</dd></div>}
      </dl>
    </Card>
  );
}

function Requests() {
  const queryClient = useQueryClient();
  const q = useRequests();
  const act = useMutation({
    mutationFn: ({ id, decision }: { id: string; decision: "approve" | "reject" }) => decide(id, decision, decision === "approve" ? "Confirmed in the call centre" : "Not possible"),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: voiceKeys.requests }); queryClient.invalidateQueries({ queryKey: voiceKeys.calls }); },
  });
  return (
    <Card title={<span className="flex items-center gap-2"><CalendarClock className="size-4" />Requests to confirm</span>}>
      {q.isLoading ? <Loading /> : q.error ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : !q.data?.requests.length ? (
        <EmptyState title="Nothing to confirm" hint="Reschedules, cancellations and ED registrations the call centre takes wait here for a clerk: the AI only proposes, one click books, cancels or registers." />
      ) : (
        <ul className="divide-y divide-slate-100" data-testid="call-requests">
          {q.data.requests.map((r: CallRequest) => (
            <li key={r.task_id} className="flex flex-wrap items-center justify-between gap-2 py-2.5">
              <div className="text-sm">
                <p className="font-medium text-slate-800">
                  {r.kind === "registration" ? "ED registration" : r.kind === "reschedule" ? "Reschedule" : "Cancel"} · MRN {r.mrn ?? "?"}
                  {r.kind === "registration" && <AiBadge label="pre-filled from the call" agent="call_center" />}
                </p>
                <p className="text-xs text-slate-500" data-testid={r.kind === "registration" ? "registration-request" : undefined}>
                  {r.kind === "registration" ? `${r.args?.complaint ?? ""}, arriving by ${r.args?.mode ?? "?"} · call ${r.args?.call ?? ""} · confirming registers the visit in the EHR` : ""}
                  {r.appointment ? `${r.appointment.service_name ?? r.appointment.service_display}, ${when(r.appointment.start)}` : ""}
                  {r.proposed ? ` → ${when(r.proposed.start)}` : ""}
                </p>
              </div>
              <div className="flex gap-1.5">
                <Button size="sm" variant="primary" onClick={() => act.mutate({ id: r.task_id, decision: "approve" })} loading={act.isPending}
                  aria-label={`Confirm ${r.kind}`}><CheckCircle2 className="size-4" />Confirm</Button>
                <Button size="sm" variant="secondary" onClick={() => act.mutate({ id: r.task_id, decision: "reject" })} disabled={act.isPending}
                  aria-label={`Reject ${r.kind}`}><XCircle className="size-4" />Reject</Button>
              </div>
            </li>
          ))}
        </ul>
      )}
      {act.error && <p className="mt-2 text-sm text-rose-600" role="alert">{(act.error as Error).message}</p>}
    </Card>
  );
}

function RecentCalls() {
  const q = useCalls();
  return (
    <Card title="Recent calls" padded={false}>
      {q.isLoading ? <Loading /> : q.error ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : !q.data?.calls.length ? (
        <EmptyState title="No calls yet" />
      ) : (
        <table className="w-full text-left text-xs" data-testid="recent-calls">
          <thead className="bg-slate-50 text-slate-500"><tr><th className="px-3 py-2">Started</th><th className="px-3 py-2">Caller</th><th className="px-3 py-2">Wanted</th><th className="px-3 py-2">Outcome</th></tr></thead>
          <tbody className="divide-y divide-slate-100">
            {q.data.calls.slice(0, 12).map((c) => (
              <tr key={c.id} title={c.summary ?? ""}>
                <td className="px-3 py-2 text-slate-600">{when(c.started_at)}</td>
                <td className="px-3 py-2">{c.mrn ? `MRN ${c.mrn}` : "not verified"}{c.language !== "en" ? " · 中文" : ""}</td>
                <td className="px-3 py-2">{[...new Set(c.intents.map((i) => i.intent))].join(", ") || "–"}</td>
                <td className="px-3 py-2"><Badge tone={OUTCOME_TONE[c.outcome] ?? "slate"}>{c.outcome}</Badge></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}
