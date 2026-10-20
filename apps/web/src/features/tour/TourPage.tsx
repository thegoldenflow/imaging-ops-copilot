// The recorded tour (demo/tour.md): screenshots of the running app, one pulsing hotspot and a caption per step, a role
// card per chapter, an end screen that leads to the live app. No sign-in and no /api request: the manifest and the
// images are static files (public/tour/), captured by scripts/tour/capture.mjs.

import clsx from "clsx";
import { ArrowLeft, ArrowRight, Maximize2, Minimize2, ScanLine, X } from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";
import { imageUrl, isTrack, useManifest, type Box, type Manifest, type TrackId } from "./manifest";

type Frame =
  | { kind: "chapter"; chapter: number; step: number } // the chapter's role card, over its first step's screen
  | { kind: "step"; chapter: number; step: number }
  | { kind: "end" };

function framesOf(m: Manifest): Frame[] {
  const frames: Frame[] = [];
  m.chapters.forEach((c, chapter) => {
    const steps = m.steps.map((s, i) => [s, i] as const).filter(([s]) => s.chapter === c.id);
    if (!steps.length) return;
    frames.push({ kind: "chapter", chapter, step: steps[0][1] });
    for (const [, step] of steps) frames.push({ kind: "step", chapter, step });
  });
  frames.push({ kind: "end" });
  return frames;
}

const clamp = (v: number, lo: number, hi: number) => Math.min(Math.max(v, lo), hi);

/**
 * Where the screenshot goes in the stage. It fits the stage; when that makes it too small to read (a phone), it is
 * zoomed towards the hotspot (up to 80 %) and panned so the hotspot is in the middle. `whole` shows it all anyway.
 */
function place(stage: { w: number; h: number }, img: { width: number; height: number }, box: Box, whole: boolean) {
  const fit = Math.min(stage.w / img.width, stage.h / img.height);
  const focus = Math.min((stage.w * 0.92) / box.width, (stage.h * 0.7) / box.height);
  const scale = whole || fit >= 0.55 ? fit : Math.max(fit, Math.min(focus, 0.8));
  const dw = img.width * scale;
  const dh = img.height * scale;
  const cx = (box.x + box.width / 2) * scale;
  const cy = (box.y + box.height / 2) * scale;
  const ox = dw <= stage.w ? (stage.w - dw) / 2 : clamp(stage.w / 2 - cx, stage.w - dw, 0);
  const oy = dh <= stage.h ? (stage.h - dh) / 2 : clamp(stage.h / 2 - cy, stage.h - dh, 0);
  return {
    scale, ox, oy, dw, dh, zoomed: scale > fit + 0.001, canZoom: fit < 0.55,
    spot: { x: ox + box.x * scale, y: oy + box.y * scale, w: box.width * scale, h: box.height * scale },
  };
}

/** The caption beside the hotspot (wide screens): below it, above it, to a side, or at the bottom of the stage. */
function calloutStyle(stage: { w: number; h: number }, s: { x: number; y: number; w: number; h: number }): CSSProperties {
  const width = Math.min(360, stage.w - 24);
  const gap = 14;
  const left = clamp(s.x + s.w / 2 - width / 2, 12, stage.w - width - 12);
  if (stage.h - (s.y + s.h) >= 190) return { left, top: s.y + s.h + gap, width };
  if (s.y >= 190) return { left, bottom: stage.h - s.y + gap, width };
  const top = clamp(s.y, 12, Math.max(12, stage.h - 230));
  if (stage.w - (s.x + s.w) >= width + 2 * gap) return { left: s.x + s.w + gap, top, width };
  if (s.x >= width + 2 * gap) return { left: s.x - width - gap, top, width };
  return { left, bottom: 12, width };
}

function useStageSize() {
  const ref = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => setSize({ w: el.clientWidth, h: el.clientHeight });
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, size] as const;
}

