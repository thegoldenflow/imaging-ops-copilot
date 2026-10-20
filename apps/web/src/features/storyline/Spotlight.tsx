// Demo mode's spotlight: which button to press next, shown on the page itself for someone who has never seen the
// app. Each step lists its targets in order (app/modules/storyline/script.py's click path, as selectors of the
// module's own screens); the first one that is on screen, enabled and not yet done gets an amber ring and a label,
// and is scrolled into view. Pressing it changes the page, and the next target lights up. The spotlight never
// clicks: the presenter does.

import { MousePointerClick } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { Storyline, StoryStep } from "./api";

export interface Target {
  find: () => Element | null;
  label: string;
  done?: () => boolean; // still on screen but already pressed (a caller's line said, a reason written)
}

// The storyline's fixed ids (app/modules/storyline/script.py)
const STAY = "stay-j0001";

const q = (selector: string) => document.querySelector(selector);
export const byText = (selector: string, text: string) => () =>
  [...document.querySelectorAll(selector)].find((e) => e.textContent?.trim() === text) ?? null;
const has = (selector: string) => () => !!q(selector);
const filled = (selector: string) => () => ((q(selector) as HTMLInputElement | null)?.value ?? "").trim().length >= 3;
// her stay is open on the documentation page (its panel may still be loading: not "open her stay" again meanwhile)
const stayOpen = () => (q("[data-testid=doc-patient]")?.textContent ?? "").includes(STAY);
const said = (transcript: string, line: string | undefined) => () =>
  !!line && (q(`[data-testid=${transcript}]`)?.textContent ?? "").includes(line);
const t = (selector: string, label: string, done?: () => boolean): Target => ({ find: () => q(selector), label, done });

function usable(el: Element | null): el is HTMLElement {
  if (!(el instanceof HTMLElement) || el.getClientRects().length === 0) return false;
  return !(el as HTMLButtonElement).disabled;
}

