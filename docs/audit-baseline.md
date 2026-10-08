# Audit baseline (WP0)

Read-only audit for the hospital-platform extension (`全院AI平台_扩展规格_Phase6-8.md`, "the extension spec"). It lists every existing component the extension reuses, with path and interface, plus the two sibling projects the spec names. Later work packages start by reading this file and `docs/data-model.md`.

Branch: `feat/hospital-platform`, cut from `feat/postgres` (phase 0 completion: PostgreSQL + PHI encryption).

## 1. Owner decisions for this extension (2026-10-08)

| Topic | Decision |
| --- | --- |
| FHIR store (6.2) | Dual backend behind `FhirGateway`. Default: FHIR R4 resources stored in the existing PostgreSQL (encrypted JSON plus plain search columns), inside the request's unit of work so tests roll back. HAPI FHIR selectable with `FHIR_BACKEND=hapi` (compose profile `ehr`); the smoke test runs against HAPI. |
| Patients (6.2) | Own deterministic, Synthea-style generator (SNOMED CT, LOINC, RxNorm codes), 1,000 patients, regenerated from the seed like the rest of the demo data, with scenarios planted for each module. `scripts/localize_synthea.py` imports real Synthea output (tested on a small committed sample). No Synthea download. |
| Workflows (6.5) | Temporal, per the spec: `temporalio` Python SDK, dev server in docker compose (profile `workflows`, namespace `hospital-demo`), time-skipping and replay tests. Without `TEMPORAL_ADDRESS` the workflow view shows "offline" and modules keep working. |
| Scope | All 14 work packages, in the order of 8.4, one commit per work package. |

## 2. Existing platform layer (apps/api)

