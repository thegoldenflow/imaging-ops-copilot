// The self-guided tour's script (demo/tour.md, approved by the owner on 2026-10-19): two tracks, chapters with a role
// card each, and per hotspot the screen, the shot it is on (capture.mjs takes the shots) and the caption. The capture
// writes this text with the shots' images and boxes into apps/web/public/tour/<track>.json; `--text-only` rewrites
// the text alone. demo/tour.md is the readable copy: change both together.

export const LIMITS = { hotspots: 12, captionWords: 30 };

export const TEMPORAL_NOTE = "Captured locally with Temporal on; the public demo runs without Temporal (offline mode).";

export const TRACKS = {
  manager: {
    title: "One patient, four decisions",
    audience: "Hospital manager",
    intro: "Margaret Ellis, 72, takes warfarin. She falls at home and breaks her hip. Follow four of the twelve moments "
      + "of her stay. In each one the AI suggests and a named person decides.",
    other: "engineer",
    end: {
      title: "Twelve suggestions, twelve people's decisions.",
      text: "The eight steps not shown work the same way: triage, imaging orders, a critical INR, medication "
        + "reconciliation, the OR booking, a night-time alert, patient instructions and coding.",
      otherLabel: "See how it works",
    },
    chapters: [
      {
        id: "call", title: "The call", step: 1,
        role: { name: "Maya Nakamura", title: "registration clerk",
          text: "You register patients. You see who they are and that they are registered, never why." },
      },
      {
        id: "bed", title: "No orthopaedic bed", step: 5,
        meanwhile: "Since the call (steps 2–4): triage suggested CTAS 2, her hip X-ray and CT were ordered, and an INR of "
          + "3.8 was flagged. Each was suggested by the AI and decided by a nurse or physician.",
        role: { name: "Jordan Lee", title: "bed manager",
          text: "You decide for the whole hospital, but you see patients only as MRN and bed." },
      },
      {
        id: "summary", title: "The discharge summary", step: 9,
        meanwhile: "Steps 6–8: the pharmacist caught warfarin with naproxen and a missing home medicine, the OR booking "
          + "was stretched to the predicted 115 minutes, and a NEWS2 alert fired on her first night.",
        role: { name: "Dr. Victor Osei", title: "orthopaedic surgeon, her attending",
          text: "Only your signature makes the discharge summary part of her record." },
      },
      {
        id: "followup", title: "48 hours later", step: 11,
        meanwhile: "Step 10: the ward nurse signed her plain-language instructions: the warfarin diet and the wound red "
          + "flags.",
        role: { name: "Vera Uchida", title: "Surgery ward nurse",
          text: "Escalations from her follow-up call come to you; the call itself never gives medical advice." },
      },
    ],
    steps: [
      { id: "verified", chapter: "call", screen: "Call centre", shot: "call-verified", target: "verified",
        caption: "You don't take this call; the AI agent does. It checks her name, birth date and the last four "
          + "health-card digits before it discusses anything." },
      { id: "registration", chapter: "call", screen: "Call centre", shot: "call-registration", target: "card",
        caption: "The agent pre-fills her ED registration from the call, marked AI. Nothing is recorded until you press "
          + "Confirm: one click instead of retyping, with your name on it." },
      { id: "exception", chapter: "bed", screen: "Control Tower", shot: "bed-exception", target: "exception",
        caption: "She needs an orthopaedic bed and Orthopedics is full. The rule engine raises this exception from live "
          + "bed data; no model decides that there is a problem." },
      { id: "narrative", chapter: "bed", screen: "Control Tower · action drawer", shot: "bed-drawer", target: "narrative",
        caption: "The AI writes the briefing from the engine's facts only: occupancy, who is boarding, who is ready to "
          + "leave. It explains the problem; it moves no bed." },
      { id: "approve", chapter: "bed", screen: "Control Tower · action drawer", shot: "bed-action", target: "action",
        caption: "Your decision: early discharge rounds on Surgery, where two patients are ready to go. Tasks go to "
          + "their teams, and the first bed they free is hers." },
      { id: "hidden", chapter: "bed", screen: "Control Tower · Surgery beds", shot: "bed-hidden", target: "hidden",
        caption: "You see her only as MRN 40720001 in a Surgery bed. Her name, reason for admission, vital signs and orders "
          + "are hidden: a bed decision doesn't need them." },
      { id: "sources", chapter: "summary", screen: "Documentation · discharge summary", shot: "summary-sources",
        target: "item",
        caption: "The AI drafted her discharge summary. Every line names the record it came from (an order, a result, "
          + "a note), so you check sources rather than memory." },
      { id: "blocked", chapter: "summary", screen: "Documentation · discharge summary", shot: "summary-blocked",
        target: "red",
        caption: "Lisinopril appears in a note but in no order. The validator marks it red and blocks your signature: "
          + "the AI cannot slip an unsupported medicine into her record." },
      { id: "signed", chapter: "summary", screen: "Documentation · discharge summary", shot: "summary-signed",
        target: "signed",
        caption: "You remove it, the validator checks again, and you sign. The summary is final under your name; the "
          + "draft never was part of her record." },
      { id: "escalation", chapter: "followup", screen: "Follow-up calls · her call", shot: "followup-call",
        target: "escalation",
        caption: "Two days home, the AI calls her with questions from her signed instructions. \"The gauze is soaked "
          + "through\": a rule, not the model, escalates at once." },
      { id: "queue", chapter: "followup", screen: "Follow-up calls · queue", shot: "followup-queue", target: "top",
        caption: "Her escalation tops your queue, in her own words. You call her back and record it. By design the call "
          + "would rather over-escalate than miss a wound problem." },
    ],
  },
  engineer: {
    title: "Behind the same story",
    audience: "Engineer",
    intro: "The same patient and the same twelve steps, seen from underneath: the audit trail, the agent gateway, the "
      + "validator, the evals and the workflow.",
    other: "manager",
    end: {
      title: "Every number here comes from the app's own trace store and eval files.",
      text: "Mock-model runs are baselines that prove the pipeline, not clinical performance. All data is synthetic.",
      otherLabel: "The patient's story",
    },
    chapters: [
      {
        id: "trail", title: "Every step leaves a trail",
        role: { name: "Casey Brooks", title: "administrator",
          text: "You see what the clinical roles cannot: each step's trail and the audit log behind it." },
      },
      {
        id: "gateway", title: "Agents work through a gateway",
        role: { name: "Casey Brooks", title: "administrator",
          text: "You see every agent run: the tools it called, their side-effect levels, and the person who decided." },
      },
      {
        id: "validator", title: "The validator is code", step: 9,
        role: { name: "Dr. Victor Osei", title: "orthopaedic surgeon",
          text: "The screen from the manager track. This time, look at the evidence behind each line." },
      },
      {
        id: "evals", title: "Evals and the durable journey",
        role: { name: "Casey Brooks", title: "administrator",
          text: "On AI Ops and Workflows: how a change gets in, and how a stay of several days survives a restart." },
      },
    ],
    steps: [
      { id: "trail", chapter: "trail", screen: "Hospital storyline · step 1", shot: "story-trail", target: "trail",
        caption: "Each step records three things: what the AI proposed, who signed, and the audit events. Step 1: the "
          + "registration the agent pre-filled, confirmed by Maya Nakamura." },
      { id: "audit", chapter: "trail", screen: "Audit log · approvals", shot: "audit-approvals", target: "row",
        caption: "The audit log is append-only and SHA-256 hash-chained. Each event says who, which role, when, which "
          + "record and why; the patient appears only as a keyed hash." },
      { id: "trace", chapter: "gateway", screen: "AI Ops · an agent run's trace", shot: "agent-trace", target: "trace",
        caption: "Every agent run leaves a trace: model and prompt version, on whose behalf it ran, inputs and outputs, "
          + "its FHIR Provenance, and the human decision that followed." },
      { id: "tools", chapter: "gateway", screen: "AI Ops · an agent run's trace", shot: "agent-tools", target: "tools",
        caption: "Each tool call passes the gateway with a side-effect level. The gateway authorises from the registry "
          + "and the signed-in caller, never from what the model claims." },
      { id: "tiers", chapter: "gateway", screen: "Safety tiers", shot: "safety-tiers", target: "tiers",
        caption: "Every agent and module is registered with a risk tier (ops, documentation, clinical decision "
          + "support) and the role that must sign. The registry decides, not the screen." },
      { id: "reach", chapter: "gateway", screen: "Safety tiers", shot: "safety-reach", target: "reach",
        caption: "Second axis: tools by side effect (read, recommend, action, privileged). An ops agent can reach a "
          + "privileged tool, like a bed move; that call still needs a recorded approval." },
      { id: "evidence", chapter: "validator", screen: "Documentation · discharge summary", shot: "summary-sources",
        target: "source",
        caption: "Each line carries references to FHIR resources. Select a line and the source panel highlights what it "
          + "cites, here her home warfarin, so every claim can be checked." },
      { id: "refused", chapter: "validator", screen: "Documentation · discharge summary", shot: "summary-why",
        target: "why",
        caption: "The validator is deterministic code, not another model. A medicine with no matching order turns red, "
          + "and the signing service refuses the draft by any path, not just this button." },
      { id: "evalcenter", chapter: "evals", screen: "AI Ops · Eval Center", shot: "evals-agents", target: "agents",
        caption: "One command runs every agent's eval set against its threshold. Mock-model runs are baselines: they "
          + "prove the pipeline and never pass the release gate on their own." },
      { id: "gate", chapter: "evals", screen: "AI Ops · Registries", shot: "evals-prompts", target: "gate",
        caption: "Prompts are versioned files. Change one without an eval run that meets the agent's threshold, and the "
          + "prompt gate fails the test suite." },
      { id: "journey", chapter: "evals", screen: "Workflows · her journey", shot: "journey", target: "timeline",
        caption: "Her stay is one durable Temporal workflow: sign-offs are signals, the 48-hour follow-up is a timer. "
          + "Kill the worker mid-step and it resumes without repeating a side effect.",
        note: TEMPORAL_NOTE, temporal: true },
    ],
  },
};

const words = (s) => s.trim().split(/\s+/).length;

/** The script's own limits: at most 12 hotspots per track, captions of at most 30 words, known chapters. */
export function checkScript() {
  const problems = [];
  for (const [key, t] of Object.entries(TRACKS)) {
    if (t.steps.length > LIMITS.hotspots) problems.push(`${key}: ${t.steps.length} hotspots (max ${LIMITS.hotspots})`);
    const chapters = new Set(t.chapters.map((c) => c.id));
    for (const s of t.steps) {
      if (words(s.caption) > LIMITS.captionWords) problems.push(`${key}/${s.id}: ${words(s.caption)} words`);
      if (!chapters.has(s.chapter)) problems.push(`${key}/${s.id}: unknown chapter ${s.chapter}`);
    }
  }
  return problems;
}
