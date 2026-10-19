# Imaging Ops Copilot

One healthcare AI operations platform running many governed clinical and operational workflows: an outpatient
imaging centre (21 systems) and a synthetic hospital around it (EHR, Control Tower, medication safety, clinical
documentation, patient voice services, durable workflows, AI Ops). Every AI function is registered with a risk tier
and a required signer, runs through one tool gateway and leaves an audit trail.

> All data is synthetic. No real patient information is used anywhere. AI output is always a draft or a suggestion
> that a person confirms. Eval numbers come from synthetic cases and show that the pipeline works, not clinical
> performance. Nothing in this repository is a medical device or gives medical advice.

**New here?** Follow [Run it](#run-it) (about 25 minutes the first time, most of it downloads and data generation),
sign in as one of the [demo people](#demo-logins), and walk the [hospital storyline](#the-two-storylines). The
architecture in two pages: **Safety tiers** and **Roadmap** in the app, or [docs/safety-tiers.md](docs/safety-tiers.md).
All docs: [docs/README.md](docs/README.md).

## What's inside

| Area | What you can demo | Where |
| --- | --- | --- |
| Hospital storyline (spec 8.2) | One synthetic patient (72, on warfarin, hip fracture) from the phone call to the 48-hour follow-up and coding, in 12 steps and about 10 minutes; at every step the AI proposes, a person signs, the audit trail records it. By default she speaks English; the Mandarin-speaking patient of spec 8.2 (with English subtitles in demo mode) is chosen at the reset | **Hospital storyline** |
| Control Tower (7.1) | ED, bed and OR boards on a simulated hospital clock; four predictive models with their reasons; a rule engine raises exceptions, the AI narrates them from the engine's facts, a bed manager approves the actions | **Control Tower** |
| Medication safety (7.2) | Rule layer (interactions, renal dosing, allergies, dose limits, duplicates) plus AI commentary with evidence; admission and discharge medication reconciliation, signed by a pharmacist and co-signed by a physician | **Medication safety** |
| Documentation (7.3) | Discharge summary where every line cites a FHIR source and an untraceable medicine blocks the signature; patient instructions in English, Simplified and Traditional Chinese from one structure; nursing handoff (SBAR) | **Documentation** |
| Voice services (7.4) | Hospital call centre (identity check, reschedule, cancel, FAQ, clinical questions to the nurse line) and the 48-hour follow-up call with red-flag escalation, in English and Chinese | **Call centre**, **Follow-up calls** |
| Platform (6.1–6.6) | FHIR R4 store behind one gateway, role-based access with break-glass, consent, free-text de-identification, an agent and tool registry with four side-effect levels, idempotent tool calls with traces and Provenance, Temporal workflows that survive a worker restart, an eval runner with a release gate, cost and failure alerts | **Patients**, **Review queue**, **Workflows**, **AI agents**, **AI Ops**, **Audit log** |
| Architecture (8.1, 8.3) | The three risk tiers built live from the registries, the tool side-effect levels, guardrails with their latest eval results, honest boundaries; 14 roadmap modules the platform could grow | **Safety tiers**, **Roadmap** |
| Imaging centre (phases 0–4) | Scheduling command centre, chest X-ray report drafts, phone agent, requisition intake (extraction, triage, protocol, contrast, MRI safety, prep, priors), radiology operations, inventory, referral analytics, referrer portal, billing QA, patient feedback, PHIPA access monitoring, inspection readiness, clinical knowledge Q&A | **Home** and the Operations, Intake, Radiology ops and Business sections |

The full list of the 21 imaging systems and what each one does is in [docs/SPEC.md](docs/SPEC.md) and
[docs/PROGRESS.md](docs/PROGRESS.md); the hospital extension is [docs/SPEC-hospital.md](docs/SPEC-hospital.md) (both
specs are in Chinese).

## Run it

### Prerequisites

- Git, and Docker Desktop (or Docker Engine) with Compose v2: PostgreSQL 16 runs in a container. Keep about 5 GB free
  on the disk that holds Docker's data (see [Troubleshooting](#troubleshooting)).
- Python 3.12 with [uv](https://docs.astral.sh/uv/) (`pip install uv` or the installer on uv's site).
- Node 20 or newer with npm.
- Chrome or Edge for the voice pages (browser speech recognition and synthesis); typed input works everywhere.
- No API key is needed: without one every AI step runs on built-in mock outputs through the same code path.
- Windows: clone into a short path such as `C:\src\ioc`. Under a deep folder the virtual environment's DLLs pass the
  260-character path limit and Python fails with "DLL load failed ... The filename or extension is too long".

### 1. Database

From the repository root:

```bash
docker compose up -d postgres
```

PostgreSQL listens on `127.0.0.1:5433` (user, password and database `ioc`), which is the API's default
`DATABASE_URL`. To use another database name on it, create it first:
`docker compose exec postgres createdb -U ioc <name>` (the API creates tables but not the database).

### 2. Backend

```bash
cd apps/api
cp .env.example .env
uv sync
uv run python -m app.core.db.crypto >> .env     # appends the two PHI keys (PowerShell: see below)
uv run python -m app.seed                       # migrates and generates the demo data (about 3 minutes)
uv run uvicorn app.main:app --port 8000         # ready in about a minute
```

In Windows PowerShell append the keys with
`uv run python -m app.core.db.crypto | Add-Content -Encoding ascii .env` (a plain `>>` writes UTF-16), or paste the
two printed lines into `.env`. The keys encrypt patient fields in the database: keep them with the database.

Check it: <http://127.0.0.1:8000/api/health> answers `{"status":"ok",...}`. The API also migrates and seeds an empty
database on start, so `app.seed` is only needed to see the progress or to regenerate the data later. If port 8000
is taken (on Windows it often is), use another one, e.g. `--port 9500`, and start the web below with
`VITE_API_PROXY_TARGET=http://127.0.0.1:9500`.

### 3. Web

In a second terminal:

```bash
cd apps/web
npm install
npm run dev
```

Open <http://localhost:5173> and pick a person. The first page after the API starts can take 10–30 seconds while its
caches fill; later pages are quick. "Reset demo" in the header regenerates all data (1–3 minutes); data and logins
otherwise survive restarts.

### 4. Optional: durable workflows (Temporal)

Everything works without Temporal: the **Workflows** page then says "offline" and the storyline runs step by step. To
see the patient journey as one durable workflow:

```bash
docker compose --profile workflows up -d temporal      # dev server: gRPC 127.0.0.1:7233, web UI http://127.0.0.1:8233
```

Add to `apps/api/.env`, restart the API, and run the worker in a third terminal:

```bash
# apps/api/.env
TEMPORAL_ADDRESS=127.0.0.1:7233
WORKFLOW_FOLLOWUP_DELAY_S=20        # demo: the 48-hour follow-up timer becomes 20 seconds (wall clock)
```

```bash
cd apps/api
uv run python -m app.workflows.worker
```

(`TEMPORAL_WORKER_IN_API=1` runs the worker inside the API instead, with fewer threads.)

### 5. Optional: a real model, HAPI FHIR, everything in Docker

- **Claude**: `ANTHROPIC_API_KEY=...` in `apps/api/.env`, restart the API. **Gemini** on Google Cloud Agent Platform
  (Vertex AI): `LLM_PROVIDER=gemini` and `GOOGLE_AGENT_PLATFORM_API_KEY=...` (not a Google AI Studio key). The header
  shows which model is live. Model ids per tier: `CLAUDE_MODEL_*` / `GEMINI_MODEL_*`.
- **HAPI FHIR** as the EHR instead of the PostgreSQL store: `docker compose --profile ehr up -d hapi`, then
  `cd apps/api && uv run python scripts/load_fhir.py` (about 10 minutes for 1,000 patients) and `FHIR_BACKEND=hapi`.
  The day simulator keeps writing the local store, so the boards follow it only on the default backend.
- **All in Docker**: `docker compose up -d --build` builds and runs PostgreSQL, the API and the web (nginx) on
  <http://localhost> (`WEB_PORT` to change it), reading `apps/api/.env`. With Temporal and its worker:
  `TEMPORAL_ADDRESS=temporal:7233 docker compose --profile workflows up -d --build` (the variable must be set in the
  shell or a root `.env`: inside the containers the API reaches Temporal as `temporal`). A server deployment is in
  [docs/DEPLOY.md](docs/DEPLOY.md) (in Chinese).

## Demo logins

The login page lists one person per role (no password; set `DEMO_PASSCODE` to require a passcode on a public server).
Switching person is one click in the header.

| Person | Role | Start here |
| --- | --- | --- |
| Jordan Lee | Operations manager (bed manager, OR coordinator) | Control Tower, Call centre, AI Ops, Scheduling |
| Dr. Yara Lindqvist | Physician, Medicine A | Patients, Documentation, Review queue |
| Jonah Eriksen | Nurse, Medicine A | Follow-up calls, Documentation (instructions, handoff), Control Tower |
| Rosa Pemberton | Pharmacist | Medication safety |
| Maya Nakamura | Registration clerk | Call centre requests, Review queue (coding) |
| Casey Brooks | Administrator | AI agents, AI Ops, Audit log, Break-glass review |
| Dr. Daniel Okafor | Medical director | Audit log, AI Ops, PHIPA access monitoring |
| Dr. Priya Raman | Radiologist | Reading room, Requisitions, Peer review |
| Sam Rivera | Technologist | Scheduling (mark exams done), Contrast checks, MRI safety |
| Alex Morgan | Front desk | Front desk (phone agent), Scheduling |
| Dr. Helen Park | Referring physician | Referrer portal (own patients only) |

The storyline switches to the right person at each step; other hospital staff (the surgeon, the ward nurse) sign in
from there.

## The two storylines

- **Hospital** (`/storyline`, about 10 minutes): press **Reset to the start** (1–3 minutes: the demo data is rebuilt
  with the storyline's patient and a full Orthopedics ward), then follow the 12 steps; each step names who plays
  it, where to click, the sentence to say and what the screen should show. Script with timings:
  [demo/hospital_journey.md](demo/hospital_journey.md). Works with and without Temporal.
- **Imaging centre** (Home page): four step-by-step storylines with one-click role switches: a chest X-ray report
  and a cancelled CT backfilled from the waitlist with messages in Chinese; the requisition intake pipeline; radiology
  operations; business and compliance. The hospital roles' Home pages list their own steps.

Each hospital module also has a 3–5 minute script: [demo/README.md](demo/README.md).

## Evals

```bash
cd apps/api
uv run python -m app.aiops.evalrun                 # every agent on the mock model (a few minutes); make eval with GNU make
uv run python -m app.aiops.evalrun order_review    # one agent
uv run python -m app.aiops.evalrun --live          # the configured Claude or Gemini model
uv run python scripts/eval_summary.py              # refresh evals/README.md from the run files
```

Each run is a file in `evals/runs/`; the release gate gives each agent its status from its latest run. The summary of
every agent's eval set, metric, threshold and latest result is [evals/README.md](evals/README.md): all committed
numbers are mock-model baselines, real-model runs are still to be done, and the human rating sheets (Control Tower
narratives, documentation) are still to be scored. In the app: **AI Ops → Eval Center**.

## Tests

```bash
cd apps/api && uv run pytest -q                   # backend: about 770 tests, 15-25 minutes; needs the database container
cd apps/web && npx tsc -b                          # typecheck
cd apps/web && npx playwright install chromium     # once
cd apps/web && npx playwright test                 # end to end: starts the API (port 8000) and Vite if not running
```

- The backend suite creates its own database (`TEST_DATABASE_URL`, default `.../ioc_test`) and rolls every test back.
- For Playwright set `LLM_PROVIDER=mock` when `.env` has a real key (the specs expect mock outputs), and
  `PW_API_PORT` / `PW_WEB_PORT` to move the API and Vite ports. On a slow machine start the API yourself first:
  Playwright waits only 60 s for it.
- `e2e/hospital-storyline.spec.ts` walks the whole storyline (15–30 minutes); set `STORY_TEMPORAL=1` when the API
  runs with Temporal. The online workflow specs skip themselves without Temporal.
- Some backend tests depend on the weekday the data is generated for (the hospital day follows it); the known cases
  are listed in [docs/PROGRESS.md](docs/PROGRESS.md).

## Configuration

`apps/api/.env` (from [apps/api/.env.example](apps/api/.env.example), git-ignored; real environment variables win).
The variables that matter:

| Variable | Default | What it does |
| --- | --- | --- |
| `PHI_ENCRYPTION_KEY`, `PHI_BLIND_INDEX_KEY` | none (required) | AES-GCM field encryption and HMAC blind index; generate with `uv run python -m app.core.db.crypto` |
| `DATABASE_URL` | `postgresql+psycopg://ioc:ioc@127.0.0.1:5433/ioc` | PostgreSQL 16 |
| `LLM_PROVIDER` | anthropic with a key, else mock | `anthropic`, `gemini` or `mock` |
| `ANTHROPIC_API_KEY`, `CLAUDE_MODEL_REASONING` / `_FAST` / `_VOICE` | none; Sonnet / Haiku | Claude and the model id per tier |
| `GOOGLE_AGENT_PLATFORM_API_KEY`, `GEMINI_MODEL_*` | none; `gemini-3.5-flash` | Gemini on Vertex AI |
| `APP_MODE` | `demo` | `prod` refuses agents whose eval has not passed (in `demo` their output is marked **Not evaluated**) |
| `TEMPORAL_ADDRESS`, `TEMPORAL_NAMESPACE`, `TEMPORAL_TASK_QUEUE`, `TEMPORAL_WORKER_IN_API` | off; `hospital-demo`; `hospital-workflows`; 0 | Durable workflows (optional) |
| `WORKFLOW_SIGNOFF_TIMEOUT_S`, `WORKFLOW_ESCALATION_TIMEOUT_S`, `WORKFLOW_FOLLOWUP_DELAY_S`, `WORKFLOW_VERIFY_AFTER_S` | 24 h, 24 h, 48 h, 1 h | Workflow timers on the wall clock; shorten for a demo |
| `FHIR_BACKEND`, `FHIR_BASE_URL` | `local`; `http://localhost:8080/fhir` | The EHR's store: PostgreSQL or HAPI FHIR |
| `FOLLOWUP_CALL_DELAY_H`, `FOLLOWUP_CALL_WINDOW_H`, `FOLLOWUP_CALLBACK_MINUTES` | 48, 72, 30 | Follow-up calls on the hospital clock |
| `DEMO_PASSCODE` | none | Passcode on the login page (set it on a public server) |
| `DEMO_DAILY_RESET_HOUR` | none | Regenerate the demo data once a day at this hour |
| `BACKGROUND_WORKERS` | 1 | 0 turns off the background loops (message dispatch, intake, simulator ticks) |
| `IDLE_PAUSE_MINUTES` | 15 | After this many minutes without a signed-in person using the app, the background work that calls the model (the simulator clock, AI intake, narration, order-review commentary) pauses until the next request; rule-based work goes on. 0: never pause |
| `BACKGROUND_AI` | `on_demand` | `on_demand`: no model call for work nobody asked for. The background runs the rules; an item's AI (an order review's commentary, a reconciliation's judgements, a Control Tower narrative, a requisition's extraction, triage and protocol, a feedback comment's labels) runs when a person opens it or presses Ask AI, and at once for new work their action created (a submitted requisition, a patient's survey answer, a storyline step). The header's "AI activity" lists every AI action of this browser. `eager`: the background writes the AI output of every new item before anyone looks (a real hospital, the evals) |
| `LLM_DAILY_BUDGET_USD` | none (no cap) | Daily cap on model spend (server's day); past it every model call is refused as "AI temporarily unavailable" and the non-AI features keep working. AI Ops shows the spend |
| `SEED`, `LLM_TIMEOUT_S`, `CORS_ORIGINS` | 42, 30, `http://localhost:5173` | Generator seed, model timeout, browser origins |

Web: `VITE_API_PROXY_TARGET` (Vite's `/api` proxy, default `http://127.0.0.1:8000`). Tests: `TEST_DATABASE_URL`,
`PW_API_PORT`, `PW_WEB_PORT`, `PW_CHROMIUM_PATH`, `STORY_TEMPORAL`.

## Troubleshooting

| Symptom | What to do |
| --- | --- |
| The API stops at start with "PHI_ENCRYPTION_KEY is not set" | Generate the keys (step 2) into `apps/api/.env` |
| `connection refused` on 5433 | `docker compose up -d postgres`; `docker compose ps` should show it healthy |
| `database "..." does not exist` | You changed `DATABASE_URL`: `docker compose exec postgres createdb -U ioc <name>` |
| `InvalidTag` errors or patient pages fail | The PHI keys changed after the data was written: put the old keys back, or regenerate the data (`uv run python -m app.seed`) |
| Every API call fails in the browser (login cards do not load) | The API is not on the port Vite proxies to: start Vite with `VITE_API_PROXY_TARGET=http://127.0.0.1:<port>` |
| Header says "AI: mock" | No key for the selected provider (that is fine for the demo) |
| Workflows page says "unreachable" | Temporal is configured but not running, or the worker is not started (`uv run python -m app.workflows.worker`) |
| The storyline reset takes long | It rebuilds the hospital (1–3 minutes, more on a loaded machine); wait for the clock to show 07:00 |
| The first page after starting the API spins for a while | The API fills its caches on the first requests (10–30 s); reload if the browser gave up |
| "DLL load failed ... The filename or extension is too long" (Windows) | The clone's path is too deep: clone into a short folder (e.g. `C:\src\ioc`) and run `uv sync` again |

### Windows notes

- **Reserved ports**: Hyper-V often reserves port ranges (check with
  `netsh interface ipv4 show excludedportrange protocol=tcp`; on the development machine 8430–9329, and 8000 was
  taken too). A bind error (`Errno 13` / `10013`) means the port is reserved: pick one outside the ranges, e.g. 9500
  or 9449, and set `VITE_API_PROXY_TARGET` (dev) or `PW_API_PORT` (Playwright) to match.
- **Disk space on C:**: Docker Desktop keeps its data disk (`docker_data.vhdx`) on C:. When C: ran nearly full,
  PostgreSQL logged I/O errors and stopped answering. Check free space (`df -h /c` in Git Bash) before seeding several
  databases, and drop test databases you no longer need
  (`docker compose exec postgres dropdb -U ioc <name>`); each demo database is about 150 MB.
- **`uv run` while the API or pytest runs**: uv may try to re-sync the virtual environment and fail on locked files;
  use `uv run --no-sync ...` (or `UV_NO_SYNC=1`) for the second command.
- **PowerShell**: use `Add-Content -Encoding ascii` instead of `>>` for `.env`, and `$env:NAME="value"` instead of
  `NAME=value command`.
- **Stray Python files in the temp folder**: a file named like a standard module (e.g. `dis.py`) in the folder you run
  Python from shadows the standard library; run scripts from the repository.
- **Line endings**: `.gitattributes` keeps shell scripts LF; let Git handle the rest.

## Repository layout

```text
apps/
  api/       FastAPI backend
    app/core       config, auth, audit, database, store
    app/llm        LLM gateway, providers, prompts, de-identification
    app/agents     agent runtime, Tool Gateway, registries' loader, approvals
    app/aiops      eval runner, release and prompt gates, observability, alerts
    app/ehr        synthetic hospital EHR: generator, FHIR store, FhirGateway, events, simulator, access, signing
    app/workflows  Temporal workflows, worker, bridge
    app/modules    one folder per system (imaging systems; control_tower, med_safety, documentation, voice_services, storyline, roadmap)
    config/        agent, tool and model registries, alerts, medication rules, roadmap and safety tiers
    prompts/       versioned prompts (one folder per agent)
  web/       React + TypeScript (Vite): src/features/<system>, e2e/ Playwright specs
  kg-qa/     Sub-project: the full medical knowledge-graph Q&A (Neo4j), unchanged
demo/        Demo scripts per module and the hospital storyline
docs/        Specs, progress, data model, safety tiers, deployment, interview narrative
evals/       Eval sets, module reports, run files, the eval summary
```

## Sub-projects

- [apps/kg-qa](apps/kg-qa): medical knowledge-graph Q&A (LangGraph agent + Neo4j + PostgreSQL), imported from an
  earlier project with its history; see its README (in Chinese). The suite's Clinical knowledge page reads its
  committed graph data into memory, so it needs neither Neo4j nor an embedding model.

## Tech stack

React + TypeScript (Vite, Tailwind, TanStack Query) · Python 3.12, FastAPI, Pydantic · PostgreSQL 16 with SQLAlchemy 2
and Alembic · FHIR R4 (own store, optional HAPI FHIR) · Temporal (optional) · scikit-learn · Anthropic SDK (optional
Google Gen AI SDK for Gemini) · Playwright