| Component | Path | Interface | Reuse in the extension |
| --- | --- | --- | --- |
| Settings | `app/core/config.py` | frozen `Settings` dataclass from env (`settings`); `.env` loader | Add `FHIR_BACKEND`, `FHIR_BASE_URL`, `FHIR_AUTH_MODE`, `TEMPORAL_ADDRESS`, `TEMPORAL_NAMESPACE`, `APP_MODE` (demo/prod) |
| Store / unit of work | `app/core/store.py` | `Store` with `EntityTable` collections (`store.patients`, ...), `store.module(name, factory)`, `store.conn()`, `next_id(prefix)`, `try_lock(key)`; `unit_of_work()`; `persist(detached, into)`; `reset_store()`; `build_store()` (detached, runs `app.seed.populate`) | The local FHIR backend is a new collection on the Store (`store.fhir`) using the same transaction, the same detached-then-persist seeding and the same reset |
| Schema registry | `app/core/db/schema.py` | `EntitySpec(name, model_ref, kind, encrypted, blind, indexes)`; `STORE_ENTITIES`, `MODULE_ENTITIES`, `CONFIGS`, `BLOBS`, `CACHES`; tables generated from Pydantic models; `FIXED_DATA_TABLES` | New module tables (traces, idempotency, break-glass grants, events) register here; the FHIR table is a fixed table |
| Repository | `app/core/db/repo.py` | dict/list-like `EntityTable` (`get`, `values`, `find_by`, `claim_due(column, now, statuses)` with `FOR UPDATE SKIP LOCKED`), row cache keyed by `(row_key, xmin)` | Whole-table loads are fine for small tables; the FHIR store must query by column instead (≈100k rows) |
| Field encryption | `app/core/db/crypto.py` | AES-GCM `cipher()`, HMAC blind index | FHIR resource bodies are stored encrypted; `Patient` MRN and health card get blind indexes |
| Migrations | `app/core/db/migrations/` (Alembic, `0001_initial_schema.py`), `app/core/db/migrate.py` (`upgrade()`, `init_db()`) | New migrations `0002+` for every new table and column |
| Auth | `app/core/auth.py` | `current_user`, `require_roles(*roles)`, `deny(...)` (403 + audit), `ensure_site(...)`, `audit_phi(...)`; header `X-Access-Reason` | Hospital roles join the same `Role` enum; unit scope and break-glass checks build on `deny` |
| Domain models | `app/core/models.py` | `Role`, `StaffUser(id, name, role, site_ids, referrer_id, reading_modalities, demo_login)`, `Patient`, `Appointment`, `ImagingStudy`, `Requisition`, `LabResult`, `Allergy`, `MessageOutbox`, `AuditEvent`, `LlmCall`, `Exam` (catalog entry) | `StaffUser` gains `unit_ids` and `practitioner_id`; `AuditEvent` gains the 6.3 fields |
| Audit | `app/core/audit.py` | `AuditLog.record(user_id, user_name, role, action, resource_type, resource_id, outcome, source_ip, reason, ts)`; SHA-256 hash chain, advisory lock, own short transaction; `verify()`; DB trigger refuses UPDATE/DELETE | Extended with `patient_mrn_hash`, `encounter_id`, `module`, `prompt_version`; the digest leaves out new fields that are null so the existing chain still verifies |
| LLM gateway | `app/llm/gateway.py` | `LlmGateway.structured(task, prompt, variables, schema_cls, tier, images, patients) -> LlmOutcome(status ok/needs_human/unavailable, data, call_id, model, prompt_version, mode)`; `tool_turn(...)`; call log `LlmCall`; `PRICES`; `get_gateway()` / `set_gateway()` | Every Claude call in the extension goes through it. WP4b adds: refuse unregistered tasks (agent registry), extra de-identification hook for FHIR patients and free text |
| Providers | `app/llm/providers.py` | `AnthropicProvider`, `GeminiProvider`, `MockProvider(latency_s)`; `@mock_fixture(task)` registers mock outputs; `strict_schema(model)` | New agents register mock fixtures so the demo runs without a key |
| De-identification | `app/llm/deid.py` | `Pseudonymizer(known_patients).redact(text)` / `.restore(value)`; regexes for phone, email, 10-digit health card, ISO and long dates | WP4 adds the free-text layer (names from patient and staff lists, Chinese names, mixed date formats, extensions, MRN, organisations) and FHIR-patient support |
| Prompts | `app/llm/prompts.py` + per-module `Prompt(name, version, system, template)` | Versioned in code; version string recorded per call | New agents use `prompts/<agent>/<version>.md` files with the 4-line header (6.6); existing prompts are referenced from the registry |
| Mock integrations | `app/integrations/mocks.py` | SMS, email, phone, OHIP mocks with latency/failure rate; `dispatch_due()` | 6.2 adapter contract (retry, timeout, circuit breaker, dead letters, correlation id) wraps these |
| Background loop | `app/main.py` | `_step(name, fn)` per step in its own transaction; `_intake_loop` every 2 s | Simulator ticks and the EventBus → Temporal bridge run as steps |
| Seed | `app/seed.py` `populate(store, seed)` | Deterministic, `random.Random(seed)`, `seed_time` blob | Hospital generator runs from `populate` (own random stream, so imaging data does not change) |
| Evals | `app/modules/evals/build.py`, `run.py` (`eval_*` functions, results to `evals/results/<task>.json`), `router.py` (AI evaluations page) | 6.6 adds `evals/<agent>/cases.jsonl`, `evals/runs/<timestamp>.json`, one runner for all agents |
| Tests | `tests/conftest.py` | Session DB `ioc_test`, seeded once; each test in a rolled-back transaction; `client`, `login(user_id)` fixtures; mock provider with zero latency | New tests reuse the fixtures |

## 3. Imaging modules reused by the flagships