function useWide() {
  const query = "(min-width: 768px)";
  const [wide, setWide] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const on = () => setWide(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return wide;
}

export function TourPage() {
  const { track } = useParams();
  if (!isTrack(track)) return <Navigate to="/tour/manager" replace />;
  return <TourLoader key={track} track={track} />;
}

function TourLoader({ track }: { track: TrackId }) {
  const q = useManifest(track);
  if (q.isLoading) return <div className="grid h-full place-items-center bg-slate-950 text-sm text-slate-400">Loading the tour…</div>;
  if (q.error || !q.data) {
    return (
      <div className="grid h-full place-items-center bg-slate-950 px-4 text-center text-slate-200">
        <div>
          <p className="text-sm">{q.error instanceof Error ? q.error.message : "The tour could not be loaded"}.</p>
          <Link to="/login" className="mt-3 inline-block text-sm font-medium text-brand-100 underline">Explore the live app instead</Link>
        </div>
      </div>
    );
  }
  return <TourView m={q.data} />;
}

function TourView({ m }: { m: Manifest }) {
  const navigate = useNavigate();
  const frames = useMemo(() => framesOf(m), [m]);
  const [at, setAt] = useState(0);
  const [whole, setWhole] = useState(false);
  const [stageRef, stage] = useStageSize();
  const wide = useWide();
  const frame = frames[at];
  const step = frame.kind === "end" ? null : m.steps[frame.step];
  const chapter = frame.kind === "end" ? null : m.chapters[frame.chapter];
  const stepNo = frame.kind === "step" ? frame.step + 1 : frame.kind === "end" ? m.steps.length : frame.step;

  const next = useCallback(() => setAt((i) => Math.min(i + 1, frames.length - 1)), [frames.length]);
  const back = useCallback(() => setAt((i) => Math.max(i - 1, 0)), []);
  useEffect(() => setWhole(false), [at]);

  useEffect(() => {
    document.title = `${m.audience} tour · Imaging Ops Copilot`;
    return () => { document.title = "Imaging Ops Copilot"; };
  }, [m.audience]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.altKey || e.ctrlKey || e.metaKey) return;
      if (e.key === "ArrowRight") { e.preventDefault(); next(); }
      else if (e.key === "ArrowLeft") { e.preventDefault(); back(); }
      else if (e.key === "Escape") navigate("/");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [next, back, navigate]);

  useEffect(() => { // the next screens load while this one is read
    for (const f of frames.slice(at + 1, at + 3)) if (f.kind !== "end") new Image().src = imageUrl(m.steps[f.step].image);
  }, [at, frames, m.steps]);

  const progress = frame.kind === "end"
    ? `Done · ${m.steps.length}/${m.steps.length}`
    : `Chapter ${frame.chapter + 1} of ${m.chapters.length}${frame.kind === "step" ? ` · ${stepNo}/${m.steps.length}` : ""}`;
  const pos = step && stage.w > 0 ? place(stage, m.viewport, step.box, whole) : null;
  const caption = frame.kind === "step" && step ? (
    <>
      <p className="text-[11px] font-semibold tracking-wide text-brand-700 uppercase">{step.screen}</p>
      <p className="mt-1 text-[15px] leading-snug text-slate-900" data-testid="tour-caption">{step.caption}</p>
      {step.note && <p className="mt-2 rounded-md bg-amber-50 px-2 py-1 text-xs text-amber-900 ring-1 ring-amber-200 ring-inset" data-testid="tour-note">{step.note}</p>}
      {at === 1 && <p className="mt-2 text-xs text-slate-500">Click the highlighted area, press Next or use the arrow keys.</p>}
    </>
  ) : null;

  const footnote = (
    <p className="min-w-0 flex-1 text-center text-[11px] text-slate-400" data-testid="tour-footer">
      Captured from the running app (version {m.captured.version}, {m.captured.at}); all data synthetic
    </p>
  );

  return (
    <div className="flex h-dvh flex-col bg-slate-950 text-slate-100" data-testid="tour" data-track={m.track}>
      <header className="flex h-12 shrink-0 items-center gap-3 border-b border-white/10 px-3 sm:px-4">
        <Link to="/" className="flex min-w-0 items-center gap-2" aria-label="Imaging Ops Copilot: back to the start">
          <span className="grid size-7 shrink-0 place-items-center rounded-lg bg-brand-600 text-white"><ScanLine className="size-4" /></span>
          <span className="hidden truncate text-sm font-semibold sm:inline">Imaging Ops Copilot</span>
        </Link>
        <span className="truncate text-sm text-slate-300">{m.audience} tour</span>
        <span className="ml-auto shrink-0 text-xs text-slate-300 tabular" data-testid="tour-progress" aria-live="polite">{progress}</span>
        <Link to="/" className="rounded-md p-1.5 text-slate-400 hover:bg-white/10 hover:text-white" aria-label="Leave the tour" data-testid="tour-close">
          <X className="size-4" />
        </Link>
      </header>
      <div className="h-0.5 shrink-0 bg-white/10" aria-hidden>
        <div className="h-full bg-amber-400 transition-[width] duration-300" style={{ width: `${(stepNo / m.steps.length) * 100}%` }} />
      </div>

      <div ref={stageRef} className="relative min-h-0 flex-1 overflow-hidden" data-testid="tour-stage">
        {step && pos && (
          <img
            key={step.image}
            src={imageUrl(step.image)}
            alt={`${step.screen} (screenshot of the app)`}
            draggable={false}
            className="absolute max-w-none select-none"
            style={{ left: pos.ox, top: pos.oy, width: pos.dw, height: pos.dh }}
            data-testid="tour-image"
          />
        )}
        {frame.kind === "step" && pos && (
          <>
            <div className="pointer-events-none absolute rounded-lg" aria-hidden
              style={{ left: pos.spot.x, top: pos.spot.y, width: pos.spot.w, height: pos.spot.h, boxShadow: "0 0 0 100vmax rgba(2, 6, 23, 0.38)" }} />
            <button type="button" onClick={next} aria-label="Next step"
              className="tour-pulse absolute rounded-lg border-2 border-amber-400 outline-none focus-visible:ring-4 focus-visible:ring-amber-200"
              style={{ left: pos.spot.x - 3, top: pos.spot.y - 3, width: pos.spot.w + 6, height: pos.spot.h + 6 }}
              data-testid="tour-hotspot">
              <span className="absolute -top-1.5 -right-1.5 flex size-3" aria-hidden>
                <span className="absolute inline-flex size-full rounded-full bg-amber-400 opacity-75 motion-safe:animate-ping" />
                <span className="relative inline-flex size-3 rounded-full bg-amber-400" />
              </span>
            </button>
            {wide && <div className="absolute rounded-xl bg-white p-4 shadow-2xl ring-1 ring-black/5" style={calloutStyle(stage, pos.spot)}
              aria-live="polite" data-testid="tour-callout">{caption}</div>}
            {pos.canZoom && (
              <button type="button" onClick={() => setWhole((w) => !w)}
                className="absolute top-2 right-2 inline-flex items-center gap-1 rounded-md bg-slate-900/80 px-2 py-1 text-[11px] text-slate-100 ring-1 ring-white/20"
                data-testid="tour-whole">
                {whole ? <><Minimize2 className="size-3" />Zoom in</> : <><Maximize2 className="size-3" />Whole screen</>}
              </button>
            )}
          </>
        )}
        {frame.kind === "chapter" && chapter && (
          <div className="absolute inset-0 grid place-items-center overflow-y-auto bg-slate-950/75 p-4">
            <RoleCard m={m} chapterIndex={frame.chapter} onStart={next} />
          </div>
        )}
        {frame.kind === "end" && (
          <div className="absolute inset-0 grid place-items-center overflow-y-auto p-4">
            <EndCard m={m} />
          </div>
        )}
      </div>

      <footer className="shrink-0 border-t border-white/10 bg-slate-950">
        {!wide && caption && <div className="max-h-[40vh] overflow-y-auto bg-white px-4 py-3" aria-live="polite" data-testid="tour-sheet">{caption}</div>}
        <div className="flex items-center gap-2 px-3 py-2 sm:px-4">
          <button type="button" onClick={back} disabled={at === 0}
            className="inline-flex h-9 items-center gap-1 rounded-lg px-3 text-sm text-slate-200 hover:bg-white/10 disabled:opacity-30" data-testid="tour-back">
            <ArrowLeft className="size-4" />Back
          </button>
          {wide ? footnote : <span className="flex-1" />}
          <button type="button" onClick={next} disabled={frame.kind === "end"}
            className={clsx("inline-flex h-9 items-center gap-1 rounded-lg px-4 text-sm font-medium disabled:opacity-30",
              "bg-amber-400 text-slate-950 hover:bg-amber-300")} data-testid="tour-next">
            Next<ArrowRight className="size-4" />
          </button>
        </div>
        {!wide && <div className="px-3 pb-2">{footnote}</div>}
      </footer>
    </div>
  );
}

