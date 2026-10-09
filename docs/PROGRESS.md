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

### WP1 · Fake hospital EHR (6.2 setup) — done

- [x] Hospital generator (`app/ehr/seed/`): Location tree (ED 30, Medicine A/B 32 each, Surgery 32, Ortho 24, ICU 12 beds; unit → room → bed, plus 4 operating rooms), 60 practitioners with roles, 1,000 patients with MRN (`urn:demo-hospital:mrn`) and synthetic health card (`urn:demo-hospital:hcn`, 10 digits + version code, marked synthetic), GTA addresses, 30% `zh-CN`/`zh-TW` (Chinese names in a second `HumanName`), problem lists, home medications (DHDR stand-in), allergies, consents (some missing on purpose), outpatient labs; deterministic from the seed with one random stream per section
- [x] 60-day hospital timeline (`simulate.py`): ED arrivals with census-dependent waits against the physician roster, admission by age/CTAS/complaint/vitals/recent admissions, beds as a real constraint (boarding, overflow units, housekeeping), ICU step-down transfers, ALC stays, elective OR blocks per surgeon and emergency add-ons with surgeon-specific durations, orders tapering off before discharge. These are the ground-truth signals the 7.1 models will learn
- [x] Written as FHIR as of 07:00 on the hospital day (`materialize.py`): ~65k resources, ~119/132 inpatient beds occupied, an ED census, today's OR list with pre-op checklist tasks; the next 36 h are stored as the day simulator's plan (`hospital_plan`, ~1,800 events) for WP3
- [x] FHIR store in PostgreSQL (`app/ehr/fhirstore.py`, table `fhir_resources`, migration 0002): encrypted JSON body, plain search columns (ids, references, codes, status, dates; no identifiers), blind indexes for MRN and health card; `store.fhir` inside the unit of work, so tests roll back; seeded and reset with the rest of the demo data (adds about 8 s)
- [x] docker compose profile `ehr`: HAPI FHIR R4 (`hapiproject/hapi:v7.4.0`, 127.0.0.1:8080, own Postgres 16, subscriptions off, external and placeholder references allowed)
- [x] `scripts/gen_locations.py`, `scripts/localize_synthea.py` (`app/ehr/synthea.py`; hand-written Synthea-format sample in `scripts/samples/synthea/`), `scripts/load_fhir.py` (organization and practitioners → locations → one PUT transaction bundle per patient, one retry, `load_errors.log`), `scripts/smoke_fhir.sh`
- [x] Tests: `tests/test_ehr_store.py` (11: tree, patients, MRN lookup, ciphertext in SQL, every clinical resource has an existing encounter, census matches bed status, flow-model history, plan integrity, CRUD, determinism, reset), `tests/test_synthea_localize.py` (4)
- [x] Smoke test against HAPI with all 1,000 patients loaded (2026-10-08): `load_fhir.py` loaded organization, 120 practitioners and roles, 275 locations and 1,000 patient bundles in 576 s with no failed bundle (`load_errors.log` empty); `smoke_fhir.sh` passes all checks (1,000 patients, 1,628 inpatient encounters, conditions / medication requests / observations for a patient, three-level Location tree)

Deviations and notes:

- Patients come from our own Synthea-style generator (owner decision), not Synthea; Synthea output can be localized and loaded with the scripts. Spec acceptance "1,000 localized patients loaded" therefore means the generated patients.
- The hospital day starts at 07:00 on the seed date (the nearest weekday at a weekend, so the OR has a list). The hospital clock is separate from the wall clock and only moves when the day simulator runs (WP3).
- The ED history is dense for a 1,000-patient population (about 52 visits a day over 60 days, ~3 visits per patient) so that ED crowding and the flow models have realistic volumes.
- Local codes (`urn:demo-hospital:*`) are used where this demo has no standard code: CTAS is LOINC 11283-9 with an integer value; NEWS2, bed requests, pre-op checks, task and flag codes are local.

