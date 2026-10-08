# Progress

## Demo-scope decisions (agreed with the owner)

Time is short and the goal is a working demo, so the first batch deviates from the spec's stack in these ways (the database rows were closed on branch `feat/postgres`):

| Spec | Demo build | Why / how to upgrade |
| --- | --- | --- |
| PostgreSQL + SQLAlchemy + Alembic | Done: PostgreSQL 16, SQLAlchemy 2 Core, Alembic. Tables are generated from the Pydantic models; modules keep their dict-style access through a unit-of-work repository (`app/core/db/repo.py`) | ORM models and SQL-side filtering only where an endpoint needs it |
| Field-level PHI encryption | Done: AES-GCM per field plus HMAC blind indexes for birth date and health card | Single key id, no rotation tool (see limits below) |
| Temporal workflows | Database polling: message dispatch every 5 s; a worker every 2 s for requisitions, prior retrieval (10), critical-result escalation (12), nightly peer-review sampling (13), feedback and inspection reminders. Due rows are claimed with `FOR UPDATE SKIP LOCKED`, scheduled runs take advisory locks, so a restart resumes in-flight steps | Temporal deferred by the owner (rationale option b); see `docs/RATIONALE-db-temporal.md` |
| Orthanc + DICOM | Synthetic PNG "phantom" chest images; PNG/JPEG upload | Real DICOM support and de-identification of DICOM tags later |
| STT/TTS services, Twilio | Browser Web Speech API (Chrome) plus typed input | Pluggable STT/TTS later; latency target must be measured locally |
| shadcn/ui, OpenAPI type generation | Small hand-written component set, hand-written types | Fine for demo size |
| Claude API | Real Claude when `ANTHROPIC_API_KEY` is set, otherwise mock outputs through the same gateway | Same code path either way |
| Knowledge-graph Q&A on Neo4j + PostgreSQL + embeddings (`apps/kg-qa`) | In-memory graph from the same data, fixed query templates, answers through the gateway | `apps/kg-qa` unchanged as the full version; it could be called as an optional backend later |
| Anthropic API only | Claude by default; Google Gemini selectable with `LLM_PROVIDER=gemini` (owner-approved stack change, see below) | Same gateway, same schemas and logging for both |

## Phase 0 — Backend Infrastructure (System 1)

Status: done (demo scope)

- [x] FastAPI app, settings from environment, health endpoint, JSON logs
- [x] Shared entities with FHIR-style names; synthetic seed: 5 sites, 12 scanners, 2000 patients, 150 referrers, ~3 months of history with cancellations and no-shows, 2 weeks of bookings, storyline patients; one-click reset
- [x] Demo login per role; every endpoint checks role, site-scoped staff limited to their sites; optional `DEMO_PASSCODE`
- [x] Audit log of PHI reads and writes and every denial (user, role, time, record, action, IP, reason); append-only SHA-256 hash chain with verification
- [x] Claude call layer: de-identification with re-identification of tool inputs, versioned prompts, Pydantic schema with one retry then "needs human review", degradation on timeout/API failure, call log without PHI (task, model, prompt version, tokens, latency, cost, input hash)
- [x] Mock SMS, email, phone, OHIP and private insurance with configurable latency and failure rate
- [x] AI usage dashboard and audit log viewer
- [x] Field-level PHI encryption (PostgreSQL, see below)

Acceptance:

- [x] `docker compose up` plus one seed command starts the demo (the API also seeds an empty database on start); login and role switching work
- [x] Unauthorized access returns 403 and leaves an audit record
- [x] PHI ciphertext in the database (`tests/test_db.py` reads the columns with plain SQL)
- [x] Call layer unit tests: de-identification, retry after schema failure, timeout degradation

### Phase 0 completion: PostgreSQL + PHI encryption — done (branch `feat/postgres`)

Owner approved step 1 of `docs/RATIONALE-db-temporal.md` on 2026-10-07; Temporal deferred (option b). Decisions taken as recommended: stateful module data in real tables, configs and small state as JSON in `module_state`; no in-memory runtime backend (local dev needs `docker compose up -d postgres`); optional daily reset via `DEMO_DAILY_RESET_HOUR`, off by default.

Done so far:

- [x] Postgres 16 service in `docker-compose.yml` (host port 127.0.0.1:5433); deps: SQLAlchemy 2, Alembic, psycopg 3, cryptography
- [x] `app/core/db/schema.py`: tables generated from the Pydantic models, registry of module state (tables / configs / blobs / per-process caches)
- [x] `app/core/db/repo.py`: dict/list-like collections with identity map, snapshot diff (only changed columns written), upsert, `claim_due` (FOR UPDATE SKIP LOCKED), `find_by` (blind index for encrypted fields)
- [x] `app/core/db/crypto.py`: AES-GCM field encryption + HMAC blind index; keys `PHI_ENCRYPTION_KEY`, `PHI_BLIND_INDEX_KEY` (generated into the local `apps/api/.env`)
- [x] `app/core/store.py` rewritten as a unit of work; detached in-memory mode for the seed generator and evals; `persist()` / `reset_store()`; sessions, id sequences and change counter in the database
- [x] Audit log in `audit_events`, written in its own short transaction under an advisory lock; migration trigger refuses UPDATE/DELETE
- [x] Alembic initial migration `0001`; `uv run python -m app.seed` migrates and seeds; API start migrates and seeds an empty database
- [x] `UnitOfWorkMiddleware`: one transaction per request, commit before the response starts (status < 500)
- [x] Background loops: each step in its own transaction; critical escalation, prior retrieval and message dispatch claim due rows with SKIP LOCKED
- [x] Phone-agent identity check uses the birth-date blind index; portal looks up health cards the same way
- [x] Tests run on Postgres (`tests/conftest.py`: test database created per session, each test rolled back)

