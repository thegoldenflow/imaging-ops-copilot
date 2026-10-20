// The first screen for a signed-out visitor (demo/tour.md): what this is, that the data is synthetic, and three ways
// in: the hospital manager's tour, the engineer's track, the live app (the sign-in page). Reads only the tours' static
// manifests (their length and a screenshot), never /api.

import { ArrowRight, Cpu, Hospital, LogIn, ScanLine } from "lucide-react";
import { Link } from "react-router-dom";
import { imageUrl, minutes, useManifest, type Manifest } from "./manifest";

const length = (m: Manifest | undefined) => (m?.steps.length ? `${m.steps.length} steps · about ${minutes(m)} min` : "about 3 min");

export function LandingPage() {
  const manager = useManifest("manager");
  const engineer = useManifest("engineer");
  const hero = manager.data?.steps.find((s) => s.id === "exception") ?? manager.data?.steps[0];
  return (
    <div className="min-h-full bg-gradient-to-b from-brand-50 to-slate-50 px-4 py-8 sm:py-12" data-testid="landing">
      <div className="mx-auto max-w-5xl">
        <div className="flex items-center gap-3">
          <div className="grid size-10 place-items-center rounded-xl bg-brand-600 text-white"><ScanLine className="size-5" /></div>
          <p className="text-lg font-semibold text-slate-900">Imaging Ops Copilot</p>
        </div>

        <div className="mt-8 grid items-center gap-8 lg:grid-cols-[1fr_1.1fr]">
          <div>
            <h1 className="text-2xl leading-tight font-semibold text-slate-900 sm:text-3xl">
              AI for a hospital's operations, where the AI suggests, a named person decides, and every step is audited.
            </h1>
            <p className="mt-3 text-sm text-slate-600 sm:text-base">
              A working demo: one synthetic patient through the emergency department, beds, surgery, discharge and a
              follow-up call. All data is synthetic; there is no real patient, clinician or hospital here.
            </p>

            <div className="mt-6 space-y-3">
              <Link to="/tour/manager" data-testid="landing-manager"
                className="flex items-center gap-3 rounded-xl bg-brand-600 p-4 text-white shadow-sm transition hover:bg-brand-700">
                <Hospital className="size-6 shrink-0" />
                <span className="min-w-0 flex-1">
                  <span className="block font-semibold">Take the tour</span>
                  <span className="block text-sm text-brand-50">Hospital manager · {length(manager.data)}</span>
                </span>
                <ArrowRight className="size-5 shrink-0" />
              </Link>
              <Link to="/tour/engineer" data-testid="landing-engineer"
                className="flex items-center gap-3 rounded-xl border border-slate-300 bg-white p-4 text-slate-800 shadow-sm transition hover:border-brand-500">
                <Cpu className="size-6 shrink-0 text-brand-600" />
                <span className="min-w-0 flex-1">
                  <span className="block font-semibold">Engineer track</span>
                  <span className="block text-sm text-slate-500">Behind the same story · {length(engineer.data)}</span>
                </span>
                <ArrowRight className="size-5 shrink-0 text-slate-400" />
              </Link>
              <Link to="/login" data-testid="landing-live"
                className="inline-flex items-center gap-1.5 px-1 text-sm font-medium text-brand-700 hover:underline">
                <LogIn className="size-4" />Explore the live app
              </Link>
              <p className="px-1 text-xs text-slate-500">
                The tours are screens recorded from this app, with no sign-in. The live app is one shared demo hospital:
                sign in as any role, and what you change, the next visitor sees.
              </p>
            </div>
          </div>

          {hero && (
            <figure className="hidden overflow-hidden rounded-xl border border-slate-200 bg-white shadow-xl sm:block">
              <div className="flex items-center gap-1.5 border-b border-slate-100 px-3 py-2" aria-hidden>
                <span className="size-2.5 rounded-full bg-slate-200" /><span className="size-2.5 rounded-full bg-slate-200" />
                <span className="size-2.5 rounded-full bg-slate-200" />
              </div>
              <img src={imageUrl(hero.image)} alt={`${hero.screen}, from the tour`} className="block w-full" />
              <figcaption className="border-t border-slate-100 px-3 py-2 text-xs text-slate-500">
                From the tour: {hero.screen}. {hero.caption}
              </figcaption>
            </figure>
          )}
        </div>

        <p className="mt-10 text-xs text-slate-500">
          All patients, staff and records are generated. AI output is always a suggestion or a draft that a person
          confirms.
        </p>
      </div>
    </div>
  );
}