### WP2 · Data model + FhirGateway (6.1, 6.2 gateway) — done

- [x] `docs/data-model.md`: the resource table, relationship rules, id conventions, local codes and extensions, the hospital clock
- [x] Type definitions `app/fhir/types/` (Pydantic, one module per resource, only the fields used; extra fields allowed so HAPI responses round-trip), including Provenance, Consent, EpisodeOfCare, Schedule, Slot, Organization; `validate(resource)`
- [x] One example JSON per resource in `app/fhir/examples/` (18 taken from the seed, 7 hand-written for types the generator does not produce yet); `tests/test_fhir_types.py`: every example validates and round-trips unchanged, seeded resources of every type validate
- [x] Exam ↔ FHIR adapter `app/ehr/imaging.py` (imaging `Appointment` + `ImagingStudy` + `Report` ↔ ServiceRequest + Encounter AMB + DiagnosticReport), both directions: standard elements where FHIR has one, typed `urn:demo-hospital:ext:imaging-*` extensions for the imaging-only workflow fields; `tests/test_fhir_imaging.py` round-trips 20 samples (every appointment status, no-show risk factors, booked from a requisition with a protocol, studies with and without reports, dictated reports, an AI draft and a signed AI report with edits and a deleted section, worklist studies, an outside prior) and also FHIR → exam → FHIR; checked once on all 19,468 seeded appointments and the 6 worklist studies (all lossless, all valid)
- [x] `FhirGateway` (`app/ehr/gateway.py`): typed read methods (`get_patient(mrn)`, `search_encounters`, `get_active_encounter`, `get_bed_board(unit)`, `get_orders`, `get_medications`, `get_home_meds`, `get_observations`, `get_documents`, `read`), write allow-list (Task any; DocumentReference preliminary only; Communication; Flag; Appointment proposed only; `append_encounter_location` for bed managers), refusal + audit outside it (`FhirAccessDenied`), audit of every call (actor, type, id, purpose module); backends `local` (PostgreSQL store) and `hapi` (`app/ehr/hapi.py`: `FHIR_BASE_URL`, `FHIR_AUTH_MODE` none / smart_backend stub; same search semantics by matching on the store's index columns)
- [x] Mock adapter contract for non-FHIR systems `app/integrations/contract.py` (timeout and retry with exponential backoff, non-retryable rejections, circuit breaker per adapter, dead-letter table `dead_letters` with encrypted payload, migration 0003, correlation id on every message in and out, logged without payloads); interfaces only for the real HL7 v2, PACS, telephony, SMS/email, fax and billing adapters (`interfaces.py`); existing mocks moved onto it: outbox dispatch (correlation id = message id), OHIP and insurance checks, outside-archive retrieval and critical-result calls (one attempt per call, their workers keep the durable retry), inbound SMS replies; `GET /api/admin/integrations` (circuit states, dead letters without contents)
- [x] De-identification field list extended to every resource above (`app/llm/fhir_deid.py`: names incl. Chinese names, birth date → age, addresses, phones, health card, MRN → keyed hash, staff names and person reference displays, narrative dropped, free text: note, conclusion, presentedForm, document content, descriptions, comments, payloads), `tests/test_fhir_deid.py` with one test per resource type (25) that fails for every type when redaction is switched off
- [x] Grep tests: no direct FHIR HTTP calls outside the gateway's HAPI backend (the HAPI loader `scripts/load_fhir.py` now goes through the same `HapiClient`); modules never touch `store.fhir` or a backend
- [x] Demo script `demo/fhir_gateway.md` + `scripts/gateway_demo.py` (local and HAPI backend give the same output on the loaded data); data-model.md (gateway, de-identification, exam mapping), audit-baseline naming map, `.env.example` updated

Tests: `tests/test_fhir_imaging.py` (4), `tests/test_fhir_gateway.py` (20, including `test_both_backends_answer_the_same` against the running HAPI server, skipped when it is down), `tests/test_fhir_deid.py` (29), `tests/test_adapter_contract.py` (16). Backend suite 2026-10-08: 265 passed (196 before WP2). Eval for this package: the 20-sample round trip and the per-resource de-identification tests (no model involved). No Playwright test: WP2 has no screen (the Control Tower in WP5 is the first).

Playwright regression run (mock provider, API on 9001): 18 of 19 pass. The one failure, `business-compliance.spec.ts` "patient feedback ... in Chinese", is not caused by WP2: the feedback seed plants an open Chinese survey only when a Chinese-speaking patient is among the 40 most recently completed exams at seeding time, and the data seeded on 2026-10-08 afternoon has none (open surveys: one Punjabi, one English). The test depended on the time of seeding. Fixed 2026-10-08: when none of the latest 40 patients speaks Chinese, the seed now takes the latest earlier completed exam of a Chinese-speaking patient without a survey and sends its survey at seeding time, so it tops the 15 newest surveys (no rng draws, so the rest of the seeded data is unchanged; when a recent Chinese-speaking patient exists the seed behaves as before). Regression test `test_seed_plants_an_open_chinese_survey_even_without_a_recent_chinese_patient` in `tests/test_phase4.py` (29 passed). Seeding simulated at every hour from 2026-10-08 00:00 for 72 hours: the newest Chinese survey is always open (16:00 failed before). `business-compliance.spec.ts` 8 of 8 pass on data seeded as of 16:00 and after a normal `python -m app.seed`.

Notes and deviations:

- The spec's `getPatient(mrn)` etc. are snake case (`get_patient`). Read methods return the `app.fhir.types` models; `get_bed_board` returns a `BedBoard` view built from Location and Encounter.
- Purpose module is written to the audit record's `reason` until WP4 extends the audit record with a `module` column (6.3).
- Signing (DocumentReference → final) and booking (Appointment → booked) are not possible through the gateway yet: the signing service comes with WP4 (sign event, hospital roles), the approved booking with WP4b (privileged tools). Until then the gateway refuses both.
- A bed move also sets the bed statuses (new bed occupied, old bed to housekeeping), as the EHR's own transfer would; the allow-list's "Encounter.location append" is the only module-facing write behind it.
- The imaging adapter's references point at the imaging records (`Patient/PT-…`, `Practitioner/R-…`); linking imaging patients to hospital MRNs is the MPI roadmap card (8.1). Two round-trip samples are seeded records with fields set that no seeded exam has (an exam booked from a requisition with a protocol; an outside prior), since the seed books no exams from requisitions and imports no priors.
- HAPI reuses the result of an identical search for 60 s by default, which made a just-admitted patient missing from the bed board (found by the parity test in a full run); the client sends `Cache-Control: no-cache` on every search.
- httpx moved from the dev group to the runtime dependencies (already installed through the Anthropic and Gemini SDKs; the HAPI backend uses it directly).
- Adapter policies: messaging 3 attempts (0.2 s, 0.8 s backoff, 5 s timeout), lookups 2 attempts (3 s timeout), outside archive 1 attempt (10 s); circuits open after 5 failures in a row for 30 s; changing a mock's settings (admin page, prior-retrieval board) closes its circuit. Messaging mocks still return at once (their configured latency was never applied); OHIP, insurers and archives wait their latency.

Limits:

- The LLM gateway does not take FHIR-redacted input yet: its own regex pass would also tokenise the ISO dates in a resource. The hook (pre-redacted content, agent registry) is WP4b; until then no Claude call uses FHIR resources.
- HAPI searches the gateway cannot express in FHIR (unit of the current bed, active at a time, document status) are filtered after fetching; fine at demo size (bed board on the local HAPI: 0.2–0.3 s warm, about 3 s for the first call of a process), a real EHR would need server-side parameters or a different query.
- Circuit breakers are per process (the API runs one worker); dead letters are parked for a person, there is no redrive yet (integration health is an 8.1 roadmap card).

### WP3 · Event bus + day simulator (6.2) — done

The simulator plays the EHR: it writes the FHIR store directly and sends HL7 v2 messages; an adapter maps them to domain events; modules subscribe to domain events and read content through `FhirGateway`.

- [x] `EventBus` (`app/ehr/events.py`): domain event model (`event_id`, `correlation_id`, `actor`, `occurred_at`, references and routing attributes only), 11 event types with domain names; transactional outbox `domain_events` (migration 0004) so FHIR writes and events commit together; `bus.subscribe(type | "*", handler, consumer=)`; in-process delivery in sequence order, a batch per transaction and, when a handler fails, the batch rolled back and redone one event per transaction; deduplication per consumer by `event_id` (`event_deliveries`), a cursor per consumer (`event_consumers`), 3 attempts then parked as a dead letter (shown on the integrations page) without reordering; `RedisPubSubBus` as interface only. The API's background loop ticks the simulator and drains the bus every second
- [x] HL7 v2 adapter (`app/ehr/hl7.py`): ADT A01/A02/A03/A08/A20, ORM O01, ORU R01, SIU S12/S14/S15 → domain events, mapping only in the adapter (`ROUTES`); minimal ER7 render/parse and ACK; under the adapter contract (correlation id per message, logged without content; unmappable messages get `AE`, unparseable ones `AR`, both parked); messages carry FHIR ids (PID-3 type PI), a real feed's MRN (type MR) is looked up through `FhirGateway`; inbound endpoint `POST /api/hospital/hl7` (admin)
- [x] `DaySimulator` (`app/ehr/simulator.py`): hospital clock (`advance`, `advance_by`, `run` at a rate with a background `tick` capped at 30 hospital minutes, `pause`, `fast_forward` to 08:00 tomorrow, stops at the plan horizon); every plan event written to FHIR the way the generator writes it (its builders are reused; ids it numbers from a counter are scoped to the plan event, so they are deterministic): ED arrival, triage (CTAS, vitals), seen (bay), decision (diagnosis, bed request), departure; admission (closes the ED visit and its bed request), transfer, ALC flag, discharge (conditions resolved, medications completed, flags closed); orders and results; OR bookings, cancellations, start and end (pre-op checks closed, post-op medications), day-surgery visits. Bed checks: a taken bed means another free bed in the unit or its overflow units; with none the patient keeps boarding and the stay's events move 30 minutes later. Housekeeping for any bed left dirty without a planned cleaning; ward vital-sign rounds every 6 h
- [x] Plan additions in the generator (`app/ehr/seed/__init__.py`): OR bookings, cancellations, day-surgery arrival and departure, ALC designations; at the same minute, cleaning and departures come before arrivals; the seed is recorded in the plan. FHIR data unchanged
- [x] Scripted scenarios (`app/ehr/scenarios.py`): ICU surge (default: enough critically ill patients to fill ICU plus one, at least 3; `count` to choose), ED surge (8 arrivals in 40 minutes, one hip fracture booked for emergency surgery in OR-04), OR overrun (+50 minutes, pushes the room's list), consent withdrawal (`consent.revoked`, a platform event). Every added entry carries the operator (EVN-5 → event actor `user:<id>`) and one correlation id
- [x] API (`app/ehr/router.py`): `GET /api/hospital/simulator` (clock, next events, scenarios), `POST .../advance`, `.../fast-forward`, `.../run`, `.../pause`, `.../inject`; `GET /api/hospital/events` (log, newest first, filter by type or encounter), `GET /api/hospital/events/consumers`; operations manager and admin; control actions audited as `simulator_event`, event-log reads as `read DomainEvent`; 409 while another step holds the simulator lock
- [x] Tests: `tests/test_event_bus.py` (26: delivery order and matching, deduplication (also within a batch), retry then park without reordering, a failing batch redone event by event, subscription checks, every message type mapped and round-tripped through ER7, mapping details, ER7 layout, five unmappable messages parked with `AE`, unparseable text `AR`, MRN lookup, inbound endpoint and roles, platform events), `tests/test_day_simulator.py` (12: one hour, a full day with ≥ 6 event types in timeline order and every written resource valid, discharge and housekeeping, planned transfer, a bed taken by a bed manager, each scenario, running clock, horizon, API roles and audit); the reset test in `tests/test_ehr_store.py` also checks that the event log is emptied. Backend suite 2026-10-08 (separate test database): 304 passed (266 before WP3)
- [x] Eval report (no model in this package): `uv run python scripts/simulator_demo.py --report` → `evals/simulator/day_run.json`: the planned day (07:00 → 08:00 the next day) and the same day with the ICU surge, each in a rolled-back transaction. Result 2026-10-08 (hospital day Thu 2026-10-08): passed. Planned day: 1,617 domain events of 8 types from 1,617 HL7 messages (ORU 801, ORM 412, A08 182, A01 90, A03 80, S14 30, A20 21, S15 1), 1,967 resources written, all valid, 0 order or consistency problems, 11 s. With the ICU surge (11 patients, ICU had 10 free beds): 1,916 events, 52 admissions postponed for want of a bed, 3 patients still boarding the next morning, 270 events under the scenario's correlation id, 0 problems, 13 s
- [x] Demo: `demo/event_bus_simulator.md` + `scripts/simulator_demo.py` (rolls back unless `--commit`); docs: `data-model.md` (events and simulator section; surgery Appointment ids corrected to `appt-NNNNNN`), `audit-baseline.md` naming map

No Playwright test: WP3 has no screen; the Control Tower's control bar (WP5) is the first UI on this API.

Notes and deviations:

- Spec names `ADT_A01` etc. are written as in MSH-9 (`ADT^A01`). Beyond the spec's six message types the simulated EHR also sends ADT^A20 (housekeeping done), ORM^O01 (orders) and SIU^S14/S15 (OR case started, finished, cancelled), so subscribers can follow bed status, orders and the OR without polling. Domain events: the spec's six names plus `encounter.updated` (A08), `bed.status_changed`, `order.placed`, `appointment.updated` and `appointment.cancelled`.
- An ED arrival is `patient.admitted` with `encounter_class=EMER` (A01 "arrival/admission"); an admission from the ED is A03 for the ED visit (`disposition=admitted`) plus A01 for the stay. Subscribers filter on `encounter_class`.
- `DaySimulator` is a set of module functions over the clock state, not a class; the clock state stays in `hospital_clock` (now also `rate`, `running`, `last_wall`, `applied`, `stopped`).
- Audit: one `simulator_event` record per control action (advance, fast-forward, run, pause, inject, inbound HL7), not one per simulated event; the event log is the per-event record (a simulated day is about 1,600 events, which would otherwise be 1,600 hash-chained audit rows).
- Vital-sign rounds are derived from the clock (00/06/12/18, as the generator writes them), not stored in the plan. ED bays have no tracked status (as in WP1); the simulator gives a patient a bay that no active ED encounter holds.
- Pre-op checks still open when a case starts are completed at the start (the team closes them before incision); the Control Tower's "pre-op incomplete within 4 hours" rule sees them open until then.
- Finding for WP5: the generated hospital has a nearly empty ICU at 07:00 (2 of 12 beds on the 2026-10-08 seed) while the wards run at 90–100%. The ICU surge therefore fills ICU by default; the ICU share in the generator may need recalibrating when the Control Tower's exception rules are built.
- Performance on the local Windows machine (PostgreSQL in Docker Desktop, about 2 ms per round trip): fast-forward from 07:00 to 08:00 the next day takes about 11 s for about 1,600 events (about 4,000 statements), 13 s with the ICU surge. The fast-forward runs inside the request. A subscriber catches up on such a day in a few transactions (batches of 200).

Limits:

- Delivery is in process, in the API's single worker; handlers run one after another in the background loop, so a slow handler delays the others. Redis Pub/Sub is an interface only.
- A parked delivery stays parked; there is no redrive tool yet (integration health is an 8.1 roadmap card).
- The simulated day ends at the plan horizon (07:00 + 36 h); the clock then stops, and replaying means resetting the demo data (about 30 s). The plan is not extended.
- The simulator writes the local FHIR store; with `FHIR_BACKEND=hapi` the simulated day does not reach the HAPI server.
- Patients waiting for a bed are admitted in plan order as beds free up; nothing re-prioritises bed requests (that is the Control Tower's job in WP5).

### WP4 · Platform increments: RBAC, break-glass, consent, free-text de-identification, audit (6.3) — done

Read-only audit first (auth, `Role`/`StaffUser`, the audit hash chain, `deid.py`, `fhir_deid.py`, the LLM gateway, `FhirGateway`, the event bus), then:

- [x] Roles and staff: `Role` gains `physician`, `nurse`, `pharmacist`, `clerk` (the spec's ops_manager is `operations_manager`); `StaffUser` gains `unit_ids`, `practitioner_id` (migration 0005, existing users get no unit scope); one StaffUser per generated Practitioner with the role of its PractitionerRole and its unit (`app/ehr/seed/platform.py`, user id `U-DOC-09` for `prac-doc-09`); demo logins Dr. Yara Lindqvist (physician, Medicine A), Jonah Eriksen (nurse, Medicine A), Rosa Pemberton (pharmacist), Maya Nakamura (clerk) on the 6300 seed; `CLINICAL_STAFF` is now the imaging staff only, so the hospital roles get no imaging screens
- [x] Access policy `app/ehr/access.py` enforced in `FhirGateway` (every read method and every write) and so in the hospital API: per-role resource types, patient scope (physician and nurse: patients with an encounter active or finished in the last 72 hours on one of their units, or attended by them; pharmacist, clerk, operations manager: hospital-wide; admin: no patients), reduced views (operations manager: a patient is an MRN; clerk and operations manager: encounters, appointments and requests without clinical elements), finer rules (pharmacist: laboratory results and medication documents only; clerk and operations manager: only tasks with their performer type), write rights; imaging roles have no hospital access; system actors are trusted and audited
- [x] Break-glass `app/ehr/breakglass.py` (table `break_glass_grants`, reason, MRN and note encrypted): reason of at least 10 characters (whitespace collapsed), a 4-hour grant per user and patient, `break_glass` audit event with the patient's MRN hash, reads under the grant marked `break-glass BG-…` in their audit reason, admin queue with the 24-hour review deadline (overdue flag) and what was read, review justified / not justified with a note as a second `break_glass` event (once; nobody reviews their own access). API `POST /api/hospital/break-glass`, `GET /api/hospital/break-glass/active`, `GET /api/admin/break-glass`, `POST /api/admin/break-glass/{id}/review`. Web: restricted card with the reason dialog on the patient chart, red banner on every page while a grant is active, admin page Break-glass review
- [x] Consent `app/ehr/consent.py`: `permit` / `deny` / `missing` per category from the generated Consent resources; degradations (one test each): no `followup_call` → no call, a nurse Task `followup-manual`; no `ai_processing` → template only; no `sms` → Communication `not-done` with the reason and a clerk Task `contact-patient`. `change_consent` writes through `FhirGateway.record_consent` (module `consent_management`; clerk, nurse, physician; audit `consent_change`) and publishes `consent.revoked` (and the new platform event `consent.granted`) on the WP3 bus; `POST /api/hospital/patients/{mrn}/consents`; consent card with record / withdraw buttons and the fallback text on the chart
- [x] Free-text de-identification `app/llm/freetext_deid.py`: span-based rule layer (dictionary from the record: patient and contact names in every spelling incl. Chinese, staff, MRN, health card, phones, address lines, postal codes, organisations; patterns: names after titles, before credentials, after or before family relations, Chinese names after cues or before honorifics, dates in ISO / slashes / dots / month names / Chinese with typed tokens `[DATE_n:D-2]` that keep the days from the reference date, phones with extensions, health cards with version codes, MRNs, street addresses and postal codes, organisations by suffix, emails), one token per person, map on the server (`Pseudonymizer`, `restore`); second pass `deid_check@1` through the LLM gateway (structured findings, hits replaced and stored encrypted in `deid_misses`, unavailable model → rule result stands) with a mock fixture; the LLM gateway now takes pre-redacted input (`structured(..., pseudonymizer=)`: only known identifiers are replaced again, so a FHIR resource's clinical dates stay), which closes the hook WP2 left open
- [x] Eval `evals/deid/`: 200 synthetic notes, 2,482 planted PHI spans (`scripts/gen_deid_eval.py`), runner `scripts/deid_eval.py` (`--check`, `--live`), report `report.md` / `report.json`, module README with the four sections. Result 2026-10-08: rule layer recall 0.9887 / precision 0.9992; with the second pass (mock provider) 0.9932 / 0.9992 (gate 0.98 / 0.90); date offsets right for 93% of the caught dates. The backend suite fails below the gate (`test_eval_meets_the_thresholds`)
- [x] Audit extension: `event_type` (read, write, ai_call, sign, break_glass, export, consent_change, simulator_event, plus login), `patient_mrn_hash`, `encounter_id`, `module`, `prompt_version` (migration 0005); null new fields are left out of the digest, so events written before WP4 verify unchanged (tested against the pre-WP4 digest); FhirGateway's purpose module moved from `reason` to `module`; the LLM gateway writes an `ai_call` event per call with the requesting user (request context, `app/core/context.py`) or `system`, the task as module and the prompt version; `GET /api/admin/audit` filters (outcome, event type, module, MRN via its hash, user) and `GET /api/admin/audit/export` (CSV, an `export` event); audit page with event type, module, patient hash, filter tabs, MRN filter and export
- [x] Signing service `app/ehr/signing.py` (`FhirGateway.sign_document`, `POST /api/hospital/documents/{id}/sign`): the only path to `docStatus=final`; the document's `source-module` (set by the gateway on create) must have a registry entry, the signer's role must be in its `required_signoff_role`, the patient in scope, and the signer a person; signatures as extensions; with `cosign` (medication reconciliation: pharmacist and physician) the draft stays preliminary until every role signed; `sign` audit events (refusals too). Demo drafts `doc-demo-discharge`, `doc-demo-handoff`, `doc-demo-medrec`; sign buttons on the chart come from the registry
- [x] Safety-tier registry `apps/api/config/modules.registry.json` (11 modules: tier, purpose, required_signoff_role, cosign, writes_allowed, consent_required; loader `app/core/registry.py`, copied into the API image); FhirGateway refuses writes of a module without an entry or outside its `writes_allowed`, for system actors too; `GET /api/hospital/registry`
- [x] Hospital API `app/ehr/platform_router.py` (units, census, role-shaped chart) and screens Patients (census per unit, open by MRN), patient chart, Break-glass review; Home has a hospital storyline (first for the hospital roles)
- [x] Tests: `tests/test_access.py` (23: the role table, one test per cell (6 roles × visibility, signing, writes), plus staff, imaging roles and the registry), `test_break_glass.py` (4), `test_consent.py` (7), `test_audit_extension.py` (9), `test_freetext_deid.py` (8); WP2 gateway tests now act as a system actor under a registered test module; Playwright `e2e/break-glass.spec.ts` (physician opens an ICU patient: dialog refuses a short reason, banner, admin review, audit). Backend suite 2026-10-08 (own test database): 355 passed (304 before WP4). Playwright regression 2026-10-08 (mock provider, API on 9001): 20 of 20 pass, the new break-glass spec and the 19 earlier ones
- [x] Demo: `demo/platform_governance.md` + `scripts/platform_demo.py` (rolled back unless `--commit`); `scripts/gateway_demo.py` reads the stay as the unit's physician now; data-model.md (access, break-glass, consent, signing, registry, free-text de-identification, audit) and audit-baseline.md (component table, naming map) updated

Notes and deviations:

- Spec field names: `consent.ai_processing` etc. on Patient are FHIR Consent resources per category (as the WP1 generator writes them); audit `timestamp` / `actor_id` / `actor_role` are the existing `ts` / `user_id` / `role`; tokens `[NAME_n]` are `[PERSON_n]` (patients, relatives) and `[STAFF_n]` (the kinds WP2 already used), dates `[DATE_n:D-2]`.
- The audit keeps `login` as its own event type besides the spec's eight; every other change (create, update, approve, …) is `write`. `action` keeps the precise verb.
- The registry is checked for writes only; reads need a purpose module but no entry (6.3's acceptance names writes). Consents are not enforced at the gateway: modules ask `consent.py` and degrade (the follow-up module still has to write its manual Task); WP4b's tool gateway checks `consent_required`.
- Break-glass is limited to physicians and nurses (the unit-scoped roles); hospital-wide roles do not need it, the administrator has no patient access to open. Grants and the banner run on the wall clock, not the simulated hospital clock. Break-glass widens patient scope, never resource types.
- Patient scope counts encounters active or finished within 72 hours (so a discharge summary can be signed after discharge) and the attending practitioner; unit-level reads (bed board, census, encounters by unit) are limited to the user's units for physicians and nurses, with no break-glass.
- Medication reconciliation is "co-signed by pharmacist and physician" (`cosign: true`): both sign, in any order. Order review lists both roles without cosign (either signs).
- The 6.3 governance items outside the acceptance list are partly here: the de-identification module has its four-section README (`evals/deid/README.md`) and its eval gate runs in the backend suite; prompt files under `prompts/<module>/<version>.md`, per-module READMEs for the agents and a CI release gate come with WP4b / 6.6 (the repository has no CI pipeline yet). `deid_check@1` lives in code like the existing prompts.
- Adding the hospital staff changes the demo data: an existing database gets them (and the draft documents) with the next demo reset. Login cards: four more.

Limits:

- The eval notes are synthetic and generated alongside the rules: the result shows the pipeline works, not performance on real notes. Remaining misses: given names with no cue, lower-case titles, dates in words; ambiguous numeric dates (05/10/2026 is read month first) give a wrong day offset though the date is redacted. Without an API key the second pass is a heuristic stand-in; `scripts/deid_eval.py --live` runs it on the configured model.
- The free-text layer is not yet applied automatically to the imaging modules' prompts (they keep the WP2-era `Pseudonymizer` pass); WP4b's agent runtime will route agent context through it.
- One `ai_call` audit event per LLM call is one more short transaction per call (about 10 ms locally).
- Unit scope is computed per gateway and patient from the patient's encounters (one query, cached for the gateway's lifetime); at demo size the chart page makes about ten audited gateway calls.

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
- Local Windows machine: port 8000 is reserved, so the API runs on 9001 for Playwright (`PW_API_PORT=9001 VITE_API_PROXY_TARGET=http://127.0.0.1:9001`). Also set `LLM_PROVIDER=mock`: the local `apps/api/.env` selects Gemini with a real key, and the specs expect mock mode (a run without it on 2026-10-08 sent real requests to Vertex AI, which returned 504, and most specs timed out).