Finishing work:

- [x] Performance. Single-row reads are capped (16 per table and unit of work, then the whole table is read). Rows are decoded in batches (one `TypeAdapter` call per query, about 3× faster than one `model_validate` per row). The flush diff reads attributes instead of `model_dump()` (about 3× faster). A per-process row cache keyed by `(row_key, xmin)` means a full-table load reads only keys and xmin, then fetches, decrypts and validates just the rows that changed; the others are copied from the cache (scalar values shared, JSON values copied per field type). The cyclic GC thresholds are raised at startup because the cache makes the heap large. Warm request times, local Windows machine, 19k appointments (first number: batch decode and cheaper diff only; second: plus row cache and GC thresholds): scheduling dashboard 0.85 → 0.3 s, dose overview 1.5 → 0.6 s, referrals 1.7 → 0.5 s, contrast checks 1.2 → 0.4 s, PHIPA alerts 1.4 → 0.5 s. Backend suite: 6 min (8 failing) → 2.5 min (154 passing)
- [x] Failing tests fixed. The dashboard change counter was not advanced in tests (the shared test store only flushed; `Store.save()` now flushes and advances it, and `commit()` uses it). Four tests held model objects across requests; the shared test store drops loaded objects after each request so later reads come from the database, so those tests now read the object again. Billing reconciliation: the seeded data ages, so exams that came due for a claim after the seed was generated are now (correctly) flagged as missing; the seed records its clock (`seed_time`) and the test excludes those. The backfill timing test passed once the suite was no longer slow
- [x] New tests (`tests/test_db.py`): changes survive a new unit of work; PHI columns are ciphertext in SQL and the blind index matches; the audit table refuses UPDATE and DELETE; a due row claimed by one worker is skipped by another (`SKIP LOCKED`); `alembic check` finds no difference between migrations and models; the row cache never serves an old row version (write from outside the store; two writes in one transaction); objects copied from the row cache share nothing mutable. `test_reset_rebuilds_demo_data` also checks that a login survives a reset
- [x] Scheduled steps safe with more than one process: peer-review sampling and the daily reset take an advisory lock and read their "already ran today" marker after taking it
- [x] Startup fails at once with a clear message when the PHI keys are missing
- [x] Playwright against the Postgres-backed API: all 19 tests in the 6 specs pass (local Windows, API on port 9001, mock provider, background workers on). Two fixes on the way, neither caused by the database: `PhoneAgent.tsx` returned `scrollIntoView(...)` from a `useEffect`, and the newer local Chromium returns a Promise there, which React then called as a cleanup and crashed the phone agent after the first reply; the storyline spec navigated right after clicking a login button, before the token was stored, which the slower database login exposed (it now waits for the signed-in name)
- [x] `docker compose up -d --build` checked locally: images build, the API migrates and seeds, a login token and the data survive `docker compose restart api`, and `psql` shows `k1:` ciphertext in the patient columns
- [x] `docker-compose.prod.yml` with a Postgres service and volume (no published port); Dockerfile comment; `.env.example` (DATABASE_URL, PHI keys, DEMO_DAILY_RESET_HOUR, BACKGROUND_WORKERS); `docs/DEPLOY.md` (architecture, `.env`, backup and restore with `pg_dump`, keys kept apart from backups, migrations and rollback, troubleshooting)
- [x] Deviation table, Phase 0 acceptance boxes, README and rationale status updated

Limits:

- Encrypted: patient identifiers (name, birth date, phone, email, address, health card), requisition text, outgoing message recipient and body, call transcripts, state and summaries, pre-registration contact details. Other free text (report text, feedback comments, notes on cases and reviews) is plaintext; the synthetic data puts no identifiers there, but a real deployment would need to review each field.
- One PHI key id (`k1`); changing keys means regenerating the demo data (DEPLOY.md). The blind index reveals which rows share a birth date or health card.
- The row cache holds a decoded copy of every table read in that process (roughly the memory the in-memory store used). Per-process caches (row cache, no-show model, search indexes) are not shared between workers; the API runs one worker.
- Most dashboards still read whole tables and filter in Python (the cache makes that cheap); SQL-side filters would be the next step if the data grew well beyond the demo size.
- The data persists, so the seeded timeline ages: bookings run out after two weeks, completed exams without claims are flagged as missing after two days. "Reset demo" or `DEMO_DAILY_RESET_HOUR` regenerate it; a reset also clears the audit log.

## Phase 1 — Flagship systems

### System 2 · Scheduling Command Center — done

