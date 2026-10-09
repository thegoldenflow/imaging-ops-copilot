import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ShieldAlert, Siren } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Button } from "../../components/ui";
import { api, post } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { time } from "../../lib/format";
import { MIN_REASON, reasonLength, type ActiveGrant } from "./api";

const BREAKS_GLASS = ["physician", "nurse"];

/** Persistent red banner while any break-glass grant of the signed-in user is active (6.3). */
export function BreakGlassBanner() {
  const { user } = useAuth();
  const enabled = Boolean(user && BREAKS_GLASS.includes(user.role));
  const active = useQuery({
    queryKey: ["break-glass-active"],
    queryFn: () => api<{ grants: ActiveGrant[] }>("/api/hospital/break-glass/active"),
    enabled,
    refetchInterval: 30_000,
  });
  const grants = active.data?.grants ?? [];
  if (!enabled || grants.length === 0) return null;
  return (
    <div role="alert" data-testid="break-glass-banner" className="flex flex-wrap items-center gap-x-3 gap-y-1 bg-rose-600 px-4 py-2 text-sm text-white md:px-6">
      <Siren className="size-4 shrink-0" />
      <span className="font-semibold">Emergency access (break-glass) active</span>
      {grants.map((g) => (
        <span key={g.id} className="rounded bg-rose-700 px-2 py-0.5 text-xs">
          MRN {g.mrn} · until {time(g.expires_at)} ({Math.floor(g.minutes_left / 60)} h {g.minutes_left % 60} min left)
        </span>
      ))}
      <span className="text-xs text-rose-100">Every action is audited; an administrator reviews this access within 24 hours.</span>
    </div>
  );
}

/** The reason dialog: required, at least 10 characters, then a 4-hour grant for this patient. */
export function BreakGlassDialog({ mrn, onClose, onGranted }: { mrn: string; onClose: () => void; onGranted: () => void }) {
  const queryClient = useQueryClient();
  const [reason, setReason] = useState("");
  const box = useRef<HTMLTextAreaElement>(null);
  const length = reasonLength(reason);
  const grant = useMutation({
    mutationFn: () => post<{ id: string; expires_at: string }>("/api/hospital/break-glass", { mrn, reason }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["break-glass-active"] });
      onGranted();
    },
  });
  useEffect(() => box.current?.focus(), []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-slate-900/40 px-4" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="break-glass-title"
        className="w-full max-w-lg rounded-xl bg-white p-5 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start gap-3">
          <div className="grid size-9 shrink-0 place-items-center rounded-full bg-rose-50 text-rose-600">
            <ShieldAlert className="size-5" />
          </div>
          <div>
            <h2 id="break-glass-title" className="text-base font-semibold text-slate-900">Emergency access (break-glass)</h2>
            <p className="mt-1 text-sm text-slate-600">
              MRN {mrn} is not one of your unit's patients. Emergency access opens this record to you for 4 hours. The access and
              everything you open are written to the audit log, and an administrator reviews it within 24 hours.
            </p>
          </div>
        </div>
        <label htmlFor="break-glass-reason" className="mt-4 block text-sm font-medium text-slate-800">Reason for emergency access</label>
        <textarea
          id="break-glass-reason"
          ref={box}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          rows={3}
          className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-brand-500 focus:outline-none"
          placeholder="e.g. Rapid response call, covering for the attending physician"
        />
        <p className={`mt-1 text-xs ${length >= MIN_REASON ? "text-slate-500" : "text-rose-600"}`} data-testid="break-glass-count">
          {length} characters{length < MIN_REASON ? ` · at least ${MIN_REASON} required` : ""}
        </p>
        {grant.error && <p className="mt-2 text-sm text-rose-600" role="alert">{grant.error.message}</p>}
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button variant="danger" onClick={() => grant.mutate()} disabled={length < MIN_REASON} loading={grant.isPending}>
            Open record for 4 hours
          </Button>
        </div>
      </div>
    </div>
  );
}
