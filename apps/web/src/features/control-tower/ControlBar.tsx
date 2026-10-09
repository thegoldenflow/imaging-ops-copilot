// The simulator's control bar (spec 7.1): hospital time, rate, run / pause, +30 min, "fast-forward to 08:00 tomorrow"
// (a background job with progress; the boards move while it runs) and scripted scenarios. It drives the day
// simulator's API (WP3); only the bed manager (and the administrator) may, others see the clock.

import clsx from "clsx";
import { FastForward, Loader2, Moon, Pause, Play, Plus, Siren, Sun, TowerControl } from "lucide-react";
import { useState } from "react";
import { ApiError } from "../../lib/api";
import { dayTime, simAdvance, simFastForward, simInject, simPause, simRun, type Board } from "./api";

const RATES = [
  { value: 60, label: "1 h / min" },
  { value: 300, label: "5 h / min" },
  { value: 900, label: "15 h / min" },
];
const SCENARIO_LABEL: Record<string, string> = {
  icu_surge: "ICU surge",
  ed_surge: "ED surge",
  or_overrun: "OR overrun",
  consent_revoked: "Consent withdrawn",
};

export function ControlBar({ board, theme, onTheme, onChanged }: {
  board: Board | undefined;
  theme: "dark" | "light";
  onTheme: () => void;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const [rate, setRate] = useState(60);
  const [scenario, setScenario] = useState("icu_surge");
  const [size, setSize] = useState(10);
  const clock = board?.clock;
  const job = board?.job?.status === "running" ? board.job : null;
  const control = board?.can_control ?? false;
  const ended = Boolean(clock?.stopped);

  const act = async (name: string, fn: () => Promise<unknown>, done?: string) => {
    setBusy(name);
    setMessage(null);
    try {
      await fn();
      if (done) setMessage({ tone: "ok", text: done });
      onChanged();
    } catch (e) {
      setMessage({ tone: "error", text: e instanceof ApiError ? e.message : "The simulator did not respond" });
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-xl border border-ct-border bg-ct-surface px-3 py-2" data-testid="control-bar">
      <div className="flex items-center gap-2">
        <div className="grid size-8 place-items-center rounded-lg bg-ct-accent text-white">
          <TowerControl className="size-4" />
        </div>
        <div className="leading-tight">
          <p className="text-sm font-semibold text-ct-text">Control Tower</p>
          <p className="text-[11px] text-ct-muted">ED → beds → OR · synthetic hospital</p>
        </div>
        <button
          onClick={onTheme}
          className="rounded-md border border-ct-border p-1.5 text-ct-muted hover:bg-ct-raised hover:text-ct-text"
          aria-label={theme === "dark" ? "Light theme" : "Dark theme"}
          title={theme === "dark" ? "Light theme" : "Dark theme"}
          data-testid="theme-toggle"
        >
          {theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
        </button>
      </div>

      <div className="flex items-center gap-2 border-l border-ct-border pl-4">
        <span
          className={clsx("size-2 rounded-full", clock?.running || job ? "ct-live bg-ct-ok" : "bg-ct-muted")}
          aria-hidden
        />
        <div className="leading-tight">
          <p className="tabular text-sm font-semibold text-ct-text" data-testid="hospital-time">
            {clock ? dayTime(clock.now) : "–"}
          </p>
          <p className="text-[11px] text-ct-muted" data-testid="clock-state">
            {job ? "fast-forwarding" : clock?.running ? `running · ${RATES.find((r) => r.value === clock.rate)?.label ?? `${clock.rate}×`}` : ended ? "end of the planned day" : "paused"} · hospital time
          </p>
        </div>
      </div>

      {control && (
        <div className="flex flex-wrap items-center gap-1.5">
          {clock?.running ? (
            <BarButton onClick={() => act("pause", simPause)} busy={busy === "pause"} icon={Pause} label="Pause" testId="sim-pause" />
          ) : (
            <>
              <select
                value={rate}
                onChange={(e) => setRate(Number(e.target.value))}
                className="h-8 rounded-md border border-ct-border bg-ct-raised px-1.5 text-xs text-ct-text"
                aria-label="Simulation rate"
                data-testid="sim-rate"
              >
                {RATES.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
              </select>
              <BarButton onClick={() => act("run", () => simRun(rate))} busy={busy === "run"} icon={Play} label="Run" disabled={ended || Boolean(job)} testId="sim-run" />
            </>
          )}
          <BarButton onClick={() => act("advance", () => simAdvance(30))} busy={busy === "advance"} icon={Plus} label="30 min" disabled={ended || Boolean(job) || clock?.running} testId="sim-advance" />
          <BarButton
            onClick={() => act("ff", simFastForward)}
            busy={busy === "ff"}
            icon={FastForward}
            label="08:00 tomorrow"
            disabled={ended || Boolean(job)}
            testId="sim-fast-forward"
          />
          <div className="ml-1 flex items-center gap-1.5 border-l border-ct-border pl-2.5">
            <select
              value={scenario}
              onChange={(e) => setScenario(e.target.value)}
              className="h-8 rounded-md border border-ct-border bg-ct-raised px-1.5 text-xs text-ct-text"
              aria-label="Scenario"
              data-testid="scenario-select"
              title={board?.scenarios[scenario]}
            >
              {Object.keys(board?.scenarios ?? SCENARIO_LABEL).map((s) => <option key={s} value={s}>{SCENARIO_LABEL[s] ?? s}</option>)}
            </select>
            {scenario === "icu_surge" && (
              <label className="flex items-center gap-1 text-[11px] text-ct-muted" title="Critically ill arrivals (ICU has 12 beds)">
                <input
                  type="number"
                  min={1}
                  max={20}
                  value={size}
                  onChange={(e) => setSize(Math.max(1, Math.min(20, Number(e.target.value) || 1)))}
                  className="h-8 w-12 rounded-md border border-ct-border bg-ct-raised px-1 text-xs text-ct-text"
                  aria-label="Patients"
                />
                patients
              </label>
            )}
            <BarButton
              onClick={() => act("inject", () => simInject(scenario, scenario === "icu_surge" ? size : undefined), `${SCENARIO_LABEL[scenario]} injected: starts in a minute of hospital time`)}
              busy={busy === "inject"}
              icon={Siren}
              label="Inject"
              disabled={ended || Boolean(job)}
              testId="sim-inject"
            />
          </div>
        </div>
      )}

      {job && (
        <div className="flex min-w-48 items-center gap-2" data-testid="ff-progress">
          <Loader2 className="size-4 animate-spin text-ct-accent" />
          <div className="h-1.5 w-32 overflow-hidden rounded-full bg-ct-raised">
            <div className="h-full rounded-full bg-ct-accent transition-all" style={{ width: `${Math.round(job.progress * 100)}%` }} />
          </div>
          <span className="tabular text-[11px] text-ct-muted">{Math.round(job.progress * 100)}% · {job.events} events</span>
        </div>
      )}

      {message && (
        <span className={clsx("ml-auto text-xs", message.tone === "error" ? "text-ct-critical" : "text-ct-ok")} role="status">{message.text}</span>
      )}
    </div>
  );
}

function BarButton({ onClick, busy, icon: Icon, label, disabled, testId }: {
  onClick: () => void;
  busy: boolean;
  icon: typeof Play;
  label: string;
  disabled?: boolean;
  testId?: string;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled || busy}
      className="inline-flex h-8 items-center gap-1.5 rounded-md border border-ct-border bg-ct-raised px-2.5 text-xs font-medium text-ct-text hover:border-ct-accent disabled:cursor-not-allowed disabled:opacity-40"
      data-testid={testId}
    >
      {busy ? <Loader2 className="size-3.5 animate-spin" /> : <Icon className="size-3.5" />}
      {label}
    </button>
  );
}
