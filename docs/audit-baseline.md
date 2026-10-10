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
| Auth | `app/core/auth.py` | `current_user`, `require_roles(*roles)`, `deny(...)` (403 + audit), `ensure_site(...)`, `audit_phi(...)`; header `X-Access-Reason` | Hospital roles join the same `Role` enum. Done in WP4: `CLINICAL_STAFF` is now the imaging staff only (hospital roles do not get the imaging screens); `current_user` records the user in the request context (`app/core/context.py`) for the `ai_call` audit event; unit scope and break-glass live in `app/ehr/access.py` / `breakglass.py` and are enforced in FhirGateway |
| Domain models | `app/core/models.py` | `Role`, `StaffUser(id, name, role, site_ids, referrer_id, reading_modalities, demo_login)`, `Patient`, `Appointment`, `ImagingStudy`, `Requisition`, `LabResult`, `Allergy`, `MessageOutbox`, `AuditEvent`, `LlmCall`, `Exam` (catalog entry) | Done in WP4: `Role` gains physician, nurse, pharmacist, clerk; `StaffUser` gains `unit_ids` and `practitioner_id`; `AuditEvent` gains the 6.3 fields (migration 0005) |
| Audit | `app/core/audit.py` | `AuditLog.record(user_id, user_name, role, action, resource_type, resource_id, outcome, source_ip, reason, ts)`; SHA-256 hash chain, advisory lock, own short transaction; `verify()`; DB trigger refuses UPDATE/DELETE | Done in WP4: `record(..., event_type, patient_mrn_hash, encounter_id, module, prompt_version)`, `query(**filters)`, `event_type_for(action)`, `mrn_hash(mrn)`; the digest leaves out new fields that are null so the existing chain still verifies |
| LLM gateway | `app/llm/gateway.py` | `LlmGateway.structured(task, prompt, variables, schema_cls, tier, images, patients) -> LlmOutcome(status ok/needs_human/unavailable, data, call_id, model, prompt_version, mode)`; `tool_turn(...)`; call log `LlmCall`; `PRICES`; `get_gateway()` / `set_gateway()` | Every Claude call in the extension goes through it. Done in WP4: `structured(..., pseudonymizer=)` for input de-identified by `fhir_deid.py` / `freetext_deid.py` (only known identifiers are replaced again), an `ai_call` audit event per call. Done in WP4b: the task is an agent id; unregistered tasks raise `UnregisteredAgent`; model tier and max_tokens from `model_policy`; prompt version must match the registry; prod mode refuses agents that have not passed (outcome unavailable); `LlmOutcome` gains `evaluated`, `eval_status`, `agent_version`, `run_id`; calls join the run's trace or get a one-call trace (`structured(..., input_refs=)`) |
| Providers | `app/llm/providers.py` | `AnthropicProvider`, `GeminiProvider`, `MockProvider(latency_s)`; `@mock_fixture(task)` registers mock outputs; `strict_schema(model)` | New agents register mock fixtures so the demo runs without a key |
| De-identification | `app/llm/deid.py` | `Pseudonymizer(known_patients).redact(text)` / `.restore(value)`; regexes for phone, email, 10-digit health card, ISO and long dates | Done in WP4: the free-text layer is `app/llm/freetext_deid.py` (`FreeTextDeidentifier`, `deidentify`), sharing the `Pseudonymizer` token map (`alias`, `redact_known` added) |
| Prompts | `app/llm/prompts.py` + per-module `Prompt(name, version, system, template)` | Versioned in code; version string recorded per call | New agents use `prompts/<agent>/<version>.md` files with the 4-line header (6.6); existing prompts are referenced from the registry |
| Mock integrations | `app/integrations/mocks.py` | SMS, email, phone, OHIP mocks with latency/failure rate; `dispatch_due()` | 6.2 adapter contract (retry, timeout, circuit breaker, dead letters, correlation id) wraps these |
| Background loop | `app/main.py` | `_step(name, fn)` per step in its own transaction; `_intake_loop` every 2 s | Simulator ticks and the EventBus → Temporal bridge run as steps. Done in WP4c: `_workflow_loop` sends the bridge's outbox to Temporal every second (`workflow dispatch`); `_workflow_worker` runs the worker in the API process when `TEMPORAL_WORKER_IN_API=1` |
| Seed | `app/seed.py` `populate(store, seed)` | Deterministic, `random.Random(seed)`, `seed_time` blob | Hospital generator runs from `populate` (own random stream, so imaging data does not change) |
| Evals | `app/modules/evals/build.py`, `run.py` (`eval_*` functions, results to `evals/results/<task>.json`), `router.py` (AI evaluations page) | 6.6 adds `evals/<agent>/cases.jsonl`, `evals/runs/<timestamp>.json`, one runner for all agents Done in WP10b: the `eval_*` functions return per-case verdicts and are the imaging suites of the one runner (`app/aiops/evalrun.py`); the page is AI Ops' "Imaging reports" tab |
| Tests | `tests/conftest.py` | Session DB `ioc_test`, seeded once; each test in a rolled-back transaction; `client`, `login(user_id)` fixtures; mock provider with zero latency | New tests reuse the fixtures |

