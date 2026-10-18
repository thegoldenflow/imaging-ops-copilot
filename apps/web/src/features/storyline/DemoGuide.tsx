// Demo mode's guide (spec 8.2, WP9): a presenter's panel that stays on screen across every page while the storyline
// is presented, one step at a time.
//
// 1. Introduction: what the step shows, who plays it and the sentence to say. Start switches to that person, opens
//    their screen and, when the step waits for hospital time, moves the clock.
// 2. Your turn: the clicks to make on that screen, the caller's lines as "Say it" buttons, the clock and the AI's
//    progress, and whose signature the step waits for. The guide never signs: the presenter clicks the real button.
// 3. Done: the step's trail (what the AI proposed, who signed, the audit events) and the question to the audience,
//    "Continue to the next step?", in the panel so the screen stays usable. Not yet pauses the guide until resumed.

import { useMutation, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import {
  ArrowRight, CheckCircle2, ChevronDown, ChevronUp, FastForward, Loader2, Map, Mic, Pause, Play, Presentation, Quote,
  RotateCcw, ShieldCheck, Sparkles, UserRound, X,
} from "lucide-react";
import { useState, type ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { AiBadge, Badge, Button } from "../../components/ui";
import { useAuth } from "../../lib/auth";
import { ROLE_LABEL } from "../../lib/types";
import { advanceStoryline, hhmm, resetStoryline, storylineKey, useStoryline, type Storyline, type StoryStep } from "./api";
import { exitDemo, sayAsCaller, setDemo, useCallerListening, useDemoMode } from "./demo";

export function DemoGuide() {
  const demo = useDemoMode();
  const { user } = useAuth();
  const q = useStoryline(demo.active && !!user);
  if (!demo.active || !user) return null;
  const data = q.data;
  if (!data) {
    return <Panel title="Demo mode"><p className="flex items-center gap-2 text-sm text-slate-600"><Loader2 className="size-4 animate-spin" />Loading the storyline…</p></Panel>;
  }
  if (!data.prepared) return <NotSetUp />;

  const started = demo.started ? data.steps[demo.started - 1] : undefined;
  if (started?.done) {
    return demo.paused ? <PausedPill step={started} /> : <AskNext step={started} data={data} />;
  }
  if (data.current == null) return <Complete data={data} />;
  const current = data.steps[data.current - 1];
  return demo.started === current.n ? <YourTurn step={current} data={data} /> : <Intro step={current} data={data} />;
}

/** The floating panel, bottom right; it can be folded to its title bar so it never hides the button to press. */
function Panel({ title, badges, children, testId }: { title: ReactNode; badges?: ReactNode; children: ReactNode; testId?: string }) {
  const [folded, setFolded] = useState(false);
  return (
    <section className="fixed inset-x-2 bottom-2 z-40 flex max-h-[50vh] flex-col sm:max-h-[75vh] overflow-hidden rounded-xl border border-brand-200 bg-white shadow-2xl sm:inset-x-auto sm:right-4 sm:bottom-4 sm:w-[24rem]"
      data-testid="demo-guide" aria-label="Demo guide">
      <header className="flex items-center gap-2 border-b border-slate-100 bg-brand-50/70 px-3 py-2">
        <Presentation className="size-4 shrink-0 text-brand-700" />
        <div className="min-w-0 flex-1">
          <p className="truncate text-xs font-semibold text-brand-800">{title}</p>
          {badges && <div className="mt-0.5 flex flex-wrap items-center gap-1">{badges}</div>}
        </div>
        <button type="button" onClick={() => setFolded(!folded)} className="rounded p-1 text-slate-500 hover:bg-white"
          aria-label={folded ? "Unfold the guide" : "Fold the guide"} data-testid="demo-fold">
          {folded ? <ChevronUp className="size-4" /> : <ChevronDown className="size-4" />}
        </button>
        <button type="button" onClick={exitDemo} className="rounded p-1 text-slate-500 hover:bg-white" aria-label="Exit demo mode" data-testid="demo-exit">
          <X className="size-4" />
        </button>
      </header>
      {!folded && <div className="space-y-3 overflow-y-auto p-3 text-sm" data-testid={testId}>{children}</div>}
    </section>
  );
}

function StepBadges({ step, data }: { step: StoryStep; data: Storyline }) {
  return (
    <>
      <Badge tone="brand">{step.module}</Badge>
      {step.stub && <Badge tone="slate"><Map className="size-3" />Roadmap (8.1): preset data</Badge>}
      <span className="text-[11px] text-slate-500">{data.steps.filter((s) => s.done).length} of {data.steps.length} done</span>
    </>
  );
}

function Cast({ step }: { step: StoryStep }) {
  if (!step.cast) return null;
  return (
    <p className="flex items-center gap-1.5 text-xs text-slate-600">
      <UserRound className="size-3.5" />Played by <span className="font-medium text-slate-800">{step.cast.name}</span> · {ROLE_LABEL[step.cast.role] ?? step.cast.role}
    </p>
  );
}

/** Switch to the step's person (a demo login) and open their screen. */
function useOpenStep() {
  const { user, login } = useAuth();
  const navigate = useNavigate();
  return async (step: StoryStep) => {
    if (step.cast && user?.id !== step.cast.id) await login(step.cast.id);
    navigate(step.route);
  };
}

function useAdvance() {
  const queryClient = useQueryClient();
  return useMutation({ mutationFn: advanceStoryline, onSuccess: () => queryClient.invalidateQueries({ queryKey: storylineKey }) });
}

// ----- 1. the introduction -----

function Intro({ step, data }: { step: StoryStep; data: Storyline }) {
  const open = useOpenStep();
  const advance = useAdvance();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const jobRunning = data.job?.status === "running";
  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      await open(step);
      setDemo({ started: step.n, paused: false });
      if (step.status === "waiting" && !jobRunning) advance.mutate();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Panel title={`Demo · step ${step.n} of ${data.steps.length}`} badges={<StepBadges step={step} data={data} />} testId={`demo-intro-${step.n}`}>
      <p className="text-base font-semibold text-slate-900">{step.title}</p>
      <Cast step={step} />
      <blockquote className="flex gap-2 rounded-lg bg-brand-50/60 p-2.5 text-slate-800">
        <Quote className="mt-0.5 size-4 shrink-0 text-brand-600" />
        <span><span className="block text-[11px] font-semibold tracking-wide text-brand-700 uppercase">Say</span>{step.say}</span>
      </blockquote>
      <div>
        <p className="text-[11px] font-semibold tracking-wide text-slate-500 uppercase">The audience will see</p>
        <ul className="mt-1 list-disc space-y-0.5 pl-5 text-xs text-slate-700">{step.expect.map((e) => <li key={e}>{e}</li>)}</ul>
      </div>
      {step.status === "waiting" && (
        <p className="flex items-center gap-1.5 rounded-lg bg-ai-50 p-2 text-xs text-ai-800">
          <FastForward className="size-3.5 shrink-0" />Starting also moves the hospital clock: {step.beat_hint}.
        </p>
      )}
      {error && <p className="text-xs text-rose-600" role="alert">{error}</p>}
      <div className="flex items-center gap-2">
        <Button variant="primary" onClick={start} loading={busy} data-testid="demo-start">
          <Play className="size-4" />Start step {step.n}
        </Button>
        <Button variant="ghost" size="sm" onClick={exitDemo}>Exit demo</Button>
      </div>
    </Panel>
  );
}

// ----- 2. the presenter's turn -----

function YourTurn({ step, data }: { step: StoryStep; data: Storyline }) {
  const { user } = useAuth();
  const location = useLocation();
  const open = useOpenStep();
  const advance = useAdvance();
  const [showSay, setShowSay] = useState(false);
  const job = data.job?.status === "running" ? data.job : null;
  const lines = step.key === "call" ? data.patient?.call_lines : step.key === "followup" ? data.patient?.followup_lines : undefined;
  const elsewhere = location.pathname !== step.route || (step.cast && user?.id !== step.cast.id);
  const aiWorking = step.step_ai?.items.some((i) => i.ai.state === "running" || i.ai.state === "queued");
  const canSay = useCallerListening();
  return (
    <Panel title={`Demo · step ${step.n} of ${data.steps.length} · your turn`} badges={<StepBadges step={step} data={data} />} testId={`demo-doing-${step.n}`}>
      <p className="font-semibold text-slate-900">{step.title}</p>
      {elsewhere && (
        <Button size="sm" variant="secondary" onClick={() => void open(step)} data-testid="demo-open">
          <ArrowRight className="size-3.5" />Back to {step.cast ? `${step.cast.name}'s ` : ""}{step.route}
        </Button>
      )}
      {job && (
        <div data-testid="demo-clock">
          <p className="flex items-center gap-1.5 text-xs text-ai-800"><Loader2 className="size-3.5 animate-spin" />Moving the hospital clock to {hhmm(job.target)}…</p>
          <div className="mt-1 h-1.5 rounded bg-slate-100"><div className="h-1.5 rounded bg-ai-600" style={{ width: `${Math.round(job.progress * 100)}%` }} /></div>
        </div>
      )}
      {!job && step.status === "waiting" && (
        <div className="space-y-1">
          <Button size="sm" variant="ai" onClick={() => advance.mutate()} loading={advance.isPending} data-testid="demo-advance">
            <FastForward className="size-3.5" />Advance the clock: {step.beat_hint}
          </Button>
          {step.advance_ai && <p className="text-[11px] text-ai-700">Then {step.advance_ai}, as soon as the clock gets there.</p>}
          {advance.error && <p className="text-xs text-rose-600" role="alert">{(advance.error as Error).message}</p>}
        </div>
      )}
      <div>
        <p className="text-[11px] font-semibold tracking-wide text-slate-500 uppercase">Now click</p>
        <ol className="mt-1 list-decimal space-y-0.5 pl-5 text-xs text-slate-800" data-testid="demo-clicks">
          {step.click_path.map((c) => <li key={c}>{c}</li>)}
        </ol>
      </div>
      {lines && (
        <div className="rounded-lg border border-dashed border-slate-300 p-2">
          <p className="mb-1 text-[11px] font-semibold text-slate-500" data-testid="demo-say-hint">
            The caller says{canSay ? "" : " (start the call first; wait while the agent answers)"}
          </p>
          <ul className="space-y-1.5">
            {lines.map((l, i) => (
              <li key={l} className="flex items-start gap-2">
                <span className="flex-1 text-xs text-slate-800" lang="zh-CN">{l}</span>
                <Button size="sm" variant="secondary" className="h-7 shrink-0 px-2" onClick={() => sayAsCaller(l)} disabled={!canSay} data-testid={`demo-say-${i}`}>
                  <Mic className="size-3" />Say it
                </Button>
              </li>
            ))}
          </ul>
        </div>
      )}
      {aiWorking && <p className="flex items-center gap-1.5 text-xs text-ai-700"><Sparkles className="size-3.5" />The AI is working on this step…</p>}
      <p className="flex items-center gap-1.5 rounded-lg bg-slate-50 p-2 text-xs text-slate-700" data-testid="demo-waiting">
        <Loader2 className="size-3.5 shrink-0 animate-spin text-slate-400" />
        {step.status === "waiting" ? "Waiting for the hospital clock" : `Waiting for the ${step.signoff}: the guide never signs, you click the button on the screen.`}
      </p>
      <button type="button" onClick={() => setShowSay(!showSay)} className="text-xs text-brand-700 hover:underline">
        {showSay ? "Hide what to say" : "What to say"}
      </button>
      {showSay && <p className="rounded-lg bg-brand-50/60 p-2 text-xs text-slate-800">{step.say}</p>}
    </Panel>
  );
}

// ----- 3. done: ask the audience -----

function Trail({ step }: { step: StoryStep }) {
  const p = step.proposal;
  const s = step.signed;
  return (
    <ul className="space-y-1.5 text-xs" data-testid="demo-trail">
      <li className="flex gap-2">
        <Sparkles className="mt-0.5 size-3.5 shrink-0 text-ai-600" />
        <span><span className="font-semibold text-slate-700">AI proposed: </span>{p ? <>{p.label} {p.agent && <AiBadge label={p.agent} agent={p.agent} />}</> : <span className="text-slate-500">{step.ai}</span>}</span>
      </li>
      <li className="flex gap-2">
        <UserRound className="mt-0.5 size-3.5 shrink-0 text-slate-600" />
        <span><span className="font-semibold text-slate-700">A person signed: </span>
          {s ? `${s.decision ?? s.status ?? "signed"} by ${s.by && s.by === step.cast?.id ? step.cast.name : s.by ?? "?"}${s.at ? ` at ${hhmm(s.at)}` : ""}` : <span className="text-slate-500">recorded by the EHR</span>}</span>
      </li>
      <li className="flex gap-2">
        <ShieldCheck className="mt-0.5 size-3.5 shrink-0 text-slate-600" />
        <span><span className="font-semibold text-slate-700">Audit trail: </span>{step.audit.length ? `${step.audit.length} event${step.audit.length === 1 ? "" : "s"} (${[...new Set(step.audit.map((a) => a.event_type))].join(", ")})` : "recorded by the EHR"}</span>
      </li>
    </ul>
  );
}

function AskNext({ step, data }: { step: StoryStep; data: Storyline }) {
  // In the panel, not a modal: the screen behind stays usable (end the call, show the result) while the question is up.
  const next = data.current != null ? data.steps[data.current - 1] : null;
  return (
    <Panel title={`Demo · step ${step.n} of ${data.steps.length} done`} badges={<StepBadges step={step} data={data} />} testId={`demo-ask-${step.n}`}>
      <p className="flex items-center gap-2 font-semibold text-slate-900"><CheckCircle2 className="size-5 shrink-0 text-emerald-600" />{step.title}</p>
      <div className="rounded-lg border border-slate-200 p-2.5"><Trail step={step} /></div>
      {next && (
        <p className="text-xs text-slate-500">Next · step {next.n}{next.cast ? `, played by ${next.cast.name}` : ""}:
          <span className="block text-sm font-medium text-slate-800">{next.title}</span></p>
      )}
      <div className="rounded-lg bg-brand-50 p-3" data-testid="demo-ask" role="group" aria-labelledby="demo-ask-title">
        <p id="demo-ask-title" className="font-semibold text-slate-900">{next ? "Continue to the next step?" : "That was the last step. See the whole story?"}</p>
        <div className="mt-2 flex gap-2">
          <Button variant="primary" onClick={() => setDemo({ started: null, paused: false })} data-testid="demo-continue">
            {next ? "Yes, continue" : "Yes, show the summary"}<ArrowRight className="size-4" />
          </Button>
          <Button variant="secondary" onClick={() => setDemo({ paused: true })} data-testid="demo-not-yet">
            <Pause className="size-4" />Not yet
          </Button>
        </div>
      </div>
    </Panel>
  );
}

function PausedPill({ step }: { step: StoryStep }) {
  return (
    <div className="fixed right-4 bottom-4 z-40 flex items-center gap-2 rounded-full border border-brand-200 bg-white py-1.5 pr-1.5 pl-3 text-xs shadow-lg" data-testid="demo-paused">
      <Presentation className="size-3.5 text-brand-700" />
      <span className="text-slate-700">Demo paused after step {step.n}</span>
      <Button size="sm" variant="primary" className="h-7 rounded-full" onClick={() => setDemo({ started: null, paused: false })} data-testid="demo-resume">
        <Play className="size-3.5" />Resume
      </Button>
      <button type="button" onClick={exitDemo} className="rounded-full p-1 text-slate-400 hover:bg-slate-100" aria-label="Exit demo mode"><X className="size-3.5" /></button>
    </div>
  );
}

// ----- before and after -----

function NotSetUp() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const reset = useMutation({
    mutationFn: resetStoryline,
    onSuccess: async () => {
      setDemo({ started: null, paused: false });
      await queryClient.invalidateQueries();
      navigate("/storyline");
    },
  });
  return (
    <Panel title="Demo mode" testId="demo-not-set-up">
      <p className="text-slate-700">The storyline is not set up. Reset it to the start: the demo data is rebuilt with her preset, the clock at 07:00 (about a minute).</p>
      {reset.isPending && <p className="text-xs text-slate-500">Rebuilding the demo data…</p>}
      {reset.error && <p className="text-xs text-rose-600" role="alert">{(reset.error as Error).message}</p>}
      <div className="flex gap-2">
        <Button variant="primary" onClick={() => reset.mutate()} loading={reset.isPending} data-testid="demo-reset"><RotateCcw className="size-4" />Reset to the start</Button>
        <Button variant="ghost" size="sm" onClick={exitDemo}>Exit demo</Button>
      </div>
    </Panel>
  );
}

function Complete({ data }: { data: Storyline }) {
  const navigate = useNavigate();
  const proposals = data.steps.filter((s) => s.proposal).length;
  const signed = data.steps.filter((s) => s.signed).length;
  const audit = data.steps.reduce((n, s) => n + s.audit.length, 0);
  return (
    <Panel title="Demo · the story is complete" testId="demo-complete">
      <p className="flex items-center gap-2 font-semibold text-slate-900"><CheckCircle2 className="size-5 text-emerald-600" />All {data.steps.length} steps are done</p>
      <ul className={clsx("space-y-1 text-xs text-slate-700")}>
        <li><Sparkles className="mr-1 inline size-3.5 text-ai-600" />{proposals} AI proposals, each a suggestion or a draft</li>
        <li><UserRound className="mr-1 inline size-3.5" />{signed} decisions signed by a named person</li>
        <li><ShieldCheck className="mr-1 inline size-3.5" />{audit} audit events recording both</li>
      </ul>
      <div className="flex gap-2">
        <Button variant="primary" size="sm" onClick={() => navigate("/storyline")} data-testid="demo-summary">See the whole trail</Button>
        <Button variant="ghost" size="sm" onClick={exitDemo}>Exit demo</Button>
      </div>
    </Panel>
  );
}