/** The targets of a step while it is the presenter's turn, in the order they are pressed. */
export function stepTargets(step: StoryStep, data: Storyline): Target[] {
  const lines = step.key === "call" ? data.patient?.call_lines ?? [] : data.patient?.followup_lines ?? [];
  const task = step.proposal?.ref?.startsWith("Task/") ? step.proposal.ref.split("/")[1] : null;
  const card = (label: string) => (task ? [t(`[data-task="${task}"] [data-testid=approve]`, label)] : []);
  switch (step.key) {
    case "call": {
      // the call's Start button comes back once the daughter's goodbye ends the call: the call is done by then
      const called = () => has("[data-testid=call-transcript]")() || has('button[aria-label="Confirm registration"]')();
      return [
        t("[data-testid=start-call]", "Start the call", called),
        t("[data-testid=demo-say-0]", "Say the daughter's first line", said("call-transcript", lines[0])),
        t("[data-testid=demo-say-1]", "Say who the patient is (name, birth date, card)", said("call-transcript", lines[1])),
        // the agent has asked "anything else?": her last line, and the call ends
        t("[data-testid=demo-say-2]", "Say the daughter's last line: the call ends", said("call-transcript", lines[2])),
        t('button[aria-label="Confirm registration"]', "Confirm the registration the AI pre-filled"),
      ];
    }
    case "triage":
      return card("Accept the AI's CTAS 2 suggestion");
    case "imaging":
      return card("Accept the suggested imaging orders");
    case "inr":
      return card("Accept: you acknowledge the critical INR");
    case "news2":
      return card("Accept: you record your response to NEWS2 7");
    case "coding":
      return card("Accept the suggested codes");
    case "bed": {
      const exc = step.proposal?.ref?.startsWith("FlowException/") ? step.proposal.ref.split("/")[1] : null;
      return [
        ...(exc ? [t(`[data-exception="${exc}"]`, "Open the Orthopedics exception", has("[data-testid=action-drawer]"))] : []),
        t("[data-testid=action-drawer] [data-testid=approve]", "Approve the actions the AI recommends"),
      ];
    }
    case "medrec": {
      const review = step.step_ai?.items.find((i) => i.kind === "order_review")?.id;
      // her naproxen order is open, or the pharmacist has signed (the surgeon co-signs: no queue to search)
      const opened = () => has("[data-testid=review-panel]")() || !!step.signed?.by;
      return [
        ...(review ? [t(`[data-testid="queue-item-${review}"]`, "Open her naproxen order (major interaction)", opened)] : []),
        t("[data-testid=queue-search]", `Search her MRN ${data.patient?.mrn ?? ""}`, opened),
        t("[data-testid=review-panel] [data-testid=decision-reason]", "Write why the order should change",
          () => filled("[data-testid=review-panel] [data-testid=decision-reason]")() || has("[data-testid=review-panel] [data-testid=decision]")()),
        t("[data-testid=review-panel] [data-testid=request-change]", "Ask the prescriber to change it"),
        t("[data-testid=medrec-panel] [data-testid=item-form] [data-testid=item-reason]", "Give a reason for this discrepancy",
          filled("[data-testid=medrec-panel] [data-testid=item-form] [data-testid=item-reason]")),
        t("[data-testid=medrec-panel] [data-testid=item-form] [data-testid=item-save]", "Save this item"),
        t("[data-testid=demo-cosign]", "Switch to the surgeon to co-sign"),
        t("[data-testid=medrec-panel] [data-testid=sign-button]", step.signed?.by ? "Co-sign as physician" : "Sign as pharmacist"),
      ];
    }
    case "or":
      return [t(`[data-testid=or-row][data-mrn="${data.patient?.mrn ?? ""}"] [data-testid=adjust-booking]`,
        "Adjust her booking to the predicted time")];
    case "summary": {
      const red = "[data-testid=summary-item][data-status=red]";
      return [
        t(`[data-testid="work-${STAY}"]`, "Open her stay", stayOpen),
        t("[data-testid=draft-summary]", "Draft the discharge summary with AI"),
        t("[data-testid=edit-summary]", "Edit: one item has no source", () => has("[data-testid=save-summary]")() || !q(red)),
        t(`${red} [data-testid=remove-item]`, "Remove the item with no source"),
        t("[data-testid=save-summary]", "Save and re-check"),
        t("[data-testid=sign-summary]", "Sign the summary as physician"),
      ];
    }
    case "instructions":
      return [
        t(`[data-testid="work-${STAY}"]`, "Open her stay", stayOpen),
        t("[data-testid=draft-instructions]", "Draft the instructions now"),
        t("[data-testid=sign-instructions]", "Sign the instructions as nurse"),
      ];
    case "followup": {
      const ended = has("[data-testid=followup-ended], [data-testid=followup-transcript]");
      const queueTab = byText("[role=tab]", "Queue");
      // the nurse's part comes once her call is over: while the clock still moves to it (no Place the call yet) the
      // Queue tab and its top item (someone else's) were marked
      const over = () => ended() || (!!step.detail && !/^(manual|scheduled|in_progress)\b/.test(step.detail));
      const after = (done: () => boolean) => () => !over() || done();
      return [
        t("[data-testid=place-call]", "Place the follow-up call"),
        t("[data-testid=demo-say-0]", "Answer as the patient: her birth date", said("followup-call-transcript", lines[0])),
        t("[data-testid=demo-say-1]", "Answer: the wound keeps leaking", said("followup-call-transcript", lines[1])),
        t("[data-testid=demo-say-2]", "Answer the remaining questions: nothing else", ended),
        { find: queueTab, label: "Open the nurse's queue", done: after(() => queueTab()?.getAttribute("aria-selected") === "true") },
        t("[data-testid=followup-list] li", "Open her call (Urgent, on top)", after(has("[data-testid=patient-words]"))),
        t('textarea[aria-label="Nurse note"]', "Write what you did when you called back", after(filled('textarea[aria-label="Nurse note"]'))),
        { find: byText("button", "Save call-back note"), label: "Save the call-back note", done: after(() => false) },
      ];
    }
    default:
      return [];
  }
}

export interface Hit {
  el: HTMLElement;
  label: string;
}

/** The first usable, not yet done target, re-checked as the page changes. */
export function useNextTarget(targets: Target[]): Hit | null {
  const ref = useRef(targets);
  ref.current = targets;
  const [hit, setHit] = useState<Hit | null>(null);
  useEffect(() => {
    const tick = () => {
      let next: Hit | null = null;
      for (const target of ref.current) {
        const el = target.find();
        if (usable(el) && !target.done?.()) {
          next = { el, label: target.label };
          break;
        }
      }
      setHit((prev) => (prev?.el === next?.el && prev?.label === next?.label ? prev : next));
    };
    tick();
    const id = window.setInterval(tick, 300);
    return () => window.clearInterval(id);
  }, []);
  return hit;
}

