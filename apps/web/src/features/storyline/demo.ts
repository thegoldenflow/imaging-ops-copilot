// Demo mode for the hospital storyline: a presenter's guide that walks the twelve steps one at a time. Its state is a
// per-browser convenience (the presenter's own screen), kept in localStorage: whether the guide is on, which step the
// presenter has started, and whether it is paused. The storyline itself (what is done) always comes from the API.

import { useSyncExternalStore } from "react";

export interface DemoState {
  active: boolean;
  started: number | null; // the step the presenter pressed Start on; null: the next step's introduction shows
  paused: boolean; // the presenter answered "Not yet" after a step: the guide waits as a small pill
}

const KEY = "ioc.demo";
const OFF: DemoState = { active: false, started: null, paused: false };
const listeners = new Set<() => void>();

function read(): DemoState {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? { ...OFF, ...(JSON.parse(raw) as Partial<DemoState>) } : OFF;
  } catch {
    return OFF;
  }
}

let state: DemoState = read();

export function setDemo(next: Partial<DemoState>) {
  state = { ...state, ...next };
  try {
    localStorage.setItem(KEY, JSON.stringify(state));
  } catch {
    // private window or blocked storage: the guide still works for this page's lifetime
  }
  listeners.forEach((l) => l());
}

export const startDemo = () => setDemo({ active: true, started: null, paused: false });
export const exitDemo = () => setDemo(OFF);

export function useDemoMode(): DemoState {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => state,
  );
}

// The guide's "Say it" buttons speak a caller's scripted line into whichever call is open (the call centre's or the
// follow-up call's input), so the presenter does not copy and paste. An input listens only while it can take a line
// (a call is open and the agent is not answering); the buttons are disabled otherwise, so no line is lost.
const SAY_EVENT = "ioc:demo-say";
let listening = 0;
const sayListeners = new Set<() => void>();

export function sayAsCaller(text: string) {
  window.dispatchEvent(new CustomEvent<string>(SAY_EVENT, { detail: text }));
}

export function listenAsCaller(onSay: (text: string) => void): () => void {
  const handler = (e: Event) => onSay((e as CustomEvent<string>).detail);
  window.addEventListener(SAY_EVENT, handler);
  listening += 1;
  sayListeners.forEach((l) => l());
  return () => {
    window.removeEventListener(SAY_EVENT, handler);
    listening -= 1;
    sayListeners.forEach((l) => l());
  };
}

export function useCallerListening(): boolean {
  return useSyncExternalStore(
    (l) => {
      sayListeners.add(l);
      return () => sayListeners.delete(l);
    },
    () => listening > 0,
  );
}
