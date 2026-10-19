// Demo mode's spotlight: which button to press next, shown on the page itself for someone who has never seen the
// app. Each step lists its targets in order (app/modules/storyline/script.py's click path, as selectors of the
// module's own screens); the first one that is on screen, enabled and not yet done gets an amber ring and a label,
// and is scrolled into view. Pressing it changes the page, and the next target lights up. The spotlight never
// clicks: the presenter does.

import { MousePointerClick } from "lucide-react";
import { useEffect, useRef, useState } from "react";
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
    case "call":
      return [
        t("[data-testid=start-call]", "Start the call"),
        t("[data-testid=demo-say-0]", "Say the daughter's first line", said("call-transcript", lines[0])),
        t("[data-testid=demo-say-1]", "Say who the patient is (name, birth date, card)", said("call-transcript", lines[1])),
        t('button[aria-label="Confirm registration"]', "Confirm the registration the AI pre-filled"),
      ];
    case "triage":
      return card("Accept the AI's CTAS 2 suggestion");
    case "imaging":
      return card("Accept the suggested imaging orders");
    case "inr":
      return card("Acknowledge the critical INR");
    case "news2":
      return card("Record your response to NEWS2 7");
    case "coding":
      return card("Confirm the suggested codes");
    case "bed": {
      const exc = step.proposal?.ref?.startsWith("FlowException/") ? step.proposal.ref.split("/")[1] : null;
      return [
        ...(exc ? [t(`[data-exception="${exc}"]`, "Open the Orthopedics exception", has("[data-testid=action-drawer]"))] : []),
        t("[data-testid=action-drawer] [data-testid=approve]", "Approve the actions the AI recommends"),
      ];
    }
    case "medrec": {
      const review = step.step_ai?.items.find((i) => i.kind === "order_review")?.id;
      const opened = has("[data-testid=review-panel]");
      return [
        ...(review ? [t(`[data-testid="queue-item-${review}"]`, "Open her naproxen order (major interaction)", opened)] : []),
        t("[data-testid=queue-search]", `Search her MRN ${data.patient?.mrn ?? ""}`, opened),
        t("[data-testid=review-panel] [data-testid=decision-reason]", "Write why the order should change",
          () => filled("[data-testid=review-panel] [data-testid=decision-reason]")() || has("[data-testid=review-panel] [data-testid=decision]")()),
        t("[data-testid=review-panel] [data-testid=request-change]", "Request a change"),
        t("[data-testid=medrec-panel] [data-testid=item-form] [data-testid=item-reason]", "Give a reason for this discrepancy",
          filled("[data-testid=medrec-panel] [data-testid=item-form] [data-testid=item-reason]")),
        t("[data-testid=medrec-panel] [data-testid=item-form] [data-testid=item-save]", "Save this item"),
        t("[data-testid=demo-cosign]", "Switch to the surgeon to co-sign"),
        t("[data-testid=medrec-panel] [data-testid=sign-button]", "Sign the reconciliation"),
      ];
    }
    case "or":
      return [t(`[data-testid=or-row][data-mrn="${data.patient?.mrn ?? ""}"] [data-testid=adjust-booking]`,
        "Adjust her booking to the predicted time")];
    case "summary": {
      const red = "[data-testid=summary-item][data-status=red]";
      return [
        t(`[data-testid="work-${STAY}"]`, "Open her stay", has("[data-testid=draft-summary], [data-testid=summary-review]")),
        t("[data-testid=draft-summary]", "Draft the discharge summary with AI"),
        t("[data-testid=edit-summary]", "Edit: one item has no source", () => has("[data-testid=save-summary]")() || !q(red)),
        t(`${red} [data-testid=remove-item]`, "Remove the item with no source"),
        t("[data-testid=save-summary]", "Save and check again"),
        t("[data-testid=sign-summary]", "Sign the summary"),
      ];
    }
    case "instructions":
      return [
        t(`[data-testid="work-${STAY}"]`, "Open her stay", has("[data-testid=draft-instructions], [data-testid=instructions-review]")),
        t("[data-testid=draft-instructions]", "Draft the instructions with AI"),
        t("[data-testid=sign-instructions]", "Sign the instructions"),
      ];
    case "followup": {
      const ended = has("[data-testid=followup-ended], [data-testid=followup-transcript]");
      const queueTab = byText("[role=tab]", "Queue");
      return [
        t("[data-testid=place-call]", "Place the follow-up call"),
        t("[data-testid=demo-say-0]", "Answer as the patient: her birth date", said("followup-call-transcript", lines[0])),
        t("[data-testid=demo-say-1]", "Answer: the wound keeps leaking", said("followup-call-transcript", lines[1])),
        t("[data-testid=demo-say-2]", "Answer the remaining questions: nothing else", ended),
        { find: queueTab, label: "Open the nurse's queue", done: () => queueTab()?.getAttribute("aria-selected") === "true" },
        t("[data-testid=followup-list] li", "Open her call (Urgent, on top)", has("[data-testid=patient-words]")),
        t('textarea[aria-label="Nurse note"]', "Write what you did when you called back", filled('textarea[aria-label="Nurse note"]')),
        { find: byText("button", "Save call-back note"), label: "Save the call-back note" },
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

/** The amber ring and its label over the target; clicks go through to the page. */
export function Spotlight({ hit }: { hit: Hit | null }) {
  const [rect, setRect] = useState<DOMRect | null>(null);
  useEffect(() => {
    if (!hit) {
      setRect(null);
      return;
    }
    const r = hit.el.getBoundingClientRect();
    if (r.top < 0 || r.bottom > window.innerHeight) hit.el.scrollIntoView({ block: "center", behavior: "smooth" });
    let frame = 0;
    const follow = () => { // the page scrolls and reflows under it: follow the target, re-rendering only on a move
      const next = hit.el.getBoundingClientRect();
      setRect((prev) => (prev && prev.left === next.left && prev.top === next.top && prev.width === next.width
        && prev.height === next.height ? prev : next));
      frame = window.requestAnimationFrame(follow);
    };
    follow();
    return () => window.cancelAnimationFrame(frame);
  }, [hit]);
  if (!hit || !rect || rect.width === 0) return null;
  const pad = 4;
  const above = rect.top > 48;
  const left = Math.max(8, Math.min(rect.left - pad, window.innerWidth - 300));
  // a button of the guide panel: the label goes to its left, outside the panel, not over the panel's own text
  // (phone width: the panel spans the screen, so it stays above)
  const beside = window.innerWidth >= 640 && !!hit.el.closest("[data-testid=demo-guide]");
  const place = beside ? { right: window.innerWidth - rect.left + pad + 8, top: Math.max(8, rect.top + rect.height / 2 - 13) }
    : above ? { left, top: rect.top - pad - 32 } : { left, top: rect.bottom + pad + 8 };
  return (
    <div className="pointer-events-none fixed inset-0 z-[60]" aria-hidden="true" data-testid="demo-spotlight">
      <div className="absolute animate-pulse rounded-lg ring-4 ring-amber-400 ring-offset-2"
        style={{ left: rect.left - pad, top: rect.top - pad, width: rect.width + 2 * pad, height: rect.height + 2 * pad }} />
      <div className="absolute flex max-w-[18rem] items-center gap-1.5 rounded-md bg-amber-400 px-2 py-1 text-xs font-semibold text-amber-950 shadow-lg"
        style={place} data-testid="demo-spotlight-label">
        <MousePointerClick className="size-3.5 shrink-0" />{hit.label}
      </div>
    </div>
  );
}
