# Self-guided tour: the script (draft for the owner's approval)

Status: **approved by the owner on 2026-10-19** (with the answers under "Owner's decisions" below). The captions
and role cards live in `apps/web/scripts/tour/script.mjs`; this file is their readable copy, kept in step.

## Why a recorded tour

Interviewers open ops.jasonyao.dev by themselves, with nobody guiding them (owner's decision, 2026-10-19). The live
storyline cannot be their first screen: there is one shared story per server, it is "not set up" after the 08:00 UTC
daily reset (casebook 12.15), a reset takes about a minute and leaks about 400 MiB (casebook 7.8), and two visitors
would share one patient. So the front door is a **recorded tour generated from the real app**: per step a
screenshot, one pulsing hotspot and a short caption (Arcade / Navattic style), ending in "try it live".

- **Two tracks:** hospital manager (the patient story) and engineer (what is behind the same story).
- **Length:** at most 12 hotspots per track (completion drops past about 5–8 steps; Arcade advises 9–12), split into
  chapters (multi-flow demos complete better). Captions at most 30 words (Navattic: 25–30), second person, saying
  why the screen matters rather than "click here".
- **Roles:** the visitor never picks a role. Each chapter opens with a role card: who you are, what you decide, what
  your role may see.
- **Honesty:** every screenshot comes from the running app (the capture script, below) with the mock model and
  synthetic data; the footer says so and names the version.

## Track 1: Hospital manager — "One patient, four decisions" (4 chapters, 11 hotspots, about 3 minutes)

Opening card: *Margaret Ellis, 72, takes warfarin. She falls at home and breaks her hip. Follow four of the twelve
moments of her stay. In each one the AI suggests and a named person decides.*

### Chapter 1 · The call (storyline step 1)

Role card: **You are Maya Nakamura, registration clerk.** You register patients. You see who they are and that they
are registered, never why.

| # | Screen | Target (hotspot) | Caption |
| --- | --- | --- | --- |
| 1 | Call centre, during the daughter's call | The call details' identity check: **Verified** | You don't take this call; the AI agent does. It checks her name, birth date and the last four health-card digits before it discusses anything. |
| 2 | Call centre, Requests | The card **ED registration · pre-filled from the call** and its **Confirm registration** button | The agent pre-fills her ED registration from the call, marked AI. Nothing is recorded until you press Confirm: one click instead of retyping, with your name on it. |

### Chapter 2 · No orthopaedic bed (step 5)

Meanwhile (steps 2–4): *triage suggested CTAS 2, her hip X-ray and CT were ordered, an INR of 3.8 was flagged. Each was
suggested by the AI and decided by a nurse or physician.*

Role card: **You are Jordan Lee, bed manager.** You decide for the whole hospital, but you see patients only as MRN
and bed.

| # | Screen | Target | Caption |
| --- | --- | --- | --- |
| 3 | Control Tower | The exception **Orthopedics at 100% occupancy** (high) | She needs an orthopaedic bed and Orthopedics is full. The rule engine raises this exception from live bed data; no model decides that there is a problem. |
| 4 | Control Tower, action drawer | The narrative, labelled **AI-generated** | The AI writes the briefing from the engine's facts only: occupancy, who is boarding, who is ready to leave. It explains the problem; it moves no bed. |
| 5 | Action drawer | The action **Early discharge rounds on Surgery (2 ready)** and **Approve** | Your decision: early discharge rounds on Surgery, where two patients are ready to go. Tasks go to their teams, and the first bed they free is hers. |
| 6 | Control Tower, Surgery unit, her bed | **Hidden for your role** | You see her only as MRN 40720001 in a Surgery bed. Her name, reason for admission, vital signs and orders are hidden: a bed decision doesn't need them. |

### Chapter 3 · The discharge summary (step 9)

Meanwhile (steps 6–8): *the pharmacist caught warfarin with naproxen and a missing home medicine, the OR booking was
stretched to the predicted 115 minutes, and a NEWS2 alert fired on her first night.*

Role card: **You are Dr. Victor Osei, orthopaedic surgeon, her attending.** Only your signature makes the discharge
summary part of her record.

| # | Screen | Target | Caption |
| --- | --- | --- | --- |
| 7 | Documentation, her stay, the AI draft | One line of the draft with its source | The AI drafted her discharge summary. Every line names the record it came from (an order, a result, a note), so you check sources rather than memory. |
| 8 | Same draft | The red item **lisinopril · no matching source** and the blocked **Sign** | Lisinopril appears in a note but in no order. The validator marks it red and blocks your signature: the AI cannot slip an unsupported medicine into her record. |
| 9 | Same draft, after the removal | **Every item traces to its sources** and **Signed by** | You remove it, the validator checks again, and you sign. The summary is final under your name; the draft never was part of her record. |

### Chapter 4 · 48 hours later (step 11)

Meanwhile (step 10): *the ward nurse signed her plain-language instructions: the warfarin diet and the wound red
flags.*

Role card: **You are Vera Uchida, Surgery ward nurse.** Escalations from her follow-up call come to you; the call
itself never gives medical advice.

| # | Screen | Target | Caption |
| --- | --- | --- | --- |
| 10 | Follow-up calls, her call | Her answer and the agent's **A nurse will call you back within 30 minutes** | Two days home, the AI calls her with questions from her signed instructions. "The gauze is soaked through": a rule, not the model, escalates at once. |
| 11 | Follow-up calls, Queue | Her escalation on top: **Urgent · Heavy wound drainage** | Her escalation tops your queue, in her own words. You call her back and record it. By design the call would rather over-escalate than miss a wound problem. |

End screen: **Twelve suggestions, twelve people's decisions.** One line for the eight steps not shown (triage, imaging
orders, a critical INR, medication reconciliation, the OR booking, a night-time alert, patient instructions, coding).
Buttons: **Try it live** (the sign-in page) · **See how it works** (the engineer track).

