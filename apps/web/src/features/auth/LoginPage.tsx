import { useQuery } from "@tanstack/react-query";
import { ScanLine } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ErrorState, Loading } from "../../components/ui";
import { api } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { ROLE_LABEL, type Role, type StaffUser } from "../../lib/types";

const ROLE_HINT: Record<Role, string> = {
  front_desk: "Phone agent, reminders, pre-registration",
  technologist: "Today's schedule at your site",
  radiologist: "AI report drafts, review and sign",
  operations_manager: "Utilization, backfill, referrals, feedback, billing",
  medical_director: "Reports, audit log, AI usage",
  admin: "Everything, including audit and settings",
  referrer: "Online requisitions, your patients and signed reports",
};

export function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [passcode, setPasscode] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const users = useQuery({
    queryKey: ["demo-users"],
    queryFn: () => api<{ users: StaffUser[]; passcode_required: boolean }>("/api/auth/users"),
  });

  const choose = async (user: StaffUser) => {
    setBusy(user.id);
    setError(null);
    try {
      await login(user.id, passcode || undefined);
      navigate("/");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Login failed");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="min-h-full bg-gradient-to-b from-brand-50 to-slate-50 px-4 py-12">
      <div className="mx-auto max-w-3xl">
        <div className="mb-8 flex items-center gap-3">
          <div className="grid size-10 place-items-center rounded-xl bg-brand-600 text-white">
            <ScanLine className="size-5" />
          </div>
          <div>
            <h1 className="text-2xl font-semibold text-slate-900">Imaging Ops Copilot</h1>
            <p className="text-sm text-slate-600">AI tools for an outpatient imaging centre · demo with synthetic data</p>
          </div>
        </div>
        <h2 className="mb-3 text-sm font-semibold text-slate-700">Sign in as</h2>
        {users.isLoading && <Loading />}
        {users.error && <ErrorState error={users.error} onRetry={() => users.refetch()} />}
        {users.data?.passcode_required && (
          <input
            type="password"
            value={passcode}
            onChange={(e) => setPasscode(e.target.value)}
            placeholder="Demo passcode"
            className="mb-3 w-full max-w-xs rounded-lg border border-slate-300 px-3 py-2 text-sm"
          />
        )}
        <div className="grid gap-3 sm:grid-cols-2">
          {users.data?.users.map((u) => (
            <button
              key={u.id}
              onClick={() => choose(u)}
              disabled={busy !== null}
              data-testid={`login-${u.role}`}
              className="flex flex-col items-start rounded-xl border border-slate-200 bg-white p-4 text-left shadow-sm transition hover:border-brand-500 hover:shadow disabled:opacity-60"
            >
              <span className="text-xs font-semibold tracking-wide text-brand-700 uppercase">{ROLE_LABEL[u.role]}</span>
              <span className="mt-1 text-sm font-medium text-slate-900">{u.name}</span>
              <span className="mt-0.5 text-xs text-slate-500">{ROLE_HINT[u.role]}</span>
              {busy === u.id && <span className="mt-1 text-xs text-slate-400">Signing in…</span>}
            </button>
          ))}
        </div>
        {error && <p className="mt-3 text-sm text-rose-600" role="alert">{error}</p>}
        <p className="mt-8 text-xs text-slate-500">
          All patients, physicians and appointments are generated. No real patient information is used. AI output is
          always a suggestion or draft that staff must confirm.
        </p>
      </div>
    </div>
  );
}