## 3. Imaging modules reused by the flagships

| Extension module | Reuses | Path and interface |
| --- | --- | --- |
| 6.1 Exam ↔ FHIR | Imaging appointment + study + report | `Appointment`, `ImagingStudy` (`app/core/models.py`), `Report` (`app/modules/reports/service.py`: `report_for_study(store, study_id)`). In this code base `Exam` is the exam catalog entry (code, name, modality, minutes); the "exam" the spec maps is the booked exam: `Appointment` (+ its `ImagingStudy` and `Report` once performed and read). |
| 7.1 Control Tower | Scheduling command center | `app/modules/scheduling/service.py`: `utilization`, `heatmap`, `rank_waitlist`; polling every 3 s in `features/scheduling`. Done in WP5: `app/modules/control_tower` (boards, flow models, rules, exceptions, narrator agent), `features/control-tower` (polls every second while the simulator runs) |
| 7.2 Order review | Contrast & renal checker, triage | `app/modules/contrast/service.py`: `ContrastConfig`, `evaluate(store, patient_id, requisition_id) -> ContrastCheck` (rules first, AI only for history); `app/modules/triage/service.py`. Done in WP6: `app/modules/med_safety` (rules over plain facts, `review_order`, `reconcile`, the workbench API), `features/med-safety` (the pharmacist workbench, light theme with the app's kit) |
| 7.3 Documentation | Report draft and sign-off | `app/modules/reports/service.py`: `generate_draft`, `edit_ratio`, `sign(store, report, signer_name, confirmed_urgent, ...)`; bilingual terminology in `app/modules/clinical_kg` (glossary, `terms`). Done in WP7: `app/modules/documentation` (structured drafts with sources, per-item review and edit ratio as in the report pipeline, signing through the signing service; the knowledge graph's English ↔ Chinese names and facts for the patient instructions' terms), `features/documentation` |
| 7.4 Voice services | Front-desk phone agent | `app/modules/frontdesk/tools.py` (`verify_identity`, `transfer_to_human`, `run_tool`, `CallSession`), `agent.py` (`ScriptedAgent`, `LlmAgent`, `agent_reply`), browser Web Speech in `features/frontdesk`. Done in WP8: `app/modules/voice_services` (its patterns reused, not its tools: the encrypted call record, regex intents with a model in front, identity before anything personal, `parse_dob` for English and Chinese dates, a person on request; the browser speech console extended to zh-CN / zh-TW), `features/voice` |
| 6.6 AI Ops | AI usage and evaluations pages | `app/modules/admin/router.py` (AI usage from `LlmCall`), `app/modules/evals/router.py`; `features/admin/AiUsagePage.tsx`, `features/evals/EvalsPage.tsx`. Done in WP10b: `app/aiops/` (runner, suites, release and prompt gates, observability, alerts, `/api/aiops/...`), `features/ai-ops/` (route `/ai-ops`); the two pages are its tabs `AiUsagePanel.tsx`, `ImagingEvalResults.tsx` (old routes open them) |

## 4. Frontend (apps/web)

- React 18, TypeScript, Vite 5, Tailwind v4 (`@theme` brand and `ai` colours in `src/index.css`), TanStack Query, react-router 6, lucide icons. No chart library (`src/components/charts.tsx` is hand-written SVG). Light theme only, except the Control Tower (WP5): `.ct[data-theme=dark|light]` tokens in `src/index.css` (`bg-ct-*`, `text-ct-*`), after the warehouse tokens; its kit (`features/control-tower/kit.tsx`: SeverityPill, StatusPill, KpiTile, HeadlineBar, Drawer, skeleton / empty / error panels).
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

Ported in WP4c (`app/workflows/`): signal handlers only record (`FlowBase._on_event` / `_on_control` into an inbox,
deduplicated by event id); sign-off waits race absolute deadlines and keep waiting after escalating
(`_await_signoff`); timer durations in the start input (`Timers`); time from `workflow.now()` only; idempotent start
with the business key as workflow id (`journey-<encounter>`; "already running" is success; a closed run may be
followed by a new one because the generator's ids repeat after a demo reset); persist first, then signal (the
bridge's outbox `workflow_commands`, insert-first, then the dispatcher); insert-first idempotency also for the
eval cases (by case id) and the waits; the confidence gate is the registry's `confidence_threshold` checked by the
runtime at `finish`, its review record kept de-identified (`app/agents/reviews.py`); replay tests from captured
histories (`tests/test_workflow_replay.py`) and a worker-restart test written new (`tests/test_workflows.py`, plus a
real process kill in `scripts/workflow_demo.py`).

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
| `config/modules.registry.json` (6.3) | WP4: `apps/api/config/modules.registry.json`, loader `app/core/registry.py`. WP4b: replaced by the agent registry (both removed) |
| `config/agents/<agent_id>.yaml`, `config/tools/<tool_id>.yaml` (6.4) | `apps/api/config/agents/`, `apps/api/config/tools/`, loader `app/agents/registry.py` (`agent`, `require_agent`, `tool`, `gate`, `temporary`, `temporary_tool`, `mode` for tests); copied into the API image |
| Agent registry fields as strings (`model_policy: claude-sonnet-4-6; 4000; 0.2`, `eval_status: passed; run_…`, `data_scope: encounter_bundle; scope=own_unit`) | YAML mappings: `model_policy` {tier, max_tokens, temperature_max} (model id from the environment), `eval_status` {status, run_id, note}, `data_scope` {resources, scope}; added `kind`, `entrypoint`, `prompt_version`, `cosign`, `confidence_threshold` |
| Agent Runtime, Tool Gateway | `app/agents/runtime.py` (`runtime.start`, `AgentRun.gather / model / tool / finish`, `execute_approved`, `lineage`), `app/agents/gateway.py` (`ToolGateway.call`), handlers `app/agents/handlers.py`, approvals `app/agents/approvals.py`, untrusted-text block `app/agents/untrusted.py`, prompt files `app/agents/prompts.py` |
| `traces/` table | `agent_traces` (`app/agents/trace.py`, migration 0006); `output_ref` is `output_refs` (list; `output_ref` = the first), `cost` is `cost_usd`, `human_action` {role, user_id, decision, at, task_id} |
| Provenance agent "Device(agent_id + version)" | `who`: Reference type Device with identifier `urn:demo-hospital:agent|<agent_id>@<version>` (no Device resources); signers join as `verifier` |
| "approve audit event" | audit `event_type` `approve` (actions approve / accept / reject), written by `app/agents/approvals.py`; tool calls are `tool_call` |
| `APP_MODE` demo / prod | `APP_MODE` (settings `app_mode`), tests use `registry.mode("prod")` |
| `prompts/<agent>/<version>.md` | `apps/api/prompts/<agent_id>/<n>.md` for version `<agent_id>@<n>` (runtime agents); imaging prompts stay in code, their versions recorded in the registry |
| `evals/injection/cases.jsonl` | repository root `evals/injection/` (cases, `report.json`, `report.md`, README), runner `apps/api/scripts/injection_eval.py` |
| RBAC table, unit-scoped visibility (6.3) | `app/ehr/access.py` (`POLICIES`, `can_see`, `reduce`, `write_refusal`, `in_scope`), enforced in `FhirGateway` |
| Break-glass, 24-hour review queue | `app/ehr/breakglass.py` (`request_access`, `active_grant`, `queue`, `review`), API `POST /api/hospital/break-glass`, `GET /api/admin/break-glass`; screens Patients (dialog, banner) and Break-glass review |
| `Patient` consent fields `consent.ai_processing` / `followup_call` / `sms` | FHIR Consent resources per category (as the generator writes them), read by `app/ehr/consent.py` (`consent_status`, `plan_followup_call`, `documentation_mode`, `send_sms`, `change_consent`) |
| Signing service | `app/ehr/signing.py`, `FhirGateway.sign_document(id)`, `POST /api/hospital/documents/{id}/sign` |
| Placeholder tokens `[NAME_1]`, `[DATE_3]` | `[PERSON_n]` / `[STAFF_n]` (the token kinds WP2 already used), `[DATE_n:D-2]` (with days from the reference date); MRN `[MRN_<keyed hash>]` when known |
| Audit fields timestamp, actor_id, actor_role | existing `ts`, `user_id`, `role`; new `event_type`, `patient_mrn_hash`, `encounter_id`, `module`, `prompt_version` |
| Release gate "CI runs the module eval" (de-identification) | `tests/test_freetext_deid.py::test_eval_meets_the_thresholds` in the backend suite and `scripts/deid_eval.py --check` (the repository has no CI pipeline yet) |
| 7.1 Control Tower, its three boards, exception stream, action drawer | `apps/api/app/modules/control_tower/` (`snapshot.py`, `rules.py`, `exceptions.py`, `agent.py`, `service.py`, `router.py`, `/api/control-tower/...`), `apps/web/src/features/control-tower/` (route `/control-tower`) |
| "charge nurse", "OR coordinator" | a `nurse` of the unit (decides that unit's exceptions); the OR coordinator is the `operations_manager` (no separate role) |
| `getBedBoard(unitId)` for the whole hospital | `FhirGateway.search(type, **params)` + `batch(label)` (one audit record for the board's ten searches), `resolve_refs(refs)` for the narrator's evidence |
| LightGBM (7.1 models) | scikit-learn `HistGradientBoostingClassifier` / `Regressor` (already a dependency; LightGBM is a stack change awaiting the owner); "LightGBM feature contributions" = path attributions on the trees (`flowmodels.Explainer`) |
| `scripts/build_flow_dataset.py`, `models/flow/` | `apps/api/scripts/build_flow_dataset.py`, `apps/api/scripts/train_flow_models.py`; `apps/api/models/flow/` (`<model>.joblib`, `report.json`, `report.md`, README; training CSVs in `data/`, not in git) |
| narrator output `{exception_id, severity, narrative, recommended_actions[{action, rationale, owner_role, expected_effect}], evidence_refs[]}` | the same plus `action_id` per action (the engine's menu entry; its wording and owner role are the engine's) |
| `explainException` (the recommend example of 6.4) | tool `explainException` (an `ai-review` Task for the bed manager and charge nurses); the approved action = tool `createFlowTask` (Task code `flow-action`) |
| 7.1 evals | `evals/control_tower/` (`rules/cases.jsonl` 20 scenarios, `narrator/cases.jsonl` 30 exceptions, `report.json` / `report.md`, rating sheet), runner `apps/api/scripts/control_tower_eval.py` |
| "fast-forward to 08:00 tomorrow" from the control bar | `POST /api/hospital/simulator/fast-forward/start` (background job, `app/ehr/simjobs.py`); the synchronous `/simulator/fast-forward` stays for scripts |
| 6.5 `InpatientJourneyWorkflow`, `CapacityExceptionWorkflow`, `LowConfidenceReviewWorkflow` | `app/workflows/journey.py`, `capacity.py`, `review.py` (shared machinery `base.py`, ids and steps `model.py`); workflow ids `journey-<encounter>`, `capacity-<exception id>`, `review-<run id>` |
| 6.5 activities `triageAssist` … `codingStub`, `detect` … `recordOutcome`, `createReviewTask` … `triggerRegression` | activity types `journey.<step>`, `capacity.<step>`, `review.<step>` in `app/workflows/activities.py` (plus `journey.context`, `journey.requestFollowupCall` / `bookFollowupCall`, `journey.news2Alert`, `workflows.recordProgress`, `workflows.openTask`) |
| `await signal('signed' / 'approved' / 'rejected' / 'reviewed')` | one signal `event` whose payload `kind` is `signed` (with `final`), `decided` (with `decision`), `cleared`, `discharged` or `news2`; plus `control` (retry / skip) |
| EventBus → `startWorkflow` / `signalWorkflow` | `app/workflows/bridge.py`: consumer `workflows.bridge`, outbox table `workflow_commands`, waits `workflow_waits`, dispatcher `dispatch_step`; Temporal client `app/workflows/client.py` |
| "high-priority Task" / "notify ops_manager" after a sign-off timeout | Task `workflow-escalation` (priority urgent, the signer's role) / Task `workflow-notify` (priority stat, the operations manager), via the tool createWorkflowTask (registry entry `workflow_engine`) |
| Workflow view; retry and skip for ops_manager and admin | web `/workflows`, `/workflows/:id` (`features/workflows/`), API `/api/workflows/...`; audit actions `workflow_retry`, `workflow_skip` (also `workflow_start`, `workflow_fault`) |
| Temporal dev server in docker-compose, namespace `hospital-demo` | compose profile `workflows`: service `temporal` (`temporalio/temporal:1.8.3`, `server start-dev`, SQLite, UI on 8233) and `worker` (`python -m app.workflows.worker`); settings `TEMPORAL_ADDRESS`, `TEMPORAL_NAMESPACE`, `TEMPORAL_TASK_QUEUE`, `TEMPORAL_WORKER_IN_API`, `WORKFLOW_*_S` |
| `recordOutcome` ("被驳回的建议也记 outcome") | `flow_exceptions.outcome` (decision, verification, resolved) |
| `appendToEvalSet` → `evals/<agent>/cases.jsonl` (source=human_review), `triggerRegression` | `app/agents/evalsets.py` (`append_case`, `regression`; `AGENT_EVALS_DIR` moves the folder); the review record `app/agents/reviews.py` (table `agent_reviews`); correction API `POST /api/workflows/reviews/{id}`; review queue web `/reviews` |
| Journey steps of WP6 | `app/modules/med_safety/journey.py` (`review_orders` + `order_review_signoff` as `order_review`; `medrec_admission` with `medication_reconciliation`), called by the activities `journey.orderReview` / `journey.medRecAdmission` |
| Journey steps of WP7 | `app/modules/documentation/journey.py` (`discharge_summary_step`, `patient_instructions_step`), called by the activities `journey.draftDischargeSummary` / `journey.draftPatientInstructions` |
| Journey step of WP8 | `app/modules/voice_services/journey.py` (`schedule_step`), called by the activity `journey.scheduleFollowupCall` behind `workflow.patched("journey-followup-call")`; the placeholder in `app/agents/library/journey_stubs.py` (`followup_consent`, activities `journey.requestFollowupCall` / `bookFollowupCall`) replays for journeys started before WP8 |
| 7.4 call centre (hospital-wide inbound calls, six intents) | runtime agent `call_center` (`app/modules/voice_services/callcenter.py`, `call_agent.py`, prompt `call_center@1`), web `/call-centre`, API `/api/voice/calls`; table `voice_calls`, ids `VC-NNNNN` |
| 7.4 identity check (name + DOB + last 4 digits of the card) | `identity.py` + tool findCaller (a scan of the encrypted Patients; the health card in `identifier` `HCN_SYSTEM`) |
| 7.4 "proposed Appointment + clerk one-click confirm" | proposeAppointment, then the privileged moveAppointment / cancelBookedAppointment: the approval Task is the request; `POST /api/agents/tasks/{id}/decision` books or cancels (`FhirGateway.book_appointment(replaces=…)`, `cancel_appointment`) |
| 7.4 FAQ knowledge base | `apps/api/knowledge/hospital_faq/*.md` (sections with keywords and answers in en / zh-Hans / zh-Hant), retrieval `kb.py` |
| 7.4 follow-up call, red-flag rule layer, classifier `{escalate, reason, confidence}` | `followup.py` (scheduling, script, call), `redflags.py` (nine rule codes), `followup_agent.py` (runtime agent `followup_calls`, prompt `followup_calls@1`, output adds `summary`, `symptoms`, `quotes`); table `followup_calls`, ids `FU-<encounter>`; settings `FOLLOWUP_CALL_DELAY_H`, `FOLLOWUP_CALL_WINDOW_H`, `FOLLOWUP_CALLBACK_MINUTES` |
| 7.4 "high-priority nurse Task + notification" | Task `followup-escalation` (stat for an emergency, urgent for a red flag or the classifier), event `followup.escalated`, the web's escalation badge (`GET /api/voice/followups/summary`) |
| 7.4 nurse follow-up queue (call back, close, refer) | `queue.py`, web `/follow-up`, `POST /api/voice/followups/{id}/actions` (`callback`, `close`, `refer`), Communications `nurse-callback` / `nurse-close` / `nurse-referral`, event `followup.handled` |
| 7.4 evals | `evals/call_center/`, `evals/followup/`, `evals/voice_bilingual/` (runner `apps/api/scripts/voice_eval.py`); injection cases inj-31..38 in `evals/injection/` |
| `config/med_rules/` (7.2) | `apps/api/config/med_rules/` (`renal_drugs.yaml` 15 drugs, `interactions.yaml` 25 pairs, `dose_bounds.yaml`, `medrec.yaml`, README; every entry with a `source`), loader `app/modules/med_safety/rules.py` (`load_tables`); copied into the API image with `config/` |
| `knowledge/guidelines/` (7.2 RAG) | `apps/api/knowledge/guidelines/` (synthetic summaries, sections `<file>#<section>`), keyword retrieval `app/modules/med_safety/knowledge.py`; copied into the API image |
| OrderReviewService | `app/modules/med_safety/service.py` (`review_order`, `review_encounter_orders`, `comment`, `decide_review`), rules `rules.py`, agent `review_agent.py` (runtime agent `order_review` 0.2.0, prompt `order_review@1`) |
| Review queue | tables `order_reviews` and `medrec_sessions` (migration 0009, down_revision 0008), `GET /api/med-safety/queue` |
| Claude commentary schema `{concerns[{type, severity, explanation, evidence_refs[]}], alternatives[{suggestion, rationale}], guideline_refs[]}` | the same plus `confidence` per concern (the workbench shows it); severities major / moderate / minor (never blocking); guideline refs `<file>#<section>` |
| MedRec, accept / modify / reject, signing | `app/modules/med_safety/reconcile.py` (rows), `service.reconcile` / `settle` / `handle_item` / `sign`, agent `medrec_agent.py` (runtime agent `medication_reconciliation` 0.2.0, prompt `medication_reconciliation@1`); the record is the DocumentReference `medrec-<encounter>-<kind>` signed through `FhirGateway.sign_document` |
| Discrepancy types (omission, dose or frequency change, duplicate therapy, new without indication) | row types `omission`, `dose_change` (dose and frequency together), `duplicate_therapy`, `new_without_indication`, plus the non-discrepancy rows `match` and `new` (indicated) |
| "出院用药表" (discharge medication table, new / changed / stopped / continued) | `app/modules/med_safety/discharge.py` `discharge_medication_table(fhir, encounter_id)`, `GET /api/med-safety/encounters/{id}/discharge-medications`; stored as the JSON attachment `medrec-data` of the signed discharge MedRec (the `discharge-med-list` document type is not used) |
| Pharmacist workbench | `apps/web/src/features/med-safety/` (`MedSafetyPage`, `QueuePanel`, `ReviewPanel`, `MedRecPanel`, `kit.tsx`), route `/med-safety`, nav "Medication safety" (pharmacist, physician) |
| 7.2 evals | `evals/order_review/` (60 cases), `evals/medrec/` (20 cases), runner `apps/api/scripts/med_safety_eval.py`, cases `apps/api/scripts/gen_med_safety_eval.py`, harness `app/modules/med_safety/evals.py`, eval patients `app/ehr/seed/medcases.py` |
| 7.3 discharge summary schema `{reason_for_admission, hospital_course, discharge_diagnoses[], procedures[], medications{new[], changed[], stopped[], continued[]}, pending_results[], follow_up[], red_flags[]}` | the same (`app/modules/documentation/models.py` `DischargeSummary`); items carry typed fields (`name`, `code`, `rxcui`, `dose`, `note`, `test`, `when`, `department`, `location`) next to `text` and `source_refs` |
| `DischargeSummaryValidator`; "one validator base class, subclasses declare the fields that must be traceable" | `app/modules/documentation/validator.py`: `TraceabilityValidator` (`RULES`: field path pattern -> `Rule`), `DischargeSummaryValidator`, `PatientInstructionsValidator`, `HandoffValidator` |
| "unmatched items block signing" | the draft's `sign-block` extension, enforced by the signing service (`app/ehr/signing.py`), and the module's sign endpoint re-validating |
| Long-context strategy (chunks by type, segment summaries, synthesis) | `facts.build` (four chunks, split above 40 facts) + `summary_agent.draft` (one prompt `discharge_summary@1` with a SEGMENT and a SYNTHESIS stage: the LLM gateway allows one registered prompt version per agent) |
| Names and dates -> tokens, restored after drafting | `app/modules/documentation/deid.py` (`[DATE_n:D+k]` relative to the admission, the runtime's token map; the LLM gateway restores) |
| Bilingual instructions "from one structured content" | the model picks codes (`InstructionsPlan`); `instructions.build` + `render.render(content, lang)` with the phrase tables `phrases.py`; Traditional Chinese by hand-written phrases and `hant.to_hant` (no conversion library) |
| Flesch-Kincaid check | `app/modules/documentation/readability.py` (`fk_grade`, standard library) |
| Explanations "from the bilingual knowledge graph" | `app/modules/documentation/terms.py` (graph names; graph facts when they pass the selection rules; reviewed explanations for common conditions, the class for other medicines) |
| Delivery channels print / SMS (consent sms) / portal | `service.deliver` (print: an `export` audit event; SMS: `consent.send_sms`; portal: a Communication), written as the `patient_messaging` module; pages `/documentation/print/:id`, `/documentation/portal/:id` (mock) |
| SBAR handoff, one per shift | DocumentReference `sbar-<encounter>-<YYYYMMDD><D\|N>` (`document.shift_key`) |
| Documentation review UI | web `/documentation` (`features/documentation/`: `SummaryReview`, `InstructionsReview`, `HandoffPanel`, `PatientPages`), API `/api/documentation/...` |
| 7.3 evals | `evals/documentation/` (30 cases, report, documents, rating sheet), runner `apps/api/scripts/documentation_eval.py`, cases `apps/api/scripts/gen_documentation_eval.py`, harness `app/modules/documentation/evals.py`, eval patients `app/ehr/seed/doccases.py` |
| `config/models.yaml` (6.6) | `apps/api/config/models.yaml`, loader `app/agents/models.py` (`resolve`, `problems`, `by_model_id`); an entry names the setting with its id (`model_id_env`), never the id; agents refer to it through `model_policy.tier` (or `model_policy.model`) |
| "agent 注册表的阈值" (6.6) | `eval_threshold` in `config/agents/<agent>.yaml` (default 0.8, `app/aiops/release.py`) |
| `make eval [AGENT]`, `evals/runs/<timestamp>.json` | `uv run python -m app.aiops.evalrun [AGENT ...]` (also `make eval AGENT=...` in the root `Makefile` for GNU make); `evals/runs/run-<YYYY-MM-DDTHHMMSS>.json`; `--prompt <agent>@<n>` / `--model <entry>` for a comparison run, `--live`, `--stamp` (writes the run id into the approved prompts' `eval_run`), `--check` |
| Eval suites, "adapters for the existing per-module sets" | `app/aiops/suites.py` (`BUILTIN`: imaging `run.py` functions, `deid_eval`, `evalsets.regression`, `scripts/control_tower_eval.py`'s `collect_and_narrate`, `med_safety.evals`, `documentation.evals`; discovery: `evals/<dir>/suite.yaml`, common-format `evals/<agent>/cases.jsonl` (prompt replay), the module report named by the registry); per-case bookkeeping `app/aiops/collect.py` (`mark(case_id)`, `CollectingGateway`) |
| "CI 校验该 run 的通过率" (prompt gate) | `app/aiops/promptgate.py` (`uv run python -m app.aiops.promptgate`) and `tests/test_prompt_gate.py` (no CI pipeline in the repository) |
| Release gate "eval_status 由最近一次 run 自动写入" | derived at read time from `evals/runs` (`app/aiops/release.py`, applied by `registry.agent()` / `agents()`); the YAML's `eval_status` is the value before the first run and is not rewritten |
| 6.6 observability "从 trace 汇总" | `app/aiops/observe.py` (`overview(store, days)`: totals, by agent / model / unit / day, failures); mock calls priced by `app/aiops/cost.py` (list-price estimate from `PRICES`) |
| `config/alerts.yaml`, "admin Task" | `apps/api/config/alerts.yaml`, `app/aiops/alerts.py` (`check`, `resolve`; API loop `_aiops_loop` every 60 s); Task code `ai-ops-alert` written by the module `ai_ops` with the action tool `createAlertTask` |
| AI Ops page | web `/ai-ops` (`features/ai-ops/`: Overview, Eval Center, Compare, Registries, Alerts, Model calls, Imaging reports); API `/api/aiops/...` (`app/aiops/router.py`) |
| "未通过评测" watermark | `AiBadge`'s flag: **Not evaluated** (pending) or **Eval failed** (failed) |