## Track 2: Engineer — "Behind the same story" (4 chapters, 11 hotspots, about 3 minutes)

Opening card: *The same patient, the same twelve steps, seen from underneath: the audit trail, the agent gateway, the
validator, the evals and the workflow.*

### Chapter 1 · Every step leaves a trail

Role card: **You are Casey Brooks, administrator.** You see what the clinical roles cannot: each step's trail and the
audit log behind it.

| # | Screen | Target | Caption |
| --- | --- | --- | --- |
| 1 | Hospital storyline, step 1 | The trail's three columns: **AI proposed · Signed by · Audit** | Each step records three things: what the AI proposed, who signed, and the audit events. Step 1: the registration the agent pre-filled, confirmed by Maya Nakamura. |
| 2 | Audit log, Approvals | The clerk's confirmation event | The audit log is append-only and SHA-256 hash-chained. Each event says who, which role, when, which record and why; the patient appears only as a keyed hash. |

### Chapter 2 · Agents work through a gateway

Role card: **You are Casey Brooks, administrator.** You see every agent run: the tools it called, their side-effect
levels, and the person who decided.

| # | Screen | Target | Caption |
| --- | --- | --- | --- |
| 3 | AI agents, Runs, the discharge-summary run (step 9) | The trace: agent, model, prompt version, on behalf of, Provenance, human action | Every agent run leaves a trace: model and prompt version, on whose behalf it ran, inputs and outputs, its FHIR Provenance, and the human decision that followed. |
| 4 | Same trace | The tool calls with **Level**, **Status** and args hash | Each tool call passes the gateway with a side-effect level. The gateway authorises from the registry and the signed-in caller, never from what the model claims. |
| 5 | Safety tiers | The three risk tiers with their signers | Every agent and module is registered with a risk tier (ops, documentation, clinical decision support) and the role that must sign. The registry decides, not the screen. |
| 6 | Safety tiers | The tier × side-effect matrix | Second axis: tools by side effect (read, recommend, action, privileged). An ops agent can reach a privileged tool, like a bed move; that call still needs a recorded approval. |

### Chapter 3 · The validator is code (step 9)

Role card: **You are Dr. Victor Osei, the surgeon,** on the screen from the manager track. This time, look at the
evidence behind each line.

| # | Screen | Target | Caption |
| --- | --- | --- | --- |
| 7 | Documentation, the AI draft | The source panel with a line's evidence | Each line carries references to FHIR resources. Select a line and the source panel highlights what it cites, here her home warfarin, so every claim can be checked. |
| 8 | Same draft | The red lisinopril and the reason signing is blocked | The validator is deterministic code, not another model. A medicine with no matching order turns red, and the signing service refuses the draft by any path, not just this button. |

### Chapter 4 · Evals and the durable journey

Role card: **You are Casey Brooks, administrator,** on AI Ops and Workflows: how a change gets in, and how a stay of
several days survives a restart.

| # | Screen | Target | Caption |
| --- | --- | --- | --- |
| 9 | AI Ops, Eval Center | The agents with their thresholds and eval status | One command runs every agent's eval set against its threshold. Mock-model runs are baselines: they prove the pipeline and never pass the release gate on their own. |
| 10 | AI Ops, Registries | **prompt gate passes** and the prompt versions | Prompts are versioned files. Change one without an eval run that meets the agent's threshold, and the prompt gate fails the test suite. |
| 11 | Workflows, `journey-stay-j0001` | The journey's timeline | Her stay is one durable Temporal workflow: sign-offs are signals, the 48-hour follow-up is a timer. Kill the worker mid-step and it resumes without repeating a side effect. |

