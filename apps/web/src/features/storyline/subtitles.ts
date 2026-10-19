// English subtitles under a Chinese call (the call centre's and the follow-up call's transcripts), for an
// English-speaking audience of the storyline. Nothing here is translated by a model: an agent turn carries the same
// hand-written reply in English (`Turn.gloss`, app/modules/voice_services/phrases.py), and the storyline's scripted
// caller lines come with fixed translations (`patient.translations`, app/modules/storyline/script.py). A line typed by
// hand has no subtitle.
//
// They are on while demo mode is on, unless the presenter turns them off; the transcript's toggle sets them either
// way (a per-browser preference, like demo mode itself).

import { useQuery } from "@tanstack/react-query";
import { useSyncExternalStore } from "react";
import { api } from "../../lib/api";
import { storylineKey, type Storyline } from "./api";
import { useDemoMode } from "./demo";

const KEY = "ioc.subtitles";
type Pref = "on" | "off" | null;
const listeners = new Set<() => void>();

function read(): Pref {
  try {
    const v = localStorage.getItem(KEY);
    return v === "on" || v === "off" ? v : null;
  } catch {
    return null;
  }
}

let pref: Pref = read();

export function setSubtitles(on: boolean) {
  pref = on ? "on" : "off";
  try {
    localStorage.setItem(KEY, pref);
  } catch {
    // blocked storage: the choice holds for this page's lifetime
  }
  listeners.forEach((l) => l());
}

export function useSubtitles(): { on: boolean; lines: Record<string, string> } {
  const demo = useDemoMode();
  const p = useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => pref,
  );
  const on = p === "on" || (p === null && demo.active);
  // the storyline's status is polled by the demo guide already (same query); outside demo mode, read once
  const q = useQuery({ queryKey: storylineKey, queryFn: () => api<Storyline>("/api/storyline"), enabled: on, staleTime: 60_000 });
  return { on, lines: q.data?.patient?.translations ?? {} };
}
