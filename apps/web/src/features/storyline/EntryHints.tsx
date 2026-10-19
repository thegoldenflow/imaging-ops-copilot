// The way into demo mode, marked the same way as the steps inside it (Spotlight.tsx): someone who opens the app for
// the first time sees which button leads to the guided storyline. On the sign-in page the administrator's card (the
// storyline's presenter), on the home page the menu's Hospital storyline, on the storyline page the Demo mode button.
// Shown while the guide is not on screen (demo mode off, or nobody signed in); inside demo mode the guide takes over.

import { useLocation } from "react-router-dom";
import { useAuth } from "../../lib/auth";
import { useDemoMode } from "./demo";
import { Spotlight, useNextTarget, type Target } from "./Spotlight";

/** The first match that is on screen (the menu has a desktop and a mobile copy of each link). */
const firstVisible = (selector: string) => () =>
  [...document.querySelectorAll(selector)].find((e) => e.getClientRects().length > 0) ?? null;

function targetsFor(path: string, signedIn: boolean): Target[] {
  if (path === "/login") {
    return [{ find: firstVisible("[data-testid=login-admin]"), label: "Start here: sign in as Casey Brooks to present the hospital storyline" }];
  }
  if (!signedIn) return [];
  if (path === "/") return [{ find: firstVisible('a[href="/storyline"]'), label: "Open the guided hospital storyline" }];
  if (path === "/storyline") {
    return [{ find: firstVisible("[data-testid=story-demo]"), label: "Start the guided demo: each step explained, the button to press marked" }];
  }
  return [];
}

export function DemoEntryHints() {
  const demo = useDemoMode();
  const { user } = useAuth();
  const { pathname } = useLocation();
  const guided = demo.active && !!user; // the guide panel shows only to someone signed in
  const hit = useNextTarget(guided ? [] : targetsFor(pathname, !!user));
  return guided ? null : <Spotlight hit={hit} />;
}
