// The hospital storyline (spec 8.2, WP9): one synthetic patient through the whole hospital in twelve steps, about
// ten minutes. Every step says who plays it (one click switches to that person), the click path, the sentence to
// say and what the screen should show, and then shows the live trail: what the AI proposed, who signed or approved
// it and the audit events of those records. The clock moves only when a step needs time to pass (Advance).

import { useMutation, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import {
  ArrowRight, CheckCircle2, ChevronDown, ChevronRight, Clock3, Copy, FastForward, Map, Quote, RotateCcw, ShieldCheck,
  Sparkles, UserRound, Workflow,
} from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { AiBadge, Badge, Button, Card, EmptyState, ErrorState, Loading, PageHeader } from "../../components/ui";
import { useAuth } from "../../lib/auth";
import { ROLE_LABEL } from "../../lib/types";
import { advanceStoryline, clock, hhmm, resetStoryline, storylineKey, useStoryline, type Storyline, type StoryStep } from "./api";

const STATUS = {
  done: { label: "Done", tone: "green" as const },
  active: { label: "Now", tone: "brand" as const },
  waiting: { label: "Waiting for the clock", tone: "amber" as const },
  pending: { label: "Next", tone: "slate" as const },
};

export function StorylinePage() {
  const q = useStoryline();
  const queryClient = useQueryClient();
  const reset = useMutation({
    mutationFn: resetStoryline,
    onSuccess: () => queryClient.invalidateQueries(),
  });
  const onReset = () => {
    if (confirm("Reset the demo data and start the storyline at 07:00?")) reset.mutate();
  };
  return (
    <div className="mx-auto max-w-5xl" data-testid="storyline">
      <PageHeader
        title="Hospital storyline"
        subtitle="One synthetic patient through the hospital in twelve steps, about ten minutes. Each step: the AI proposes, a person signs, the audit trail records it."
        actions={<Button variant="secondary" onClick={onReset} loading={reset.isPending} data-testid="story-reset"><RotateCcw className="size-4" />Reset to the start</Button>}
      />
      {reset.isPending && <p className="mb-3 text-sm text-slate-600">Rebuilding the demo data and the storyline's preset (about a minute)…</p>}
      {reset.error && <p className="mb-3 text-sm text-rose-600" role="alert">{(reset.error as Error).message}</p>}
      {q.isLoading ? <Loading /> : q.error ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : q.data && (
        <Story data={q.data} />
      )}
    </div>
  );
}

function Story({ data }: { data: Storyline }) {
  if (!data.prepared) {
    return (
      <Card>
        <EmptyState title="The storyline is not set up" hint="Press “Reset to the start”: the demo data is rebuilt with the storyline's patient, Orthopedics and Surgery full, and the clock at 07:00." />
      </Card>
    );
  }
  const done = data.steps.filter((s) => s.done).length;
  return (
    <>
      <div className="mb-4 grid gap-3 md:grid-cols-3">
        <PatientCard data={data} />
        <ClockCard data={data} done={done} />
        <YouCard data={data} />
      </div>
      {!!data.capacity?.warnings?.length && (
        <p className="mb-3 rounded-lg bg-amber-50 p-2 text-xs text-amber-800">Preset note: {data.capacity.warnings.join("; ")}</p>
      )}
      <ol className="space-y-2.5">
        {data.steps.map((s) => <StepCard key={s.n} step={s} data={data} />)}
      </ol>
    </>
  );
}

function PatientCard({ data }: { data: Storyline }) {
  const p = data.patient!;
  return (
    <Card title="The patient">
      <p className="text-sm font-semibold text-slate-900">{p.name_zh} {p.name} · {p.age} · MRN {p.mrn}</p>
      <p className="mt-1 text-xs text-slate-600">Mandarin-speaking; atrial fibrillation on warfarin, hypertension. Falls at home: hip fracture.</p>
      <p className="mt-1 text-xs text-slate-500">
        Imaging-centre record {p.imaging_record} <Badge tone="slate"><Map className="size-3" />MPI link preset (8.1)</Badge>
      </p>
    </Card>
  );
}

function ClockCard({ data, done }: { data: Storyline; done: number }) {
  const t = data.temporal;
  const online = t.configured && t.status.online;
  return (
    <Card title="Hospital time">
      <p className="text-lg font-semibold tabular-nums text-slate-900" data-testid="story-clock">{clock(data.now)}</p>
      <p className="mt-1 text-xs text-slate-600">{done} of {data.steps.length} steps done · budget {Math.round(data.total_seconds / 60)} min</p>
      <p className="mt-1 flex flex-wrap items-center gap-1.5 text-xs" data-testid="story-temporal">
        <Workflow className="size-3.5 text-slate-500" />
        {online ? <Badge tone="green">Journey workflow: Temporal online ({t.status.workers} worker{t.status.workers === 1 ? "" : "s"})</Badge>
          : t.configured ? <Badge tone="amber">Temporal unreachable: the story runs step by step</Badge>
          : <Badge tone="slate">Temporal off: the story runs step by step (offline mode)</Badge>}
        {data.journey && <Link to={`/workflows/${encodeURIComponent(data.journey.id)}`} className="text-brand-700 hover:underline" data-testid="story-journey">{data.journey.id}: {data.journey.done}/{data.journey.total}</Link>}
      </p>
      {data.job && data.job.status === "running" && (
        <div className="mt-2" data-testid="story-job">
          <div className="h-1.5 rounded bg-slate-100"><div className="h-1.5 rounded bg-brand-600" style={{ width: `${Math.round(data.job.progress * 100)}%` }} /></div>
          <p className="mt-0.5 text-[11px] text-slate-500">Fast-forward to {hhmm(data.job.target)} · {data.job.events} events</p>
        </div>
      )}
    </Card>
  );
}

function YouCard({ data }: { data: Storyline }) {
  return (
    <Card title="You are">
      <p className="text-sm font-semibold text-slate-900" data-testid="you-name">{data.you.name}</p>
      <p className="text-xs text-slate-600" data-testid="you-role">{ROLE_LABEL[data.you.role] ?? data.you.role}{data.you.units.length ? ` · ${data.you.units.join(", ")}` : ""}</p>
      <p className="mt-1 text-xs text-slate-500">Each step names who plays it: switch with one click and watch what the role may see and do change.</p>
    </Card>
  );
}

function StepCard({ step, data }: { step: StoryStep; data: Storyline }) {
  const current = step.n === data.current;
  const [open, setOpen] = useState(current);
  useEffect(() => { if (current) setOpen(true); }, [current]);
  const status = STATUS[step.status];
  return (
    <li className={clsx("rounded-xl border bg-white", current ? "border-brand-300 shadow-sm" : "border-slate-200")}
      data-testid={`story-step-${step.n}`} data-status={step.status}>
      <button type="button" onClick={() => setOpen(!open)} className="flex w-full items-center gap-3 p-3 text-left">
        <span className={clsx("grid size-7 shrink-0 place-items-center rounded-full text-sm font-semibold",
          step.done ? "bg-emerald-100 text-emerald-700" : current ? "bg-brand-600 text-white" : "bg-slate-100 text-slate-600")}>
          {step.done ? <CheckCircle2 className="size-4" /> : step.n}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-semibold text-slate-900">{step.title}</span>
          <span className="mt-0.5 flex flex-wrap items-center gap-1.5 text-xs text-slate-500">
            <Badge tone="brand">{step.module}</Badge>
            {step.stub && <Badge tone="slate"><Map className="size-3" />Roadmap (8.1): preset data</Badge>}
            {step.cast && <span><UserRound className="mr-0.5 inline size-3" />{step.cast.name} · {ROLE_LABEL[step.cast.role] ?? step.cast.role}</span>}
            <span><Clock3 className="mr-0.5 inline size-3" />{step.seconds} s</span>
          </span>
        </span>
        <Badge tone={status.tone}>{status.label}</Badge>
        {open ? <ChevronDown className="size-4 text-slate-400" /> : <ChevronRight className="size-4 text-slate-400" />}
      </button>
      {open && <StepBody step={step} data={data} current={current} />}
    </li>
  );
}

function StepBody({ step, data, current }: { step: StoryStep; data: Storyline; current: boolean }) {
  const { user, login } = useAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const advance = useMutation({ mutationFn: advanceStoryline, onSuccess: () => queryClient.invalidateQueries({ queryKey: storylineKey }) });
  const isYou = user?.id === step.cast?.id;
  const go = async () => {
    if (step.cast && !isYou) await login(step.cast.id);
    navigate(step.route);
  };
  const lines = step.key === "call" ? data.patient?.call_lines : step.key === "followup" ? data.patient?.followup_lines : undefined;
  const jobRunning = data.job?.status === "running";
  return (
    <div className="space-y-3 border-t border-slate-100 p-3 pt-2.5 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        {step.cast && (
          <Button size="sm" variant={isYou ? "primary" : "secondary"} onClick={go} data-testid={`switch-${step.n}`}>
            {isYou ? <>Open {step.route}<ArrowRight className="size-3.5" /></> : <>Switch to {step.cast.name} ({ROLE_LABEL[step.cast.role] ?? step.cast.role}) and open {step.route}</>}
          </Button>
        )}
        {current && step.status === "waiting" && (
          <Button size="sm" variant="ai" onClick={() => advance.mutate()} loading={advance.isPending || jobRunning} disabled={jobRunning}
            data-testid="advance" title={step.beat_hint ?? undefined}>
            <FastForward className="size-3.5" />Advance the clock: {step.beat_hint}
          </Button>
        )}
        {advance.error && <span className="text-xs text-rose-600" role="alert">{(advance.error as Error).message}</span>}
      </div>
      <ol className="flex flex-wrap items-center gap-1 text-xs text-slate-600" aria-label="Click path">
        {step.click_path.map((c, i) => (
          <li key={c} className="flex items-center gap-1">
            {i > 0 && <ChevronRight className="size-3 text-slate-400" />}
            <span className="rounded bg-slate-100 px-1.5 py-0.5">{c}</span>
          </li>
        ))}
      </ol>
      <blockquote className="flex gap-2 rounded-lg bg-brand-50/60 p-2.5 text-slate-800" data-testid={`say-${step.n}`}>
        <Quote className="mt-0.5 size-4 shrink-0 text-brand-600" />
        <span>{step.say}</span>
      </blockquote>
      {lines && <Phrases lines={lines} />}
      <div className="grid gap-3 md:grid-cols-2">
        <div>
          <p className="text-xs font-semibold tracking-wide text-slate-500 uppercase">The screen should show</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-5 text-xs text-slate-700">{step.expect.map((e) => <li key={e}>{e}</li>)}</ul>
        </div>
        <div>
          <p className="flex items-center gap-1 text-xs font-semibold tracking-wide text-slate-500 uppercase"><ShieldCheck className="size-3.5" />Switching role shows</p>
          <p className="mt-1 text-xs text-slate-700">{step.permission}</p>
        </div>
      </div>
      <Trail step={step} />
    </div>
  );
}