- [x] Cancellation produces a ranked backfill list in under 5 s (measured: a few ms)
- [x] Dashboard refreshes live (polling every 3 s)
- [x] No-show model reports hold-out AUC (≈0.77 on synthetic data) and shows the top 3 factors per appointment; high-risk appointments get an extra reminder
- [x] Urgency always first; within a tier wait time, exam value and key referrer with configurable weights and a visible score breakdown
- [x] Cross-site suggestions for patients whose acceptable sites are full

### System 3 · Report Generator — done (evaluation pending)

- [x] Upload or pick from worklist → structured AI draft → review → sign
- [x] Server enforces that unsigned reports cannot be sent or viewed by the referrer
- [x] AI labelling, model and prompt version, edit ratio recorded
- [x] Confirmed urgent findings create critical result records (for System 12)
- [ ] Evaluation page against a public dataset (needs dataset download; the cloud environment blocks the hosts)

### System 4 · Front Desk Automation — done (latency to verify locally)

- [x] Call flow: verify identity → reschedule or cancel → confirm; medical questions and requests for a person are transferred
- [x] Claude tool-use agent when a key is set; deterministic scripted agent otherwise or when the API fails
- [x] New bookings schedule 72 h / 24 h reminders, confirmation with pre-registration link and prep instructions in the patient's language
- [x] SMS reply C confirms, X cancels and releases the slot to backfill
- [x] Mobile pre-registration in English, French, Chinese and Punjabi with mock OHIP / private insurance check
- [ ] Measure speech-to-reply latency (≤ 1.5 s target) on a local machine with a real key

## Phase 2 — Requisition intake pipeline (Systems 5–10)

Status: done (demo scope), branch `feat/phase-2-requisitions`

Pipeline: requisition arrives → shared extraction (one Claude call) → triage (5) → protocol suggestion (6) → contrast check (7, contrast exams) → MRI screening (8, MRI) → after booking: prep instructions (9) and prior imaging retrieval (10) in parallel.

Demo-scope notes: requisitions are synthetic text generated with ground-truth labels (the same generator writes the eval sets). The mock provider is a rule-based baseline, so the whole pipeline runs without an API key. Seeded requisitions are pre-processed with that baseline; three arrive unprocessed and the intake worker runs them through the gateway at startup. Prior-imaging retrieval runs as an in-process worker with retries instead of Temporal.

### Shared step · Requisition extraction — done

- [x] `Requisition`, `LabResult`, `Allergy` entities and a synthetic requisition generator with labels (form and free-text letter styles)
- [x] Extraction schema with source quote and confidence for every field; one gateway call per requisition, de-identified
- [x] Side-by-side view: original text with highlighted sources, low-confidence fields in yellow, staff corrections recorded and audited
- [x] Eval set: 50 synthetic requisitions; field-level accuracy

### System 5 · Priority Triage — done

- [x] AI priority P1–P4 with rationale and red flags; target wait days per tier, editable by the medical director
- [x] Queue sorted by days left to target; overdue in red
- [x] Radiologist confirm or override; override needs a reason, is audited and feeds agreement stats
- [x] New requisition triaged and queued within seconds (acceptance: within 1 minute); eval script reports agreement, over- and under-triage

### System 6 · Protocol Assignment Assistant — done

- [x] Synthetic library of 20 protocols (indications, contrast, duration)
- [x] Keyword retrieval narrows candidates; Claude picks primary + 2 alternatives with rationale and contrast flag; invalid ids fall back to retrieval order and are marked for review
- [x] One-click approve or change; approved duration sets the booking slot length (also on waitlist backfill)
- [x] Adoption rate, top-3 rate and most common changes on the insights tab
- [x] Eval set: 40 indications; top-1 / top-3 hit rate

### System 7 · Contrast & Renal Checker — done

- [x] Synthetic eGFR results and allergy records
- [x] Thresholds on a settings page, only the medical director (or admin) can change them; demo placeholder values labelled as such
- [x] Status pass / needs eGFR / needs premedication / needs review with the basis written out; AI only supplies the extracted history
- [x] Every contrast exam in the next 14 days has a status; statuses are computed on read, so a threshold change recomputes all immediately
- [x] Unresolved checks queue a reminder to the referring office 48 hours before the exam

### System 8 · MRI Safety Screening Assistant — done

- [x] Questionnaire in English, French, Chinese and Punjabi, sent by link in the patient's language
- [x] Claude extracts devices from free-text answers; matched against a synthetic device list
- [x] Flags only ever ask for review; technologist or radiologist records cleared / not cleared with a note
- [x] A flagged appointment cannot be confirmed (SMS reply, phone agent) before review
- [x] Eval set: 30 implant descriptions in 4 languages; device category accuracy

### System 9 · Patient Prep Instructions — done

- [x] English templates per prep type, approved by clinical staff
- [x] Claude drafts translations ahead of time; a person approves; only approved versions are sent (otherwise approved English, with a staff note)
- [x] Sent at booking and 48 hours before, in the patient's preferred language

### System 10 · Prior Imaging Retrieval — done

- [x] Mock outside archives (3 facilities) with configurable latency and failure rate, switchable from the board
- [x] Tasks created after booking from requisition mentions and patient history
- [x] Worker with retry, exponential backoff and a 4-attempt limit; states requested / retrying / received / not found / failed, with an event timeline; received studies imported and linked to the exam

