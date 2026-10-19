// The call console shared by the call centre and the follow-up calls (spec 7.4): a member of staff plays the caller
// (or the patient), by typing or with the browser's speech recognition, and hears the agent through the browser's
// speech synthesis, in English or Chinese. Only text reaches the server; no audio is recorded or stored. A Chinese
// call can show English subtitles under its turns (storyline/subtitles.ts).

import clsx from "clsx";
import { Bot, Mic, MicOff, Send, User, Volume2, VolumeX } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Button, EmptyState } from "../../components/ui";
import { listenAsCaller } from "../storyline/demo";
import { setSubtitles, useSubtitles } from "../storyline/subtitles";
import { speechLang, type CallLang, type Turn } from "./api";

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

/** Reads a line aloud in the call's language (browser speech synthesis; nothing leaves the browser). */
export function useSpeech() {
  const [on, setOn] = useState(true);
  useEffect(() => () => window.speechSynthesis?.cancel(), []);
  const say = (text: string, lang: CallLang | string) => {
    if (!on || !window.speechSynthesis) return;
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.lang = speechLang(lang);
    window.speechSynthesis.speak(u);
  };
  return { on, setOn, say };
}

export function SpeakerToggle({ on, setOn }: { on: boolean; setOn: (v: boolean) => void }) {
  return (
    <Button size="sm" variant="ghost" onClick={() => setOn(!on)} aria-label={on ? "Mute the agent's voice" : "Read the agent's replies aloud"}>
      {on ? <Volume2 className="size-4" /> : <VolumeX className="size-4" />}
    </Button>
  );
}

export function Transcript({ turns, pending, callerLabel, testId = "transcript" }: {
  turns: Turn[];
  pending?: boolean;
  callerLabel: string;
  testId?: string;
}) {
  const bottom = useRef<HTMLDivElement>(null);
  const subtitles = useSubtitles();
  const foreign = turns.some((t) => t.lang !== "en");
  // Block body: newer Chromium returns a Promise from scrollIntoView, which React would call as a cleanup.
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [turns.length, subtitles.on]);
  return (
    <div>
      {foreign && (
        <label className="mb-1 flex items-center justify-end gap-1.5 text-xs text-slate-500" title="The agent's replies are hand-written in each language; the storyline's scripted lines have fixed translations. Nothing is translated by AI.">
          <input type="checkbox" className="size-3.5 accent-brand-600" checked={subtitles.on} onChange={(e) => setSubtitles(e.target.checked)}
            data-testid="subtitles-toggle" />
          English subtitles
        </label>
      )}
      <div className="h-[24rem] space-y-2 overflow-y-auto rounded-lg bg-slate-50 p-3" aria-live="polite" data-testid={testId}>
        {turns.map((t, i) => {
          const sub = subtitles.on && t.lang !== "en" ? (t.role === "agent" ? t.gloss : subtitles.lines[t.text]) : null;
          return (
            <div key={i} className={clsx("flex items-end gap-1.5", t.role === "caller" ? "justify-end" : "justify-start")}>
              {t.role === "agent" && <Bot className="mb-1 size-4 shrink-0 text-ai-600" aria-label="AI agent" />}
              <div className={clsx("max-w-[80%] rounded-2xl px-3 py-2 text-sm", t.role === "caller" ? "bg-brand-600 text-white" : "border border-slate-200 bg-white text-slate-800")}>
                <p lang={t.lang === "en" ? "en" : t.lang === "zh-Hant" ? "zh-TW" : "zh-CN"}>{t.text}</p>
                {sub && (
                  <p lang="en" data-testid="turn-gloss" className={clsx("mt-1 border-t pt-1 text-xs italic",
                    t.role === "caller" ? "border-brand-400 text-brand-100" : "border-slate-100 text-slate-500")}>
                    <span className="font-semibold not-italic">Translation: </span>{sub}
                  </p>
                )}
                {t.role === "agent" && t.intent && (
                  <p className="mt-1 text-[10px] text-slate-400">recognised: {t.intent}{t.source ? ` (${t.source})` : ""}</p>
                )}
              </div>
              {t.role === "caller" && <User className="mb-1 size-4 shrink-0 text-slate-400" aria-label={callerLabel} />}
            </div>
          );
        })}
        {pending && <p className="text-xs text-slate-400">The agent is answering…</p>}
        <div ref={bottom} />
      </div>
    </div>
  );
}

/** The caller's side: type or speak (Chrome / Edge Web Speech) in the call's language, or pick a quick phrase. */
export function CallerInput({ lang, disabled, busy, onSend, quick, placeholder }: {
  lang: CallLang | string;
  disabled?: boolean;
  busy?: boolean;
  onSend: (text: string) => void;
  quick?: { label: string; text: string }[];
  placeholder: string;
}) {
  const [text, setText] = useState("");
  const [listening, setListening] = useState(false);
  const recognition = useRef<Recognition | null>(null);

  const submit = (value: string) => {
    const t = value.trim();
    if (!t || disabled || busy) return;
    setText("");
    onSend(t);
  };
  // The storyline's demo guide speaks the caller's scripted lines into the open call (its "Say it" buttons), only
  // while this input can take one: its buttons are disabled meanwhile.
  const submitRef = useRef(submit);
  submitRef.current = submit;
  useEffect(() => (disabled || busy ? undefined : listenAsCaller((t) => submitRef.current(t))), [disabled, busy]);

  const toggleMic = () => {
    if (!SpeechRecognitionCtor) return;
    if (listening) { recognition.current?.stop(); return; }
    const r = new SpeechRecognitionCtor();
    r.lang = speechLang(lang);
    r.interimResults = false;
    r.onresult = (e) => submit(e.results[0][0].transcript);
    r.onend = () => setListening(false);
    r.onerror = () => setListening(false);
    recognition.current = r;
    window.speechSynthesis?.cancel();
    r.start();
    setListening(true);
  };

  return (
    <div className="mt-3">
      <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); submit(text); }}>
        {SpeechRecognitionCtor && (
          <Button type="button" variant={listening ? "danger" : "secondary"} onClick={toggleMic} disabled={disabled}
            aria-label={listening ? "Stop listening" : `Speak (${speechLang(lang)})`} title={`Speech recognition: ${speechLang(lang)}`}>
            {listening ? <MicOff className="size-4" /> : <Mic className="size-4" />}
          </Button>
        )}
        <input value={text} onChange={(e) => setText(e.target.value)} placeholder={placeholder} disabled={disabled}
          className="h-9 flex-1 rounded-lg border border-slate-300 px-3 text-sm disabled:bg-slate-100" aria-label={placeholder} />
        <Button type="submit" variant="primary" disabled={disabled || !text.trim()} loading={busy} aria-label="Send">
          <Send className="size-4" />
        </Button>
      </form>
      {quick && quick.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5" aria-label="Quick phrases">
          {quick.map((q) => (
            <button key={q.label + q.text} onClick={() => submit(q.text)} disabled={disabled || busy} title={q.text}
              className="rounded-full border border-slate-200 bg-white px-2.5 py-1 text-xs text-slate-600 hover:border-brand-500 hover:text-brand-700 disabled:opacity-50">
              {q.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export function NoCall({ title, hint, icon }: { title: string; hint: string; icon: ReactNode }) {
  return <EmptyState title={title} hint={hint} icon={icon} />;
}
