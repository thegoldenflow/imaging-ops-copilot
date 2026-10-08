# Imaging Ops Copilot

A demo AI toolkit for an outpatient medical imaging center: scheduling, chest X-ray report drafting, front desk automation, requisition triage, radiology operations and compliance. The full scope is 21 systems delivered in 5 phases; all five phases (systems 1–21) are built in a lean demo form.

> All data is synthetic. No real patient information is used anywhere. AI output is always a draft or a suggestion that a person must confirm. Nothing in this repository is a medical device or gives diagnostic advice.

## What's in the first batch

| # | System | What you can demo |
| --- | --- | --- |
| 1 | Backend Infrastructure | One-click demo login per role, role and site checks (403 + audit), hash-chained audit log, Claude call layer with de-identification, schema validation, retry and graceful degradation, AI usage dashboard, mock SMS / email / OHIP services |
| 2 | Scheduling Command Center | Live utilization by site and scanner, site × hour heatmap, cancellation → ranked waitlist backfill in under a second, multilingual SMS offers (first to confirm wins), cross-site redirect suggestions, no-show model with hold-out AUC and top 3 risk factors per appointment |
| 3 | Report Generator | Chest X-ray worklist, AI draft in a fixed schema, section-by-section accept / edit / delete, sign-off, confirmed urgent findings handed to critical results, drafts never visible to referrers (enforced server side) |
| 4 | Front Desk Automation | Phone agent in the browser (speech in Chrome, or typed) with identity verification, reschedule, cancel, prep instructions, directions and hand-off to staff; automatic reminders 72 h and 24 h before; SMS replies that confirm or cancel; mobile pre-registration page in 4 languages with mock OHIP check |

### Phase 2: requisition intake pipeline

| # | System | What you can demo |
| --- | --- | --- |
| – | Requisition extraction | One Claude call per requisition; every field shows its source quote and confidence, highlighted in the original text; low-confidence fields in yellow; staff corrections are audited |
| 5 | Priority Triage | P1–P4 with rationale and red flags, queue ordered by days left to target, radiologist confirm or override with a mandatory reason, live AI-vs-radiologist agreement |
| 6 | Protocol Assignment | Retrieval over a 20-protocol library, Claude picks a primary and two alternatives, one-click approval sets the slot length, adoption stats |
| 7 | Contrast & Renal Checker | Rules with director-set thresholds decide pass / needs eGFR / needs premedication / needs review with written basis; changing a threshold recomputes everything |
| 8 | MRI Safety Screening | Patient questionnaire in 4 languages, Claude reads free-text implant descriptions, flags block confirmation until a technologist reviews |
| 9 | Patient Prep Instructions | AI-drafted translations need approval; only approved text is sent, at booking and 48 h before |
| 10 | Prior Imaging Retrieval | Outside priors requested after booking, retried with backoff against flaky mock archives, imported and linked to the exam |

### Phase 3: radiology operations

| # | System | What you can demo |
| --- | --- | --- |
| 11 | Reporting Backlog & Turnaround | Unreported studies by site, exam type, priority and age; turnaround (signed − completed) against per-priority targets with at-risk and overdue flags; each radiologist's queue; reassignment suggestions to on-shift, credentialed readers (audited); dictate and sign from the queue |
| 12 | Critical Results Tracker | Confirmed findings open a case; the ordering physician is called, re-notified by phone and fax, then the case escalates to the medical director; acknowledgement records who, how and when (staff or referrer portal); no closing without it; full timeline |
| 13 | Peer Review / QA | Nightly sampling at a set rate, blind assignment never back to the original reader, concur / minor / significant grading with discrepancy type, QA report by radiologist and exam type for the QA lead only, CSV export |
| 14 | CT Dose Monitoring | Dose record per CT shaped like a DICOM dose SR (CTDIvol, DLP per irradiation event), reference levels per protocol, exceedance review list, weekly trends by scanner and protocol |

### Phase 4: business and compliance

| # | System | What you can demo |
| --- | --- | --- |
| 15 | Inventory Manager | Contrast and consumables per site with lots and expiry; completing an exam deducts what it used (first-expiring lot first); low-stock alerts with a drafted purchase order, near-expiry and expired lots, receiving and count corrections |
| 16 | Referral Analytics | Referral volume by referrer, specialty, modality, site and week with filters and trends; a visit list of referrers whose volume dropped; a Claude weekly summary in which every number is filled in from the queries and links to its dashboard tile |
| 17 | Referring Physician Portal | Separate referrer login; online requisitions (form plus free text) go through the intake pipeline; referrers see only their own patients, anything else is 403 and audited |
| 18 | Billing & Claims QA | Completed exams reconciled against claims (synthetic fee codes): not submitted, duplicate, wrong code, wrong amount, rejected, billed but not performed; work queue with outcomes; CSV export |
| 19 | Patient Feedback | Survey by SMS after every exam, mobile page in 4 languages; Claude labels sentiment and themes for staff to confirm; low ratings alert the site manager at once; ratings and themes by site and week |
| 20 | PHIPA Access Monitoring | Rules over the hash-chained audit log (cross-site, after hours, bulk, same family name, own record, repeated refusals) with risk scores and evidence; investigation trail; compliance report and export |
| 21 | Inspection Readiness Hub | Versioned policies, equipment records, staff credentials and quality records; reminders at 60/30/7 days and when overdue; inspection checklist; policy Q&A that cites the exact section or says the documents do not cover it |

### Added: clinical knowledge Q&A

