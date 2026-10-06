# Progress

## Repository setup

- [x] Create the repository and import the earlier AIMed project as `apps/kg-qa/` with its history (`medical_kg.jsonl` and interview notes stripped from history)
- [x] Add `docs/SPEC.md`, `CLAUDE.md` and this file

## Phase 0 — Backend Infrastructure (System 1)

Status: not started

### Tasks

- [ ] Monorepo scaffolding: `apps/api` (FastAPI, Python 3.12), `apps/web` (Vite, React, TypeScript, Tailwind, shadcn/ui), `infra/docker-compose.yml` (Postgres, Temporal, Orthanc), Makefile, `.env.example`
- [ ] Settings, structured logging that never contains PHI, health check endpoint
- [ ] Core data model: all shared entities from the spec as SQLAlchemy models with FHIR-aligned naming, plus the initial Alembic migration
- [ ] Field-level PHI encryption for patient name, date of birth, health card number, phone and address; key from the environment
- [ ] Auth and roles (front desk, technologist, radiologist, operations manager, medical director, admin, referrer); every endpoint checks role and site; demo mode switches role in one click
- [ ] Audit log: every PHI read and write records user, role, time, record, action, source IP and reason; append-only with a hash chain; denied requests are audited too
- [ ] Claude call layer: de-identification, versioned prompts in `app/llm/prompts/`, Pydantic output schemas with one retry and then "needs human review", degradation on timeout, rate limit or API failure, `LlmCall` logging without PHI, model IDs from the environment
- [ ] Integration mocks: RIS (FHIR-style), PACS (Orthanc with sample DICOM), outside image archive, OHIP card validation, SMS, email and phone, each with configurable latency and failure rate
- [ ] Temporal worker with retry policies and one example durable workflow
- [ ] Synthetic seed: 5 sites, 12 scanners, 2000 patients, 150 referrers, 3 months of appointment history with cancellations and no-shows, plus the demo-storyline patient (preferred language Chinese); one-command reset
- [ ] Monitoring: health endpoint, structured logs, and an AI usage dashboard (calls, latency, failure rate and cost per feature)
- [ ] Frontend shell: login page with demo role switcher, app layout, shared design system with loading, empty and error states, API types generated from OpenAPI
- [ ] Tests: pytest for core and the LLM layer; Playwright end-to-end test for login and role switching
- [ ] Cloud-session startup hook that starts the Docker daemon

### Acceptance (from the spec)

- [ ] `docker compose up` plus one seed command, then the frontend can log in and switch roles
- [ ] Unauthorized access always returns 403 and leaves an audit record
- [ ] PHI fields are ciphertext when the database is queried directly
- [ ] The Claude call layer has unit tests for de-identification, retry after schema validation failure, and timeout degradation

## Phase 1 — Flagship systems (Systems 2–4)

Status: not started

## Phases 2–4

Status: not started

## Environment notes

- `ANTHROPIC_API_KEY` is not configured in the cloud environment yet. The LLM layer is built and tested against mocks until it is.
- The cloud network policy blocks the chest X-ray dataset hosts (NIH ChestX-ray14, Open-i). System 3 needs sample images from there or uploaded manually.
- The voice latency target (1.5 s or less) has to be verified on a local machine.
- Public deployment target is not decided yet.