End screen: **Every number in this tour comes from the app's own trace store and eval files.** Buttons: **Try it
live** · **The patient's story** (the manager track).

## Owner's decisions (2026-10-19)

1. **Script:** approved as drafted.
2. **Workflow timeline (engineer hotspot 11):** the public server runs offline (no Temporal), so the whole capture
   runs locally with Temporal on, and that screen says "Captured locally with Temporal on; the public demo runs
   without Temporal (offline mode)". (Not chosen: the storyline page's "Temporal off" card instead, or no hotspot.)
3. **Builder's name on the landing page:** none; the product name only.
4. **Step 12 (coding, played by the registration clerk):** only as "coding" in the end screen's one-line list, with
   no role named (it is a roadmap stub, not a highlight).

## How it is built

**Where things are.** The script's text: `apps/web/scripts/tour/script.mjs` (tracks, chapters, role cards, captions;
it checks its own limits: at most 12 hotspots, captions of at most 30 words). The capture:
`apps/web/scripts/tour/capture.mjs`. Its output, committed: `apps/web/public/tour/manager.json`, `engineer.json` and
`img/*.webp` (1440 × 900 screenshots). The viewer and the landing page: `apps/web/src/features/tour/`. The test:
`apps/web/e2e/tour.spec.ts`.

**The front door.** A signed-out visitor at `/` sees the landing page (`LandingPage.tsx`): what this is, that the
data is synthetic, each tour's length from its manifest, the manager tour (primary), the engineer track and "Explore
the live app" (the sign-in page, with its Casey Brooks hint and a link back to the tour). Signed in, `/` is the home
page as before. `/tour/manager` and `/tour/engineer` (`TourPage.tsx`) sit outside the session: no sign-in, and not one
`/api` request (the manifest and images are static files). Each chapter opens with its role card; each step shows the
screenshot fitted to the window (on a phone it zooms towards the hotspot, with a "Whole screen" switch), the pulsing
hotspot (click it, Next, or →), the caption beside it (in a sheet under it on a phone), progress ("Chapter 2 of 4 ·
5/11"), Back/Next and ←/→, Escape to leave. The footer says "Captured from the running app (version …); all data
synthetic". The end screen leads to the live app or the other track.

**Re-capturing** (after a change to a screen the tour shows, e.g. once the demo-mode fixes land). About 35 minutes
with Temporal on (the clock's fast-forwards are slower then), about 10 without; Windows notes as in the repository's environment notes (own database, a port outside the
reserved range, `UV_NO_SYNC=1` or `uv run --no-sync`).

1. Once: an empty database of its own, and a Temporal namespace of its own on the dev Temporal server (`docker
   compose --profile workflows up -d temporal`), because a storyline reset ends every running workflow of its
   namespace:

       docker exec imaging-ops-copilot-postgres-1 psql -U ioc -d postgres -c 'CREATE DATABASE "ioc_tour"'
       docker exec imaging-ops-copilot-temporal-1 temporal operator namespace create --namespace tour-capture --address 127.0.0.1:7233

2. The API, in `apps/api`, with the mock model and Temporal on (the worker in the API, the demo's short timers):

       DATABASE_URL=postgresql+psycopg://ioc:ioc@127.0.0.1:5433/ioc_tour LLM_PROVIDER=mock        TEMPORAL_ADDRESS=127.0.0.1:7233 TEMPORAL_NAMESPACE=tour-capture TEMPORAL_WORKER_IN_API=1        WORKFLOW_FOLLOWUP_DELAY_S=20 WORKFLOW_SIGNOFF_TIMEOUT_S=600 WORKFLOW_ESCALATION_TIMEOUT_S=600        uv run uvicorn app.main:app --port 9441

   (plus the PHI keys: the test-only ones from `tests/conftest.py` will do; no model key is needed).
3. The one command, in `apps/web`:

       npm run tour:capture -- --api http://127.0.0.1:9441

   It starts its own Vite (port 5199, `--web <url>` uses a running one), resets the storyline (the English story),
   plays step 1 as the clerk, has the demo's automation do steps 2–4 (`POST /api/storyline/steps/bed/jump`), moves
   the clock and plays step 5 as the bed manager, and so on through steps 9 and 11; it takes the engineer track's
   screens before step 12 (so the audit log shows the story up to the follow-up), lets the automation do step 12,
   and takes the journey's timeline last. It refuses an API that is not on the mock model. Without Temporal it
   still captures everything else and leaves the timeline hotspot out (and says so).

`npm run tour:capture -- --text-only` rewrites only the captions and role cards from `script.mjs` into the manifests,
keeping the screenshots (after a wording change). `--from <stage>` (`bed`, `summary`, `followup`, `trail`, `engineer`,
`coding`, `journey`) resumes a run that broke at that stage, without a reset, keeping the shots taken before it: the
manifests are rewritten after every shot.
