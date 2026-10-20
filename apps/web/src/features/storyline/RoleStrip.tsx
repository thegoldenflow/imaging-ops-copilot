// Demo mode's role strip (spec 8.2: "show the current role at every step, switch roles so the audience sees the
// permissions change"): under the header while demo mode is on, who is signed in now and what that role may see or
// do in this step (the step's `permission` sentence, app/modules/storyline/script.py). When demo mode switches person
// (Start step N, Switch to … to co-sign, Back to …), the strip turns into a notice for a few seconds: "You are now …",
// and who it was before. It sits in the page's flow under the header, not over it, so it never covers a button the
// spotlight marks.

import clsx from "clsx";
import { Repeat, UserRound } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useAuth } from "../../lib/auth";
import { ROLE_LABEL, type Role } from "../../lib/types";
import { useStoryline } from "./api";
import { useDemoMode } from "./demo";

const NOTICE_MS = 5000;

export function DemoRoleStrip() {
  const demo = useDemoMode();
  const { user } = useAuth();
  const q = useStoryline(demo.active && !!user);
  const me = useRef(user);
  me.current = user;
  const previous = useRef<{ id: string; name: string; role: Role } | null>(null);
  const [from, setFrom] = useState<{ name: string; role: Role } | null>(null);
  useEffect(() => { // on a change of person only (the user object is refetched with the same id)
    const was = previous.current;
    const now = me.current;
    previous.current = now ? { id: now.id, name: now.name, role: now.role } : null;
    if (!demo.active || !now || !was || was.id === now.id) {
      setFrom(null);
      return;
    }
    setFrom({ name: was.name, role: was.role });
    const id = window.setTimeout(() => setFrom(null), NOTICE_MS);
    return () => window.clearTimeout(id);
  }, [user?.id, demo.active]);
  if (!demo.active || !user) return null;

  const data = q.data;
  const n = demo.started ?? data?.current ?? null;
  const step = n != null && data?.prepared ? data.steps[n - 1] : undefined;
  // her surgeon co-signs step 6: the step's sentence is about him too
  const cosigner = step?.key === "medrec" ? data?.steps.find((s) => s.key === "summary")?.cast : undefined;
  const plays = !!step?.cast && (step.cast.id === user.id || cosigner?.id === user.id);
  const role = ROLE_LABEL[user.role] ?? user.role;
  return (
    <div className={clsx("border-b px-4 py-2 transition-colors duration-500 md:px-6",
      from ? "border-brand-700 bg-brand-700 text-white" : "border-brand-100 bg-brand-50 text-slate-800")}
      data-testid="demo-role-strip" data-switched={from ? "true" : "false"} role="status" aria-live="polite">
      <p className="flex min-w-0 items-center gap-2 text-sm">
        {from ? <Repeat className="size-4 shrink-0 animate-pulse" /> : <UserRound className="size-4 shrink-0 text-brand-700" />}
        <span className="truncate">
          You are now <span className={clsx("font-semibold", !from && "text-brand-700")} data-testid="demo-role-name">{user.name} · {role}</span>
          {from && <span className="ml-2 text-xs text-brand-100" data-testid="demo-role-notice">switched from {from.name} · {ROLE_LABEL[from.role] ?? from.role}</span>}
        </span>
      </p>
      {step && (
        <p className={clsx("mt-0.5 pl-6 text-xs", from ? "text-brand-50" : "text-slate-700")} data-testid="demo-role-permission">
          {plays ? <><span className="font-semibold">Step {step.n}: </span>{step.permission}</>
            : <>Step {step.n} is played by {step.cast ? `${step.cast.name} · ${ROLE_LABEL[step.cast.role] ?? step.cast.role}` : "its person"}: Start switches to them.</>}
        </p>
      )}
    </div>
  );
}