| Extension module | Reuses | Path and interface |
| --- | --- | --- |
| 6.1 Exam ↔ FHIR | Imaging appointment + study + report | `Appointment`, `ImagingStudy` (`app/core/models.py`), `Report` (`app/modules/reports/service.py`: `report_for_study(store, study_id)`). In this code base `Exam` is the exam catalog entry (code, name, modality, minutes); the "exam" the spec maps is the booked exam: `Appointment` (+ its `ImagingStudy` and `Report` once performed and read). |
| 7.1 Control Tower | Scheduling command center | `app/modules/scheduling/service.py`: `utilization`, `heatmap`, `rank_waitlist`; polling every 3 s in `features/scheduling` |
| 7.2 Order review | Contrast & renal checker, triage | `app/modules/contrast/service.py`: `ContrastConfig`, `evaluate(store, patient_id, requisition_id) -> ContrastCheck` (rules first, AI only for history); `app/modules/triage/service.py` |
| 7.3 Documentation | Report draft and sign-off | `app/modules/reports/service.py`: `generate_draft`, `edit_ratio`, `sign(store, report, signer_name, confirmed_urgent, ...)`; bilingual terminology in `app/modules/clinical_kg` (glossary, `terms`) |
| 7.4 Voice services | Front-desk phone agent | `app/modules/frontdesk/tools.py` (`verify_identity`, `transfer_to_human`, `run_tool`, `CallSession`), `agent.py` (`ScriptedAgent`, `LlmAgent`, `agent_reply`), browser Web Speech in `features/frontdesk` |
| 6.6 AI Ops | AI usage and evaluations pages | `app/modules/admin/router.py` (AI usage from `LlmCall`), `app/modules/evals/router.py`; `features/admin/AiUsagePage.tsx`, `features/evals/EvalsPage.tsx` |

## 4. Frontend (apps/web)

- React 18, TypeScript, Vite 5, Tailwind v4 (`@theme` brand and `ai` colours in `src/index.css`), TanStack Query, react-router 6, lucide icons. No chart library (`src/components/charts.tsx` is hand-written SVG). Light theme only.
- Shell: `src/components/Layout.tsx` (`NAV` items with roles and sections, `canSee`, header with role and AI-mode badge, reset button), routes and `Guard` in `src/main.tsx`.
- Kit: `src/components/ui.tsx` — `Button`, `Card`, `Badge`, `AiBadge`, `Loading`, `EmptyState`, `ErrorState`, `PageHeader`, `Stat`, `Tabs`.
- API: `src/lib/api.ts` (`api`, `post`, `patch`, `put`, `download`, `ApiError`), `src/lib/auth.tsx` (`useAuth`), types in `src/lib/types.ts`.
- E2E: `apps/web/e2e/*.spec.ts`, Playwright config starts the API (`PW_API_PORT`, 9001 on this Windows machine) and Vite.

## 5. Sibling projects named by the spec

### warehouse (`D:\code\AI\agent\warehouse`) — patterns, not code

The spec assumes a three-column control tower; warehouse has a sidebar app with Overview, an Exceptions table and an exception detail page. Charts are ECharts on shadcn/Radix; this repo has neither. Forecasting is scikit-learn `HistGradientBoostingRegressor`, not LightGBM, with no saved model or per-feature explanation.

Taken over as patterns:

- Design tokens (`web/src/styles/tokens.css`: dark default `--bg #0B1220`, `--surface #111A2E`, `--accent #3B82F6`, `--critical #F87171`, `--warning #FBBF24`, `--ok #34D399`, light set under `[data-theme='light']`) and the theme switch (`web/src/lib/theme.ts`: `<html data-theme>`, localStorage). Used for the Control Tower's dark/light theme.
- State views (`components/StateViews.tsx`: skeleton, empty, error, `QueryView`), `StatusPill` (colour + icon + text, never colour alone), `HeadlineBar` (severity bar beside a one-sentence headline), `DetailDrawer` (420 px right panel, Esc closes).
- Exception flow: rule detectors produce `DetectedEvent`s; the LLM only picks from an engine-built action menu and writes the narrative; `agent/guards.py` rejects numbers the engine did not produce and claims that an action was executed (one regeneration, then a template sentence). Decision API requires a reason for rejection.
- Forecast pipeline: features shifted to avoid leakage, time-based origins for validation, comparison against a naive baseline, JSON metrics report with a `--check` mode.
- `tools/sql_query.py` guards (SELECT/WITH only, no stacked statements, read-only, row cap) for the 8.1 NL-analytics roadmap card.