| System | What you can demo |
| --- | --- |
| Clinical knowledge (from the `apps/kg-qa` sub-project, in-memory version) | Staff ask about a disease (symptoms, tests, drugs, treatments, complications) or list symptoms to see matching diseases, in English or Chinese. A side panel on each requisition is prefilled with the extracted clinical indication; a full page keeps recent questions. Lookups use fixed query templates over the medical knowledge graph; every answer statement cites the graph facts it uses, checked by the server; questions the graph does not cover get a plain "no facts" reply. Reference for technologists, radiologists and the medical director only, not a diagnosis; front desk and referrers have no access |

Phase 3 uses no AI; it is about data, workflow and an audit trail. Exams finish when a technologist marks them done (Scheduling → Appointments), which creates the study, assigns a reader and records CT dose.

Each AI feature has an eval set in `evals/` (`cd apps/api && uv run python -m app.modules.evals.run`), shown on the AI evaluations page.

The home page walks through four storylines. The first: a chest X-ray flags a possible nodule, the report is signed, another patient phones to cancel a CT, and the freed slot is backfilled with the first patient, who gets her messages in Chinese.

## Run it

Requirements: Python 3.12 with [uv](https://docs.astral.sh/uv/), Node 20+, Docker (for PostgreSQL).

```bash
# database (PostgreSQL 16 on 127.0.0.1:5433), from the repository root
docker compose up -d postgres

# backend (http://127.0.0.1:8000)
cd apps/api
cp .env.example .env
uv run python -m app.core.db.crypto      # prints the two PHI keys: paste them into .env
uv sync
uv run uvicorn app.main:app --reload --port 8000   # migrates and seeds an empty database on start

# frontend (http://localhost:5173), in a second terminal
cd apps/web
npm install
npm run dev
```

Open http://localhost:5173 and pick a role. Data and logins persist across restarts; "Reset demo" in the header regenerates all data (`uv run python -m app.seed` does the same from the command line).

Without an API key every AI feature runs on built-in mock outputs, so the whole demo works offline. To use Claude, set `ANTHROPIC_API_KEY` in `apps/api/.env` and restart the backend.

### Switching to Gemini on Google Cloud Agent Platform

Claude is the default. To run every AI feature on Gemini instead, put these lines in `apps/api/.env` and restart the backend:

```bash
LLM_PROVIDER=gemini
GOOGLE_AGENT_PLATFORM_API_KEY=your-agent-platform-key
# Optional: models per tier (all default to gemini-3.5-flash)
# GEMINI_MODEL_REASONING=gemini-3.5-flash
# GEMINI_MODEL_FAST=gemini-3.5-flash
# GEMINI_MODEL_VOICE=gemini-3.5-flash
```

`LLM_PROVIDER` takes `anthropic`, `gemini` or `mock`. Left unset, the app uses Claude when `ANTHROPIC_API_KEY` is set and mock outputs otherwise; a provider without its key also falls back to mock. Gemini uses the Google Gen AI SDK in Agent Platform / Vertex AI mode (not Google AI Studio or the Gemini Developer API), while retaining the same call layer for de-identification, schema validation, call logging and degradation. The header shows "AI: Gemini via Vertex AI API", and `uv run python -m app.modules.evals.run` records Gemini results as mode `gemini`. See [docs/PROGRESS.md](docs/PROGRESS.md) for limits.

### Docker / server

`docker compose up -d --build` runs PostgreSQL, the API and the web front end (nginx serving the build and proxying `/api`) on http://localhost, reading `apps/api/.env` (the PHI keys are required). For the server (Docker Hub images behind the host's Caddy) see [docs/DEPLOY.md](docs/DEPLOY.md).

### Tests

```bash
cd apps/api && uv run pytest -q          # backend tests; need the database container (they use their own ioc_test database)
cd apps/web && npx playwright test       # 6 end-to-end specs: one per phase, the model-provider badge and clinical knowledge (starts both servers if needed)
```

## Tech stack

React + TypeScript (Vite, Tailwind, TanStack Query) · Python 3.12 FastAPI · Pydantic · PostgreSQL 16 with SQLAlchemy 2 and Alembic · scikit-learn · Anthropic Python SDK (optional Google Gen AI SDK for Gemini) · Playwright

Data lives in PostgreSQL. Patient identifiers and the free text that repeats them (requisition letters, outgoing messages, call transcripts) are encrypted per field with AES-GCM before they reach the database; lookups by birth date or health card use a keyed blind index. The audit log is a hash chain in a table that refuses UPDATE and DELETE. Background steps (message dispatch, critical-result escalation, prior retrieval) claim due rows with `SELECT ... FOR UPDATE SKIP LOCKED`, so they continue after a restart. The demo data comes from a seeded generator; external systems are mocked in process, and there is no Temporal or Orthanc. See [docs/PROGRESS.md](docs/PROGRESS.md) for how this differs from the full spec.

## Repository layout

```text
apps/
  api/      FastAPI backend: app/core, app/llm, app/modules/<system>, app/integrations
  web/      React frontend: src/features/<system>
  kg-qa/    Sub-project: medical knowledge-graph Q&A
evals/      Eval datasets and results
docs/       SPEC.md, PROGRESS.md
```

## Sub-projects

- [apps/kg-qa](apps/kg-qa) — medical knowledge-graph Q&A (LangGraph agent + Neo4j + PostgreSQL), imported from an earlier project together with its history. It runs on its own; see its README (in Chinese). The suite's Clinical knowledge page reads its committed graph data (read only) into memory, so it works without Neo4j, PostgreSQL or an embedding model; `apps/kg-qa` itself is unchanged and stays the full version.

## Docs

- [docs/SPEC.md](docs/SPEC.md) — full specification (in Chinese)
- [docs/PROGRESS.md](docs/PROGRESS.md) — progress, demo-scope decisions and what's next