function RoleCard({ m, chapterIndex, onStart }: { m: Manifest; chapterIndex: number; onStart: () => void }) {
  const c = m.chapters[chapterIndex];
  const initials = c.role.name.replace(/^Dr\.\s*/, "").split(/\s+/).map((w) => w[0]).join("").slice(0, 2);
  return (
    <div className="w-full max-w-md rounded-2xl bg-white p-5 text-slate-800 shadow-2xl sm:p-6" data-testid="tour-role-card">
      <p className="text-xs font-semibold tracking-wide text-brand-700 uppercase">Chapter {chapterIndex + 1} of {m.chapters.length}</p>
      <h2 className="mt-1 text-xl font-semibold text-slate-900">{c.title}</h2>
      {chapterIndex === 0 && <p className="mt-2 text-sm text-slate-600">{m.intro}</p>}
      {c.meanwhile && (
        <p className="mt-2 text-sm text-slate-600"><span className="font-medium text-slate-800">Meanwhile. </span>{c.meanwhile}</p>
      )}
      <div className="mt-4 flex gap-3 rounded-xl border border-brand-100 bg-brand-50/60 p-3">
        <span className="grid size-10 shrink-0 place-items-center rounded-full bg-brand-600 text-sm font-semibold text-white" aria-hidden>{initials}</span>
        <div className="text-sm">
          <p className="text-slate-900">You are <b>{c.role.name}</b>, {c.role.title}.</p>
          <p className="mt-0.5 text-slate-600">{c.role.text}</p>
        </div>
      </div>
      <button type="button" onClick={onStart}
        className="mt-5 inline-flex h-10 w-full items-center justify-center gap-1.5 rounded-lg bg-brand-600 text-sm font-medium text-white hover:bg-brand-700"
        data-testid="tour-start-chapter" autoFocus>
        {chapterIndex === 0 ? "Start the tour" : "Continue"}<ArrowRight className="size-4" />
      </button>
    </div>
  );
}

