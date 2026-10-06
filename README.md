# Imaging Ops Copilot

A demo AI toolkit for an outpatient medical imaging center: scheduling, chest X-ray report drafting, front desk automation, requisition triage, radiology operations and compliance. The full scope is 21 systems delivered in 5 phases; the first batch (systems 1–4) is built.

> All data is synthetic. No real patient information is used anywhere. AI output is always a draft or a suggestion that a person must confirm. Nothing in this repository is a medical device or gives diagnostic advice.

## What's in the first batch

| # | System | What you can demo |
| --- | --- | --- |
| 1 | Backend Infrastructure | One-click demo login per role, role and site checks (403 + audit), hash-chained audit log, Claude call layer with de-identification, schema validation, retry and graceful degradation, AI usage dashboard, mock SMS / email / OHIP services |
| 2 | Scheduling Command Center | Live utilization by site and scanner, site × hour heatmap, cancellation → ranked waitlist backfill in under a second, multilingual SMS offers (first to confirm wins), cross-site redirect suggestions, no-show model with hold-out AUC and top 3 risk factors per appointment |
| 3 | Report Generator | Chest X-ray worklist, AI draft in a fixed schema, section-by-section accept / edit / delete, sign-off, confirmed urgent findings handed to critical results, drafts never visible to referrers (enforced server side) |
| 4 | Front Desk Automation | Phone agent in the browser (speech in Chrome, or typed) with identity verification, reschedule, cancel, prep instructions, directions and hand-off to staff; automatic reminders 72 h and 24 h before; SMS replies that confirm or cancel; mobile pre-registration page in 4 languages with mock OHIP check |

The home page walks through a single storyline: a chest X-ray flags a possible nodule, the report is signed, another patient phones to cancel a CT, and the freed slot is backfilled with the first patient, who gets her messages in Chinese.

## Run it

Requirements: Python 3.12 with [uv](https://docs.astral.sh/uv/), Node 20+.

```bash
# backend (http://127.0.0.1:8000)
cd apps/api
uv sync
uv run uvicorn app.main:app --reload --port 8000

# frontend (http://localhost:5173), in a second terminal
cd apps/web
npm install
npm run dev
```

Open http://localhost:5173 and pick a role. "Reset demo" in the header regenerates all data.

Without an API key every AI feature runs on built-in mock outputs, so the whole demo works offline. To use Claude, copy `apps/api/.env.example` to `apps/api/.env` and set `ANTHROPIC_API_KEY`, then restart the backend.

### Tests

```bash
cd apps/api && uv run pytest -q          # 23 backend tests
cd apps/web && npx playwright test       # storyline end-to-end (starts both servers if needed)
```

## Tech stack

React + TypeScript (Vite, Tailwind, TanStack Query) · Python 3.12 FastAPI · Pydantic · scikit-learn · Anthropic Python SDK · Playwright

For the demo, data lives in memory and is regenerated from a seeded generator; external systems are mocked in process. See [docs/PROGRESS.md](docs/PROGRESS.md) for how this differs from the full spec.

## Repository layout

```text
apps/
  api/      FastAPI backend: app/core, app/llm, app/modules/<system>, app/integrations
  web/      React frontend: src/features/<system>
  kg-qa/    Sub-project: medical knowledge-graph Q&A
docs/       SPEC.md, PROGRESS.md
```

## Sub-projects

- [apps/kg-qa](apps/kg-qa) — medical knowledge-graph Q&A (LangGraph agent + Neo4j + PostgreSQL), imported from an earlier project together with its history. It runs on its own; see its README (in Chinese).

## Docs

- [docs/SPEC.md](docs/SPEC.md) — full specification (in Chinese)
- [docs/PROGRESS.md](docs/PROGRESS.md) — progress, demo-scope decisions and what's next
