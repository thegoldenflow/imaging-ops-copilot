import { useMutation, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { Mic, MicOff, PhoneOff, PhoneCall, Send, Volume2, VolumeX, Wrench } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { AiBadge, Badge, Button, Card, EmptyState } from "../../components/ui";
import { post } from "../../lib/api";
import type { CallSession } from "../../lib/types";

const QUICK = [
  "Hi, this is Robert Taylor, born April 12, 1968.",
  "I need to cancel my appointment.",
  "Yes, please.",
  "Can I reschedule instead?",
  "The second one.",
  "How should I prepare?",
  "Where are you located?",
  "Is the contrast dye safe for my kidneys?",
  "That's all, thanks.",
];

// Minimal typing for the browser Speech Recognition API (Chrome exposes it prefixed).
interface Recognition {
  lang: string;
  interimResults: boolean;
  start(): void;
  stop(): void;
  onresult: ((e: { results: { 0: { transcript: string } }[] }) => void) | null;
  onend: (() => void) | null;
  onerror: (() => void) | null;
}
const SpeechRecognitionCtor = (window as unknown as { SpeechRecognition?: new () => Recognition; webkitSpeechRecognition?: new () => Recognition })
  .SpeechRecognition ?? (window as unknown as { webkitSpeechRecognition?: new () => Recognition }).webkitSpeechRecognition;

interface TurnResponse { reply: string; mode: string; latency_ms: number; ended: boolean; session: CallSession }

export function PhoneAgent() {
  const queryClient = useQueryClient();
  const [session, setSession] = useState<CallSession | null>(null);
  const [text, setText] = useState("");
  const [speak, setSpeak] = useState(true);
  const [listening, setListening] = useState(false);
  const [lastLatency, setLastLatency] = useState<number | null>(null);
  const [mode, setMode] = useState<string | null>(null);
  const recognition = useRef<Recognition | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => bottom.current?.scrollIntoView({ behavior: "smooth", block: "nearest" }), [session?.transcript.length]);
  useEffect(() => () => window.speechSynthesis?.cancel(), []);

  const say = (line: string) => {
    if (!speak || !window.speechSynthesis) return;
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(line);
    u.lang = "en-CA";
    window.speechSynthesis.speak(u);
  };

  const start = useMutation({
    mutationFn: () => post<CallSession>("/api/frontdesk/calls"),
    onSuccess: (s) => {
      setSession(s);
      setLastLatency(null);
      setMode(s.agent_mode);
      say(s.transcript[0].text);
    },
  });
  const turn = useMutation({
    mutationFn: (utterance: string) => post<TurnResponse>(`/api/frontdesk/calls/${session!.id}/turn`, { text: utterance }),
    onSuccess: (res) => {
      setSession(res.session);
      setLastLatency(res.latency_ms);
      setMode(res.mode);
      say(res.reply);
      queryClient.invalidateQueries();
    },
  });
  const end = useMutation({
    mutationFn: () => post<CallSession>(`/api/frontdesk/calls/${session!.id}/end`),
    onSuccess: (s) => { setSession(s); queryClient.invalidateQueries(); },
  });

  const submit = (utterance: string) => {
    const t = utterance.trim();
    if (!t || !session || session.ended_at || turn.isPending) return;
    setText("");
    turn.mutate(t);
  };

  const toggleMic = () => {
    if (!SpeechRecognitionCtor) return;
    if (listening) { recognition.current?.stop(); return; }
    const r = new SpeechRecognitionCtor();
    r.lang = "en-CA";
    r.interimResults = false;
    r.onresult = (e) => submit(e.results[0][0].transcript);
    r.onend = () => setListening(false);
    r.onerror = () => setListening(false);
    recognition.current = r;
    window.speechSynthesis?.cancel();
    r.start();
    setListening(true);
  };

  const active = session && !session.ended_at;

  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_20rem]">
      <Card
        title={<span className="flex items-center gap-2">AI phone receptionist {mode === "claude" ? <AiBadge /> : mode && <Badge>Scripted fallback</Badge>}</span>}
        actions={
          <>
            <Button size="sm" variant="ghost" onClick={() => setSpeak(!speak)} aria-label={speak ? "Mute agent voice" : "Unmute agent voice"}>
              {speak ? <Volume2 className="size-4" /> : <VolumeX className="size-4" />}
            </Button>
            {active ? (
              <Button size="sm" variant="danger" onClick={() => end.mutate()} loading={end.isPending}><PhoneOff className="size-4" /> End call</Button>
            ) : (
              <Button size="sm" variant="primary" onClick={() => start.mutate()} loading={start.isPending} data-testid="start-call"><PhoneCall className="size-4" /> Start call</Button>
            )}
          </>
        }
      >
        {!session ? (
          <EmptyState title="No active call" hint="Start a call, then speak (Chrome) or type as the caller. The agent verifies identity before discussing any appointment and hands medical questions to staff." icon={<PhoneCall className="size-8" />} />
        ) : (
          <>
            <div className="h-[26rem] space-y-2 overflow-y-auto rounded-lg bg-slate-50 p-3" aria-live="polite" data-testid="transcript">
              {session.transcript.map((t, i) =>
                t.role === "tool" ? (
                  <p key={i} className="flex items-center gap-1.5 px-2 font-mono text-[11px] text-slate-400"><Wrench className="size-3" />{t.text}</p>
                ) : (
                  <div key={i} className={clsx("flex", t.role === "caller" ? "justify-end" : "justify-start")}>
                    <p className={clsx("max-w-[80%] rounded-2xl px-3 py-2 text-sm", t.role === "caller" ? "bg-brand-600 text-white" : "border border-slate-200 bg-white text-slate-800")}>
                      {t.text}
                    </p>
                  </div>
                ),
              )}
              {turn.isPending && <p className="text-xs text-slate-400">Agent is responding…</p>}
              <div ref={bottom} />
            </div>
            {active && (
              <>
                <form className="mt-3 flex gap-2" onSubmit={(e) => { e.preventDefault(); submit(text); }}>
                  {SpeechRecognitionCtor && (
                    <Button type="button" variant={listening ? "danger" : "secondary"} onClick={toggleMic} aria-label={listening ? "Stop listening" : "Speak"}>
                      {listening ? <MicOff className="size-4" /> : <Mic className="size-4" />}
                    </Button>
                  )}
                  <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Type what the caller says…" className="h-9 flex-1 rounded-lg border border-slate-300 px-3 text-sm" aria-label="Caller says" />
                  <Button type="submit" variant="primary" disabled={!text.trim()} loading={turn.isPending}><Send className="size-4" /></Button>
                </form>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {QUICK.map((q) => (
                    <button key={q} onClick={() => submit(q)} disabled={turn.isPending} className="rounded-full border border-slate-200 bg-white px-2.5 py-1 text-xs text-slate-600 hover:border-brand-500 hover:text-brand-700 disabled:opacity-50">
                      {q}
                    </button>
                  ))}
                </div>
              </>
            )}
            {turn.error && <p className="mt-2 text-sm text-rose-600" role="alert">{(turn.error as Error).message}</p>}
          </>
        )}
      </Card>

      <Card title="Call details">
        {!session ? (
          <p className="text-sm text-slate-500">Details appear once a call starts.</p>
        ) : (
          <dl className="space-y-3 text-sm">
            <div><dt className="text-xs text-slate-500">Agent</dt><dd>{mode === "claude" ? "Claude (tool use)" : "Scripted fallback (no API key)"}</dd></div>
            <div><dt className="text-xs text-slate-500">Identity</dt><dd>{session.verified_patient_id ? <Badge tone="green">Verified</Badge> : <Badge tone="amber">Not verified</Badge>}</dd></div>
            <div><dt className="text-xs text-slate-500">Last response time (server)</dt><dd className="tabular">{lastLatency == null ? "–" : `${lastLatency} ms`}</dd></div>
            <div>
              <dt className="text-xs text-slate-500">Actions</dt>
              <dd>{session.actions.length ? <ul className="list-disc pl-4">{session.actions.map((a, i) => <li key={i}>{a.tool}{a.backfill_case ? ` → backfill ${a.backfill_case}` : ""}</li>)}</ul> : "None yet"}</dd>
            </div>
            <div><dt className="text-xs text-slate-500">Outcome</dt><dd><Badge tone={session.outcome === "transferred" ? "amber" : session.outcome === "resolved" ? "green" : "slate"}>{session.outcome}</Badge></dd></div>
            {session.summary && (
              <div><dt className="flex items-center gap-1.5 text-xs text-slate-500">Summary <AiBadge /></dt><dd className="mt-1 text-slate-700">{session.summary}</dd></div>
            )}
          </dl>
        )}
      </Card>
    </div>
  );
}