### freight-arbiter-demo (`D:\code\AI\agent\freight-arbiter-demo`) — patterns, not code

Java 21, Spring Boot, Temporal Java SDK 1.35, MySQL. Nothing is copyable into Python as-is; ported patterns:

- Workflow: signal handlers only mutate state and queue audit commands; the workflow thread drains them through an activity. Waits use an absolute deadline (`awaitUntil`), human holds race an escalation timer and keep waiting after escalating. Timer durations are passed in the start input so config changes stay deterministic. Time comes from the workflow clock only.
- Idempotent start: workflow id = business key, "allow duplicate failed only", already-started is treated as success. Persist first, then signal.
- Confidence gate: one `decide(...)` returning accepted or a list of reasons (below threshold, model asked for review, validation finding, ...); review items are rows; resolving one stores model output and correction (`intervention_memory`) for the golden set. Here that becomes `LowConfidenceReviewWorkflow` appending to `evals/<agent>/cases.jsonl`.
- Insert-first idempotency (`INSERT ... ON CONFLICT DO NOTHING RETURNING` in PostgreSQL); provenance record with prompt version, model, input and output hashes.
- Replay test from a captured history file; no worker-restart test exists there (written new here).

## 6. Naming map (extension spec → this code base)

| Extension spec | Here |
| --- | --- |
| `fhir/` type definitions | `apps/api/app/fhir/` (`types/<resource>.py`, `examples/<Resource>.json`) |
| `FhirGateway`, `EventBus`, `DaySimulator` | `apps/api/app/ehr/`: `gateway.py` (HAPI backend `hapi.py`), `events.py` (`InProcessEventBus`, `bus`; `RedisPubSubBus` interface), `simulator.py` (module functions `advance`, `fast_forward`, `run`, `pause`, `tick` rather than a class), scripted events `scenarios.py`, API `router.py` (`/api/hospital/...`) |
| HL7 → domain event mapping "in the adapter" | `apps/api/app/ehr/hl7.py` (`Hl7EventAdapter`, `ROUTES`); `ADT_A01` etc. are written `ADT^A01` as in MSH-9 |
| `subscribe(eventType, handler)` | `bus.subscribe(event_type, handler, consumer=...)`: a stable consumer name keeps the cursor and the deduplication records |
| `getPatient(mrn)`, `searchEncounters`, `getBedBoard(unitId)`, ... | `FhirGateway.get_patient(mrn)`, `search_encounters(...)`, `get_bed_board(unit_id)`, ... (snake case) |
| Exam → FHIR adapter | `apps/api/app/ehr/imaging.py` (`exam_to_fhir`, `exam_from_fhir`) |
| De-identification field list for the resources | `apps/api/app/llm/fhir_deid.py` (`FhirDeidentifier`, `STRUCTURED`, `FREE_TEXT`) |
| Non-FHIR adapter contract, real adapters (interfaces) | `apps/api/app/integrations/contract.py`, `interfaces.py`; mocks in `mocks.py` |
| Bed manager role | `operations_manager` |
| `demo/<name>.md` | repository root `demo/` |
| `scripts/*.py`, `scripts/smoke_fhir.sh` | `apps/api/scripts/` |
| `config/agents`, `config/tools`, `config/models.yaml`, `config/alerts.yaml`, `config/med_rules/` | `apps/api/config/...` (shipped in the API image) |
| `prompts/<module>/<version>.md` | `apps/api/prompts/<agent>/<version>.md` |
| `evals/<agent>/cases.jsonl`, `evals/runs/` | repository root `evals/` (next to the existing `datasets/` and `results/`) |
| Roles physician, nurse, pharmacist, clerk | new `Role` values `physician`, `nurse`, `pharmacist`, `clerk` |
| Roles ops_manager, admin | existing `operations_manager`, `admin` |
| Exam (imaging) | `Appointment` + `ImagingStudy` + `Report` (see 3) |
| `make eval` | `make eval` (GNU make) or `uv run python -m app.aiops.evalrun` (Windows has no GNU make on this machine) |