### Evaluations

`cd apps/api && uv run python -m app.modules.evals.build` (datasets) and `uv run python -m app.modules.evals.run` (results to `evals/results/`, shown on the AI evaluations page). Committed results are from the rule-based baselines in mock mode and are optimistic because the rules were written alongside the generator.

- [ ] Run the evals with a real `ANTHROPIC_API_KEY` and record Claude's numbers

## Phase 3 — Radiology operations (Systems 11–14)

Status: done (demo scope), branch `feat/phase-3-radiology-ops`

Shared groundwork: a technologist marks an exam done → the appointment becomes completed, an `ImagingStudy` is created, and completion hooks run (assign a reader, record CT dose). The seed adds studies and signed reports for the last 14 days of completed exams, CT dose records for 90 days, and four more synthetic radiologists with reading credentials (demo login stays one user per role). Long-running flows (critical-result escalation, nightly peer-review sampling) run in the in-process worker loop, like prior retrieval in phase 2, instead of Temporal. Phase 3 makes no Claude calls.

Demo-scope notes:

- Data model: fields added, nothing removed. `ImagingStudy` gained `site_id`, `scanner_id`, `priority`, `protocol_id`; `StaffUser` gained `reading_modalities` (credentials) and `demo_login`; `Report` gained `source` (AI draft or dictated) and `signed_by_id`, and its AI fields now have defaults so dictated reports fit the same entity. The phase 1 `CriticalResult` stub moved into the critical module as `CriticalCase`.
- Studies other than chest X-rays have no images in the demo; they are read from the backlog with a fixed normal-report template (not AI text) that the radiologist edits.
- "Mark done" also works on upcoming exams so the demo runs at any hour; such an exam is recorded as performed now.
- Critical-result timings are compressed to seconds (critical: re-notify 20 s, escalate 45 s) and labelled as demo values; the typical clinic policy is shown next to them.
- Peer-review history is seeded as a 20% audit sample so the QA report shows a pattern (Dr. Webb's MRI reads); the live default rate is 5%. The QA lead is the medical director; admins are refused.
- CT dose values are synthetic; reference levels are placeholders set by the medical director. One scanner (EVW-CT1) drifts upward over the last three weeks for the trend demo.

### Shared step · Exam completion and reading roster — done

- [x] Technologist marks an exam done; study created; completion hooks for later systems
- [x] `Report` covers dictated (non-AI) reports as well as AI drafts; signer id recorded
- [x] Synthetic radiologists with credentialed modalities; shift roster
- [x] 14 days of synthetic studies and signed reports; unread studies form the starting backlog

### System 11 · Reporting Backlog & Turnaround Tracker — done

- [x] Unreported studies grouped by site, exam type, priority and age
- [x] Turnaround = signed − completed; targets per priority (configurable); at-risk and overdue flags
- [x] Per-radiologist queue; reassignment suggestions to on-shift, credentialed readers
- [x] Board refreshes live (polling every 3 s) with new studies and sign-offs; reassignment is audited

### System 12 · Critical Results Tracker — done

- [x] Case opened from a confirmed finding (report sign-off or dictation): finding, level, ordering physician, deadline
- [x] Worker notifies the ordering physician (mock phone/fax), re-notifies, then escalates to the medical director per configurable policy
- [x] Acknowledgement records who, when and how (staff entry or referrer portal); a case cannot close without one
- [x] Full timeline for every case; the unacknowledged demo case escalates on its own

### System 13 · Peer Review / QA — done

- [x] Scheduled sampling at a configurable rate and hour; blind assignment, never to the original reader
- [x] Graded review (concur / minor / significant) with discrepancy type
- [x] QA report by radiologist and exam type, QA lead (medical director) only; CSV export (audited)

### System 14 · CT Dose Monitoring — done

- [x] Every completed CT has a dose record (CTDIvol, DLP) shaped like a DICOM RDSR (TID 10011)
- [x] Reference levels per protocol (medical director); exceedances listed and reviewable; changes recompute immediately
- [x] Weekly trends by scanner and protocol

Tests: `tests/test_phase3.py` (29 backend tests) and `e2e/radiology-ops.spec.ts` (technologist → backlog balancing → dictation → live escalation and close → blind peer review → QA export → CT dose).

## Phase 4 — Business & compliance (Systems 15–21)

Status: done (demo scope), branch `feat/phase-4-business-compliance` (built on `feat/phase-3-radiology-ops`)

Same shape as phase 3: module state in `store.module(...)`, a `seed(s, rng, now)` per module, completion hooks for exam-driven work (inventory deduction, satisfaction survey), the in-process worker for background steps (feedback classification, inspection reminders). Three new Claude tasks go through the gateway with mock fixtures, so the demo runs without a key: `referral_weekly_summary`, `feedback_classify` and `policy_qa`.

Demo-scope notes:

- Data model: fields and spec entities added, nothing removed. Spec entities live in their modules, like `Screening` and `CriticalCase` before them: `InventoryItem` (one per site and product, with lots), `Claim`, `Document` (DocumentReference, versioned for policies) and `FeedbackResponse` (the satisfaction-survey QuestionnaireResponse). `AuditLog.record` gained an optional `ts` so the seed can import a chronological 30-day access history; the hash chain covers it the same way.
- Referral volume counts ordered exams by exam date (the 90-day history gives complete weeks). Six referrers' recent referrals are moved to colleagues in the seed so the visit list has real cases; this runs before studies are seeded and uses its own random stream, so earlier phases' data does not change.
- The weekly summary never contains a number written by the model: Claude places facts by key (`{last_week_total}`), the server fills in values from the dashboard queries and rejects drafts with their own digits or unknown keys (one retry, then "needs human review"). Each fact names the dashboard tile that shows it.
- Portal: a referrer's patients are those with an appointment, requisition, waitlist entry or study naming them. A refused patient id gets the same 403 whether it exists or not. Portal requisitions are composed into the same text layout as faxed ones and run through the same pipeline; referrers see a priority only after a radiologist confirms it, never the AI suggestion. Six upcoming appointments are given to Dr. Park in the seed.
- Billing: fee codes and amounts are a synthetic table, not the OHIP schedule. Claims cover the last 30 days; a claim is expected within 2 days of the exam. The seed plants every kind of discrepancy (also an amount that differs from the fee table) and records the planted set so tests check that each is found and nothing else is flagged.
- Feedback: surveys go out immediately after completion (a clinic would wait a few hours). A 1–2 star rating alerts the site manager by rule, with or without AI; an AI "negative" reading on a higher rating alerts too. Site managers are fictional contacts. Westbrook has a planted bad month.
- PHIPA monitoring recomputes alerts from the audit log (cached by log length), so the log stays the single source of truth; investigation state is stored by alert id and every step is itself audited. Three site staff (Laura Gagnon, Dana Kim, Chris Patel) and a patient record for Sam Rivera were added for the planted cases. Central intake (requisitions) is exempt from the cross-site rule. Thresholds (after hours 22:00–06:00, 25 patients in 15 minutes, 3 refusals in 10 minutes) are demo values.
- Inspection: policies are synthetic text (labelled "not clinical or legal guidance"); uploads accept pasted text or .md/.txt files, split into sections by `#` headings. Retrieval is keyword scoring (no embeddings); when nothing scores, the reply says the documents do not cover it without calling Claude. Citations must name a retrieved section and quote it verbatim, checked by the server. Reminders go out at 60, 30 and 7 days and when overdue, once per stage. Quality records from systems 13 and 14 are generated summaries.

### System 15 · Inventory Manager — done

- [x] `InventoryItem` per site and product with lots (lot number, expiry, quantity), reorder point and reorder quantity
- [x] Completion hook deducts contrast and consumables (first-expiring lot first) when an exam is completed; movement log
- [x] Low-stock alert plus purchase-order draft; near-expiry and expired lot alerts; receive stock and count corrections (audited)

### System 16 · Referral Analytics Dashboard — done

- [x] Referral volume by referrer, specialty, exam type, site and week, with filters and trend charts
- [x] Referrers with a marked drop in volume form a visit list
- [x] Weekly summary drafted by Claude from query results only: the model places numbers by fact key, the server fills them in and rejects any number it did not supply; each number links to its dashboard tile

### System 17 · Referring Physician Portal — done

- [x] Referrer sees only their own patients (appointments, requisition status, signed reports); anything else is 403 and audited
- [x] Online requisition (structured form plus free text) enters the phase 2 pipeline; staff see triage and protocol suggestions

### System 18 · Billing & Claims QA — done

- [x] `Claim` entity and a synthetic fee code table (not the OHIP schedule)
- [x] Reconciliation rules: missing claim, duplicate claim, code does not match the exam performed, amount differs from the fee table, rejected claim, claim for an exam that was not performed; seeded with each kind
- [x] Work queue with resolution outcomes; CSV export (audited)

### System 19 · Patient Feedback — done

- [x] Survey sent after completion in the patient's language; mobile page in 4 languages (rating plus free text)
- [x] Claude classifies sentiment and themes; staff confirm or correct; low rating or negative sentiment notifies the site manager
- [x] Ratings and themes by site and week; 60-item eval set (sentiment and theme accuracy)

### System 20 · PHIPA Access Monitoring — done

- [x] Rules over the audit log: patient not seen at the user's sites, after-hours access, bulk access, same family name, own record, repeated denials
- [x] Alerts with risk score and evidence (audit sequence numbers); seeded anomalies of every kind
- [x] Investigation queue with a full trail (assign, notes, outcome); compliance report and CSV export

### System 21 · Inspection Readiness Hub — done

- [x] Document library: versioned policies, equipment records (maintenance, QC tests, repairs), staff credentials, QC records generated from systems 13 and 14
- [x] Expiry reminders (credentials and equipment tests) and an inspection checklist
- [x] Policy Q&A grounded in uploaded documents only, with clickable citations; says so when the answer is not in the documents; eval set

### Phase 4 wrap-up — done

- [x] Storyline 4 on the home page; Playwright spec for phase 4; README and PROGRESS updated
- [x] Evals: `feedback` (60 comments, 4 languages), `policy_qa` (20 questions, 4 not covered) and `referral_summary` (number traceability over 3 data sets); baseline results committed
- [ ] Run the phase 4 evals with a real `ANTHROPIC_API_KEY` (mock baselines only so far: feedback sentiment 95%, theme F1 0.93; policy Q&A 88% answered with the right citation, 75% correct "not found"; summary numbers 100% traceable by construction)

Tests: `tests/test_phase4.py` (28 backend tests) and `e2e/business-compliance.spec.ts` (one test per system plus the storyline card).

## Optional Gemini provider

Status: done, needs a real-key check. Branch `feat/gemini-provider` (built on `feat/phase-4-business-compliance`). The owner approved this stack change: Gemini as an optional model vendor next to Claude, chosen by an environment variable, Claude staying the default.

- [x] Settings: `LLM_PROVIDER` (`anthropic`, `gemini`, `mock`; `LLM_MODE` still works as the old name). Unset keeps the earlier behaviour: Claude with `ANTHROPIC_API_KEY`, mock without. A provider whose key is missing runs as mock. Gemini uses `GOOGLE_AGENT_PLATFORM_API_KEY` with Agent Platform / Vertex AI; `GEMINI_API_KEY` (a Google AI Studio key) is ignored with a startup warning, because it does not work on the Vertex AI endpoint. Tier overrides remain `GEMINI_MODEL_REASONING`, `GEMINI_MODEL_FAST` and `GEMINI_MODEL_VOICE`.
- [x] `GeminiProvider` with the same interface as `AnthropicProvider`. `complete_json` sends images as inline parts (the chest X-ray draft works the same way) and constrains output with `response_mime_type=application/json` plus `response_json_schema` (the same closed schema the Claude path uses). `tool_turn` takes and returns the Anthropic-style messages, tool definitions and content blocks, converting to Gemini contents, function declarations, function calls and function responses, so the phone agent is unchanged apart from a rename (`ClaudeAgent` → `LlmAgent`) and the agent mode (`claude` or `gemini`)
- [x] Errors: API errors (4xx including 429, 5xx), timeouts and network errors raise `ProviderUnavailable`; a blocked prompt, a safety/recitation/prohibited-content finish or no candidates raise `ProviderRefusal`. The SDK retries once (2 attempts), like the Anthropic client's `max_retries=1`
- [x] Gateway picks provider and model names by vendor; the call log's `mode` is `anthropic`, `gemini` or `mock` and `model` is the model id sent. Vertex AI prices are recorded for `gemini-2.5-flash` and `gemini-3.5-flash`.
- [x] UI: header badge "AI: Gemini via Vertex AI API"; AI usage, report review, phone agent and evaluations name the vendor. Eval results record the model id from the call log.
- [x] `LLM_PROVIDER=gemini uv run python -m app.modules.evals.run` uses the Agent Platform / Vertex AI SDK route and degrades unavailable calls safely.
- [x] Tests: `tests/test_gemini.py` (31 tests, fake client, no network): JSON output with image and schema, retry after a schema failure and after invalid JSON, timeout / connection / 429 / 503 degradation, refusals, history and tool-call conversion including thought signatures, the phone agent's tool loop and scripted fallback on Gemini, settings and provider selection, `/api/meta` mode, eval labels, Vertex client configuration, API error messages that keep Google's own text. `e2e/llm-provider.spec.ts` checks the badge and the eval labels (Gemini responses simulated in the browser)
- [ ] Real calls with the Agent Platform key: verify a minimal request, then a chest X-ray draft, requisition, phone call and the evals.

Choices:

- SDK: the official `google-genai` SDK rather than Gemini's OpenAI-compatible endpoint. It supports `response_json_schema`, inline image parts, function calling with thought signatures (Gemini 3 models expect the signature of each function call to be sent back on the next turn; the provider keeps it on the content block) and typed errors. The OpenAI-compatible endpoint would add a third message format and drop the signatures and finish/block reasons we use for refusals.
- Minimal local `gemini-2.5-flash` and `gemini-3.5-flash` requests were verified through `aiplatform.googleapis.com` with HTTP 200 and non-empty text on 2026-10-07. Default model is `gemini-3.5-flash` for all three tiers; override per tier with the environment variables.
- Vertex AI global standard price as checked 2026-10-07: `gemini-2.5-flash` is $0.30 input / $2.50 output, and `gemini-3.5-flash` is $1.50 input / $9.00 output, per million tokens. Output includes reasoning tokens.

Limits:

- Not tested against the live API. The cloud network blocks ai.google.dev, so the model name and price were read from Google's search-indexed docs pages and the SDK source, not the pages themselves; check them on the pricing page when setting up.
- Gemini supports a subset of JSON Schema for structured output. The schemas sent are the same closed Pydantic schemas used for Claude; if Gemini rejects one, the call fails as "unavailable" and the call log shows the API error.
- No thinking-level tuning: Gemini Flash thinks by default, which adds latency in the phone agent; the 1.5 s speech-to-reply target is unmeasured for Gemini, as for Claude.
- The server-side refusal fallback used with Claude Sonnet 5.5 has no Gemini counterpart; a Gemini refusal goes to a person.
- One provider per running backend; switching needs a restart.

## Clinical knowledge Q&A (knowledge graph, in-memory)

Status: done (demo scope), branch `feat/gemini-provider`. The owner approved bringing the `apps/kg-qa` knowledge-graph Q&A into the suite as an in-memory demo version (stack change: no Neo4j, PostgreSQL, Chroma or embedding model; `apps/kg-qa` stays unchanged as the full version). Staff-facing only: the phone agent rule (no medical questions, transfer to a person) stays.

Placement: a side panel on the requisition review page (next to triage and protocol), prefilled with the extracted clinical indication, plus a "Clinical knowledge" page under Intake pipeline. Technologists, radiologists, the medical director and admins; front desk and referrers get 403.

- [x] In-memory graph loaded read-only from `apps/kg-qa/data/knowledge_graph/` (the committed annotation graph: 3,668 diseases, 30,513 facts; `medical_kg.jsonl` is read too when placed there locally; `CLINICAL_KG_DIR` overrides the folder). Duplicate disease lines merged; stable fact ids (hash of disease, relation, value)
- [x] English → Chinese glossary (159 terms common in requisitions), every target checked against the graph by a test; kg-qa's bilingual terminology sample merged in. Alignment: exact name, glossary, then fuzzy match (marked "approx.")
- [x] Question reading (`clinical_kg_parse`, fast tier): the model maps terms to Chinese graph names and picks the intent; the rule-based reader (glossary plus dictionary scan) is the mock fixture and the fallback when AI is unavailable
- [x] Fixed query templates instead of model-written Cypher: facts about a disease (symptoms, tests, drugs, treatments, complications, causes, affected groups), and diseases ranked by symptoms matched (then by the share of the disease's listed symptoms that matched, then by how well described the disease is), with their listed tests
- [x] Answer (`clinical_kg_answer`, reasoning tier): statements that each cite graph fact ids; unknown ids are rejected (one retry, then the facts are listed). Nothing found → "no facts" without a model call. AI unavailable → the facts are listed. One broad symptom → a note to add findings
- [x] Requisition questions are audited (`knowledge_query` on the requisition) and that patient's identifiers are redacted by the gateway
- [x] UI: side panel and page, AI or template answer marked, clickable fact citations, grouped fact list, "reference only, not a diagnosis" notice, recent questions
- [x] Eval set `clinical_kg` (30 questions, English and Chinese: 19 disease questions with an expected cited fact, 3 symptom lists with plausible diseases, 2 requisition-style indications, 6 not covered). Mock baseline: 100% on every metric, optimistic because the rules and the set were written together
- [x] Tests: `tests/test_clinical_kg.py` (18), `e2e/clinical-knowledge.spec.ts` (3)
- [ ] Run the eval with a real model (Claude or Gemini) and record the numbers
- [x] Tooling for English terminology of graph nodes: `python -m app.modules.clinical_kg.terms export` lists untranslated names (priority 1: all diseases plus terms used by two or more diseases, 6,382 names; `--all`: 16,900), `terms check` validates `data/terminology_en.jsonl`; the glossary merges it (hand-written entries win, low-confidence entries are display-only, aliases under 3 letters or claimed by two nodes are not matched); English matching looks up word n-grams with a plural fallback. Translation prompt: `docs/prompts/translate-kg-terminology.md`
- [ ] Generate `terminology_en.jsonl` locally with that prompt and commit it (backend restart needed to load it)

Limits:

- The graph is built from annotated Chinese medical literature (CMeIE). Coverage is uneven (paediatrics and rare diseases are over-represented; e.g. no facts for melanoma or goitre, no interstitial lung disease or meniscal tear nodes), and it is not a clinical guideline. The UI says so on every answer.
- Symptom lookups only count listed symptoms; there is no probability, prevalence or age/sex weighting. With one generic symptom the list is broad (flagged in the answer).
- English questions depend on the glossary when AI is off; terms outside it are reported as "not in graph". With a model, terms are translated first.
- Fixed templates cannot answer combined questions (e.g. "diseases with fever that need a chest X-ray"); the full `apps/kg-qa` service can, and could be added later as an optional backend.
- Questions are not kept across a reset (in-memory log), and there is no multi-turn conversation.

## Hospital platform — phases 6–8 (`docs/SPEC-hospital.md`)

Branch `feat/hospital-platform` (from `feat/postgres`). Owner decisions of 2026-10-08 and the reuse audit are in `docs/audit-baseline.md`: FHIR store in PostgreSQL with HAPI as a switchable backend, own deterministic Synthea-style generator plus an optional Synthea import, Temporal per the spec, all 14 work packages in the spec's 8.4 order, one commit each. A work package is done when it has code, tests, eval report, demo data and a demo script (spec rule 4). Baseline before the extension: 154 backend tests passing.

Order: WP0 → WP1 → WP2 → WP3 → WP4 → WP4b → WP5 → WP4c → WP6 → WP7 → WP8 → WP10b → WP9 → WP10. Each package's task list is filled in below when it starts.

### WP0 · Read-only audit — done

- [x] `docs/audit-baseline.md`: reusable components with paths and interfaces, the two sibling projects (warehouse, freight-arbiter-demo: both reusable as patterns only), naming map
- [x] Extension spec moved to `docs/SPEC-hospital.md`; CLAUDE.md points to it

### WP1 · Fake hospital EHR (6.2 setup)

- [x] Hospital generator (`app/ehr/seed/`): Location tree (ED 30, Medicine A/B 32 each, Surgery 32, Ortho 24, ICU 12 beds; unit → room → bed, plus 4 operating rooms), 60 practitioners with roles, 1,000 patients with MRN (`urn:demo-hospital:mrn`) and synthetic health card (`urn:demo-hospital:hcn`, 10 digits + version code, marked synthetic), GTA addresses, 30% `zh-CN`/`zh-TW` (Chinese names in a second `HumanName`), problem lists, home medications (DHDR stand-in), allergies, consents (some missing on purpose), outpatient labs; deterministic from the seed with one random stream per section
- [x] 60-day hospital timeline (`simulate.py`): ED arrivals with census-dependent waits against the physician roster, admission by age/CTAS/complaint/vitals/recent admissions, beds as a real constraint (boarding, overflow units, housekeeping), ICU step-down transfers, ALC stays, elective OR blocks per surgeon and emergency add-ons with surgeon-specific durations, orders tapering off before discharge. These are the ground-truth signals the 7.1 models will learn
- [x] Written as FHIR as of 07:00 on the hospital day (`materialize.py`): ~65k resources, ~119/132 inpatient beds occupied, an ED census, today's OR list with pre-op checklist tasks; the next 36 h are stored as the day simulator's plan (`hospital_plan`, ~1,800 events) for WP3
- [x] FHIR store in PostgreSQL (`app/ehr/fhirstore.py`, table `fhir_resources`, migration 0002): encrypted JSON body, plain search columns (ids, references, codes, status, dates; no identifiers), blind indexes for MRN and health card; `store.fhir` inside the unit of work, so tests roll back; seeded and reset with the rest of the demo data (adds about 8 s)
- [x] docker compose profile `ehr`: HAPI FHIR R4 (`hapiproject/hapi:v7.4.0`, 127.0.0.1:8080, own Postgres 16, subscriptions off, external and placeholder references allowed)
- [x] `scripts/gen_locations.py`, `scripts/localize_synthea.py` (`app/ehr/synthea.py`; hand-written Synthea-format sample in `scripts/samples/synthea/`), `scripts/load_fhir.py` (organization and practitioners → locations → one PUT transaction bundle per patient, one retry, `load_errors.log`), `scripts/smoke_fhir.sh`
- [x] Tests: `tests/test_ehr_store.py` (11: tree, patients, MRN lookup, ciphertext in SQL, every clinical resource has an existing encounter, census matches bed status, flow-model history, plan integrity, CRUD, determinism, reset), `tests/test_synthea_localize.py` (4)
- [ ] Smoke test against HAPI with all 1,000 patients loaded; `load_errors.log` empty

Deviations and notes:

- Patients come from our own Synthea-style generator (owner decision), not Synthea; Synthea output can be localized and loaded with the scripts. Spec acceptance "1,000 localized patients loaded" therefore means the generated patients.
- The hospital day starts at 07:00 on the seed date (the nearest weekday at a weekend, so the OR has a list). The hospital clock is separate from the wall clock and only moves when the day simulator runs (WP3).
- The ED history is dense for a 1,000-patient population (about 52 visits a day over 60 days, ~3 visits per patient) so that ED crowding and the flow models have realistic volumes.
- Local codes (`urn:demo-hospital:*`) are used where this demo has no standard code: CTAS is LOINC 11283-9 with an integer value; NEWS2, bed requests, pre-op checks, task and flag codes are local.

### WP2 · Data model + FhirGateway (6.1, 6.2 gateway)

### WP3 · Event bus + day simulator (6.2)

### WP4 · Platform increments: RBAC, break-glass, consent, free-text de-identification, audit (6.3)

### WP4b · Agent runtime and tool gateway (6.4)

### WP5 · Control Tower (7.1)

### WP4c · Temporal workflows (6.5)

### WP6 · Orders and medication safety (7.2)

### WP7 · Documentation agent (7.3)

### WP8 · Patient voice services (7.4)

### WP10b · AI Ops (6.6)

### WP9 · Hospital storyline (8.2)

### WP10 · Roadmap cards and documentation (8.1, 8.3)

## Environment notes

- `ANTHROPIC_API_KEY` and `GOOGLE_AGENT_PLATFORM_API_KEY` are not configured in the cloud environment; everything runs in mock mode there.
- The cloud network blocks Google's docs hosts (ai.google.dev, docs.cloud.google.com); the Gemini API host itself is reachable.
- The cloud network policy blocks the chest X-ray dataset hosts (NIH ChestX-ray14, Open-i).
- Deployment: two Docker images (API; web = nginx + static build) pulled from Docker Hub plus the official `postgres:16-alpine` image, on the server behind the host's Caddy, replacing the earlier smart_medical service on the same domain. Data, logins and in-flight background steps survive restarts (Docker volume `imaging-ops_pgdata`); the PHI keys live only in the server's `.env` and must be backed up separately. The API runs one worker. See docs/DEPLOY.md.
- Local Windows machine: port 8000 is reserved, so the API runs on 9001 for Playwright (`PW_API_PORT=9001 VITE_API_PROXY_TARGET=http://127.0.0.1:9001`).