// ----- where the label goes -----
// The label must not hide what it points at: not the target, not the text of the target's own row or card (the
// registration card's "ED registration · MRN …" above its Confirm), and not the guide panel. For a button of the
// guide panel (Say it, Start step N) it goes outside the panel, beside its edge, level with the button. For a button
// on the page it goes to the free side: around the target's row, the place that covers the least text and fewest
// controls of the page (sampled), so it does not land on a neighbouring card's text either (the sign-in page).

interface Box { left: number; top: number; right: number; bottom: number }

const GAP = 12; // between the label and what it must not cover (the ring shows 10 px around the target)
const EDGE = 8; // from the window's edge
const RING = 4; // the ring's padding around the target

const box = (r: DOMRect | Box): Box => ({ left: r.left, top: r.top, right: r.right, bottom: r.bottom });
const grow = (b: Box, by: number): Box => ({ left: b.left - by, top: b.top - by, right: b.right + by, bottom: b.bottom + by });
const meets = (a: Box, b: Box) => a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(v, Math.max(lo, hi)));

/** The row, list item or card the target belongs to: its text is what the label must leave readable. */
function ownBox(el: HTMLElement): Box {
  const own = el.closest("li, tr, [data-task], [data-exception], [data-testid=or-row]");
  const r = own && !own.closest("[data-testid=demo-guide]") ? own.getBoundingClientRect() : null;
  return box(r && r.height < window.innerHeight / 3 ? r : el.getBoundingClientRect());
}

/** Whether text or a control of the page is at this point (the spotlight itself does not count). */
function busyAt(x: number, y: number): boolean {
  const hitEl = document.elementFromPoint(x, y);
  if (!hitEl || hitEl.closest("[data-testid=demo-spotlight]")) return false;
  if (hitEl.closest("button, a, input, select, textarea, [role=tab]")) return true;
  const doc = document as Document & {
    caretPositionFromPoint?: (x: number, y: number) => { offsetNode: Node; offset: number } | null;
    caretRangeFromPoint?: (x: number, y: number) => Range | null;
  };
  const pos = doc.caretPositionFromPoint?.(x, y);
  const range = pos ? null : doc.caretRangeFromPoint?.(x, y);
  const node = pos?.offsetNode ?? range?.startContainer;
  const offset = pos?.offset ?? range?.startOffset ?? 0;
  if (!(node instanceof Text) || !node.data.trim()) return false;
  const glyph = document.createRange();
  for (const at of [offset - 1, offset]) { // the caret sits between two characters: is either of them under the point?
    if (at < 0 || at >= node.length) continue;
    glyph.setStart(node, at);
    glyph.setEnd(node, at + 1);
    for (const r of glyph.getClientRects()) if (x >= r.left - 1 && x <= r.right + 1 && y >= r.top && y <= r.bottom) return true;
  }
  return false;
}

/** The share of sample points in the box that land on text or a control (every 8 px down: no line of text slips
 * between two rows). */
function cover(b: Box): number {
  let busy = 0;
  let all = 0;
  for (let y = b.top + 3; y <= b.bottom - 3; y += 8) {
    for (let x = b.left + 3; x <= b.right - 3; x += 14) {
      all += 1;
      if (busyAt(x, y)) busy += 1;
    }
  }
  return all ? busy / all : 0;
}

