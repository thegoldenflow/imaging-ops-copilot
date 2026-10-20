// The recorded tour's manifests (demo/tour.md): written by scripts/tour/capture.mjs into public/tour/<track>.json and
// served as static files, so the tour needs no sign-in and makes no /api request.

import { useQuery } from "@tanstack/react-query";

export const TRACK_IDS = ["manager", "engineer"] as const;
export type TrackId = (typeof TRACK_IDS)[number];
export const isTrack = (s: string | undefined): s is TrackId => TRACK_IDS.includes(s as TrackId);

export interface Box { x: number; y: number; width: number; height: number }

export interface Chapter {
  id: string;
  title: string;
  step?: number; // the storyline step it shows
  meanwhile?: string; // what happened since the last chapter
  role: { name: string; title: string; text: string };
}

export interface TourStep {
  id: string;
  chapter: string;
  screen: string;
  image: string; // relative to /tour/
  box: Box; // the hotspot, in the screenshot's pixels
  caption: string;
  note?: string; // how this one screen was captured, when it differs from the rest
}

export interface Manifest {
  track: TrackId;
  title: string;
  audience: string;
  intro: string;
  other: TrackId;
  end: { title: string; text: string; otherLabel: string };
  captured: { at: string; version: string; model: string; temporal: boolean; story: string };
  viewport: { width: number; height: number };
  chapters: Chapter[];
  steps: TourStep[];
}

/** About 15 seconds per hotspot, as the tour's length on the landing page. */
export const minutes = (m: Manifest) => Math.max(1, Math.round((m.steps.length * 15) / 60));

export const imageUrl = (image: string) => `/tour/${image}`;

export function useManifest(track: TrackId) {
  return useQuery({
    queryKey: ["tour", track],
    queryFn: async () => {
      const r = await fetch(`/tour/${track}.json`);
      // a missing file comes back as the app itself (the SPA fallback), not as a 404
      if (!r.ok || !(r.headers.get("content-type") ?? "").includes("json")) {
        throw new Error(`The ${track} tour has not been captured yet`);
      }
      return (await r.json()) as Manifest;
    },
    staleTime: Infinity,
    retry: false,
  });
}