function EndCard({ m }: { m: Manifest }) {
  return (
    <div className="w-full max-w-lg rounded-2xl bg-white p-6 text-slate-800 shadow-2xl" data-testid="tour-end">
      <h2 className="text-xl font-semibold text-slate-900">{m.end.title}</h2>
      <p className="mt-2 text-sm text-slate-600">{m.end.text}</p>
      <div className="mt-5 grid gap-2 sm:grid-cols-2">
        <Link to="/login" className="inline-flex h-10 items-center justify-center gap-1.5 rounded-lg bg-brand-600 text-sm font-medium text-white hover:bg-brand-700"
          data-testid="tour-try-live">Try it live<ArrowRight className="size-4" /></Link>
        <Link to={`/tour/${m.other}`} className="inline-flex h-10 items-center justify-center rounded-lg border border-slate-300 text-sm font-medium text-slate-700 hover:bg-slate-50"
          data-testid="tour-other-track">{m.end.otherLabel}</Link>
      </div>
      <p className="mt-3 text-xs text-slate-500">
        The live app is one shared demo hospital: sign in as any role, and what you change, the next visitor sees.
      </p>
      <Link to="/" className="mt-3 inline-block text-xs font-medium text-brand-700 hover:underline">Back to the start</Link>
    </div>
  );
}