/** Where the label (w × h) goes for a target at `t` (its ring included). */
export function placeLabel(el: HTMLElement, t: DOMRect, w: number, h: number): { left: number; top: number } {
  const W = window.innerWidth;
  const H = window.innerHeight;
  const ring = grow(box(t), RING + 2);
  const at = (left: number, top: number): Box => ({ left, top, right: left + w, bottom: top + h });
  const fits = (b: Box) => b.left >= EDGE && b.top >= EDGE && b.right <= W - EDGE && b.bottom <= H - EDGE;
  const level = clamp(t.top + t.height / 2 - h / 2, EDGE, H - EDGE - h);
  const panel = document.querySelector("[data-testid=demo-guide]")?.getBoundingClientRect();
  if (panel && el.closest("[data-testid=demo-guide]")) {
    // the guide's own button: beside the panel's outer edge, level with the button; at phone width (the panel spans
    // the screen) above the panel
    const sides = W >= 640 ? [at(panel.left - GAP - w, level), at(panel.right + GAP, level)]
      : [at(clamp(t.left, EDGE, W - EDGE - w), panel.top - GAP - h)];
    const side = sides.find(fits);
    if (side) return { left: side.left, top: side.top };
  } else {
    const own = ownBox(el);
    const keep = [ring, grow(own, 2), ...(panel ? [grow(box(panel), 2)] : [])];
    const xs = [t.left, t.right - w, t.left + t.width / 2 - w / 2].map((x) => clamp(x, EDGE, W - EDGE - w));
    const candidates = [
      ...xs.map((x) => at(x, own.top - GAP - h)), // above its row
      ...xs.map((x) => at(x, own.bottom + GAP)), // below its row
      at(own.right + GAP, level), // right of its row
      at(own.left - GAP - w, level), // left of its row
    ].filter((b) => fits(b) && !keep.some((k) => meets(b, k)));
    let best: Box | null = null;
    let least = Infinity;
    for (const b of candidates) { // the first one that covers least (in order of preference)
      const c = cover(b);
      if (c < least - 0.01) {
        best = b;
        least = c;
      }
      if (c === 0) break;
    }
    if (best) return { left: best.left, top: best.top };
  }
  // nowhere free (a crowded small window): just above or below the target, clear of it at least
  const left = clamp(t.left - RING, EDGE, W - EDGE - w);
  return t.top - RING - GAP - h >= EDGE ? { left, top: t.top - RING - GAP - h } : { left, top: t.bottom + RING + GAP };
}

const same = (a: DOMRect | null | undefined, b: DOMRect | null | undefined) =>
  a?.left === b?.left && a?.top === b?.top && a?.width === b?.width && a?.height === b?.height;

/** The amber ring and its label beside the target; clicks go through to the page. */
export function Spotlight({ hit }: { hit: Hit | null }) {
  const [rect, setRect] = useState<DOMRect | null>(null);
  const [panel, setPanel] = useState<DOMRect | null>(null);
  const [place, setPlace] = useState<{ left: number; top: number } | null>(null);
  const labelRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!hit) {
      setRect(null);
      return;
    }
    const r = hit.el.getBoundingClientRect();
    if (r.top < 0 || r.bottom > window.innerHeight) hit.el.scrollIntoView({ block: "center", behavior: "smooth" });
    let frame = 0;
    const follow = () => { // the page scrolls and reflows under it: follow the target (and the guide panel, which the
      // label keeps clear of), re-rendering only on a move
      const next = hit.el.getBoundingClientRect();
      const guide = document.querySelector("[data-testid=demo-guide]")?.getBoundingClientRect() ?? null;
      setRect((prev) => (same(prev, next) ? prev : next));
      setPanel((prev) => (same(prev, guide) ? prev : guide));
      frame = window.requestAnimationFrame(follow);
    };
    follow();
    return () => window.cancelAnimationFrame(frame);
  }, [hit]);
  // placed once its size is known, again when the target or the panel moves (not every frame: the page's text under
  // the places it tries does not change with the label)
  useLayoutEffect(() => {
    const label = labelRef.current;
    if (!hit || !rect || !label || rect.width === 0) {
      setPlace(null);
      return;
    }
    setPlace(placeLabel(hit.el, rect, label.offsetWidth, label.offsetHeight));
  }, [hit, rect, panel]);
  if (!hit || !rect || rect.width === 0) return null;
  return (
    <div className="pointer-events-none fixed inset-0 z-[60]" aria-hidden="true" data-testid="demo-spotlight">
      <div className="absolute animate-pulse rounded-lg ring-4 ring-amber-400 ring-offset-2" data-testid="demo-spotlight-ring"
        style={{ left: rect.left - RING, top: rect.top - RING, width: rect.width + 2 * RING, height: rect.height + 2 * RING }} />
      <div ref={labelRef} className="absolute flex w-max max-w-[15rem] items-center gap-1.5 rounded-md bg-amber-400 px-2 py-1 text-xs font-semibold text-amber-950 shadow-lg"
        style={place ?? { left: 0, top: 0, visibility: "hidden" }} data-testid="demo-spotlight-label">
        <MousePointerClick className="size-3.5 shrink-0" />{hit.label}
      </div>
    </div>
  );
}