function Phrases({ lines }: { lines: string[] }) {
  return (
    <div className="rounded-lg border border-dashed border-slate-300 p-2 text-xs">
      <p className="mb-1 font-semibold text-slate-500">What the caller says (type or paste in the call):</p>
      <ul className="space-y-1">
        {lines.map((l) => (
          <li key={l} className="flex items-center justify-between gap-2">
            <span className="text-slate-800" lang="zh-CN">{l}</span>
            <button type="button" className="inline-flex items-center gap-1 text-brand-700 hover:underline" onClick={() => void navigator.clipboard?.writeText(l)}>
              <Copy className="size-3" />Copy
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Trail({ step }: { step: StoryStep }) {
  const p = step.proposal;
  const s = step.signed;
  return (
    <div className="grid gap-2 md:grid-cols-3" aria-label="AI proposes, a person signs, audit trail">
      <div className="rounded-lg border border-ai-100 bg-ai-50/40 p-2" data-testid={`story-proposal-${step.n}`}>
        <p className="flex items-center gap-1 text-xs font-semibold text-ai-700"><Sparkles className="size-3.5" />1 · AI proposes</p>
        {p ? (
          <>
            <p className="mt-1 text-xs font-medium text-slate-800">{p.label}</p>
            <p className="mt-0.5 flex flex-wrap items-center gap-1 text-[11px] text-slate-500">
              {p.agent && <AiBadge label={p.agent} agent={p.agent} />}
              <span>{p.ref}</span>{p.at && <span>· {hhmm(p.at)}</span>}
            </p>
            {(p.recommendation || p.summary) && (
              <details className="mt-1 text-[11px] text-slate-600">
                <summary className="cursor-pointer text-brand-700">What it said</summary>
                <p className="mt-1 whitespace-pre-line">{p.recommendation || p.summary}</p>
              </details>
            )}
          </>
        ) : <p className="mt-1 text-xs text-slate-500">{step.ai}: not yet</p>}
      </div>
      <div className="rounded-lg border border-slate-200 p-2" data-testid={`story-signed-${step.n}`}>
        <p className="flex items-center gap-1 text-xs font-semibold text-slate-700"><UserRound className="size-3.5" />2 · A person signs</p>
        {s ? (
          <p className="mt-1 text-xs text-slate-800">
            {s.decision ?? s.status ?? "signed"} by {s.by ?? "?"}{s.at ? ` · ${hhmm(s.at)}` : ""}
            {s.signatures ? ` · ${s.signatures} signature${s.signatures === 1 ? "" : "s"}` : ""}
          </p>
        ) : <p className="mt-1 text-xs text-slate-500">Waiting for the {step.signoff}</p>}
        {step.detail && <p className="mt-1 text-[11px] text-slate-600">{step.detail}</p>}
        {step.written.length > 0 && (
          <p className="mt-1 text-[11px] text-slate-500">EHR: {step.written.join(", ")}</p>
        )}
      </div>
      <div className="rounded-lg border border-slate-200 p-2" data-testid={`story-audit-${step.n}`}>
        <p className="flex items-center gap-1 text-xs font-semibold text-slate-700"><ShieldCheck className="size-3.5" />3 · Audit trail</p>
        {step.audit.length ? (
          <ul className="mt-1 space-y-0.5 text-[11px] text-slate-600">
            {step.audit.map((a, i) => (
              <li key={i}><Badge tone={a.event_type === "sign" ? "green" : a.event_type === "approve" ? "blue" : "slate"}>{a.event_type}</Badge> {a.action} {a.resource} · {a.user} ({a.role}) · {hhmm(a.at)}</li>
            ))}
          </ul>
        ) : <p className="mt-1 text-xs text-slate-500">{step.done ? "Recorded by the EHR" : "Nothing yet"}</p>}
      </div>
    </div>
  );
}
