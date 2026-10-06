# Progress

## Demo-scope decisions (agreed with the owner)

Time is short and the goal is a working demo, so the first batch deviates from the spec's stack in these ways:

| Spec | Demo build | Why / how to upgrade |
| --- | --- | --- |
| PostgreSQL + SQLAlchemy + Alembic | In-memory store (`app/core/store.py`) rebuilt from a seeded generator on start and on "Reset demo" | All reads and writes go through the store object; a database repository can replace it later |
| Field-level PHI encryption | Not implemented (nothing is persisted) | Needed once a database is added |
| Temporal workflows | In-process scheduler loop that sends due messages every 5 s | Replace with Temporal when long-running flows (systems 10, 12) arrive |
| Orthanc + DICOM | Synthetic PNG "phantom" chest images; PNG/JPEG upload | Real DICOM support and de-identification of DICOM tags later |
| STT/TTS services, Twilio | Browser Web Speech API (Chrome) plus typed input | Pluggable STT/TTS later; latency target must be measured locally |
| shadcn/ui, OpenAPI type generation | Small hand-written component set, hand-written types | Fine for demo size |
| Claude API | Real Claude when `ANTHROPIC_API_KEY` is set, otherwise mock outputs through the same gateway | Same code path either way |

## Phase 0 — Backend Infrastructure (System 1)

Status: done (demo scope)

- [x] FastAPI app, settings from environment, health endpoint, JSON logs
- [x] Shared entities with FHIR-style names; synthetic seed: 5 sites, 12 scanners, 2000 patients, 150 referrers, ~3 months of history with cancellations and no-shows, 2 weeks of bookings, storyline patients; one-click reset
- [x] Demo login per role; every endpoint checks role, site-scoped staff limited to their sites; optional `DEMO_PASSCODE`
- [x] Audit log of PHI reads and writes and every denial (user, role, time, record, action, IP, reason); append-only SHA-256 hash chain with verification
- [x] Claude call layer: de-identification with re-identification of tool inputs, versioned prompts, Pydantic schema with one retry then "needs human review", degradation on timeout/API failure, call log without PHI (task, model, prompt version, tokens, latency, cost, input hash)
- [x] Mock SMS, email, phone, OHIP and private insurance with configurable latency and failure rate
- [x] AI usage dashboard and audit log viewer
- [ ] Field-level PHI encryption (deferred with the database)

Acceptance:

- [x] One command per app starts the demo; login and role switching work
- [x] Unauthorized access returns 403 and leaves an audit record
- [ ] PHI ciphertext in the database (no database in demo scope)
- [x] Call layer unit tests: de-identification, retry after schema failure, timeout degradation

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

Status: in progress (branch `feat/phase-2-requisitions`)

Pipeline: requisition arrives → shared extraction (one Claude call) → triage (5) → protocol suggestion (6) → contrast check (7, contrast exams) → MRI screening (8, MRI) → after booking: prep instructions (9) and prior imaging retrieval (10) in parallel.

Demo-scope notes: requisitions are synthetic text generated with ground-truth labels (the same generator writes the eval sets). The mock provider is a rule-based baseline extractor, so the whole pipeline runs without an API key. Seeded requisitions are pre-processed with that baseline; a few arrive unprocessed for live demos. Prior-imaging retrieval runs as an in-process worker with retries instead of Temporal.

### Shared step · Requisition extraction

- [ ] `Requisition`, `LabResult`, `Allergy` entities and a synthetic requisition generator with labels
- [ ] Extraction schema (requested exam, indication, history, prior imaging, allergies, renal/diabetes, implant hints, stated urgency), each value with its source quote and confidence
- [ ] Side-by-side view: original text with highlighted sources, low-confidence fields in yellow, staff corrections recorded
- [ ] Eval set: 50 synthetic requisitions with field labels; field-level accuracy

### System 5 · Priority Triage

- [ ] AI priority P1–P4 with rationale and red flags; target wait days per tier in config
- [ ] Queue sorted by days left to target; overdue in red
- [ ] Radiologist override requires a reason, is audited and feeds agreement stats
- [ ] Acceptance: new requisition triaged and queued within 1 minute; eval script reports model vs radiologist agreement

### System 6 · Protocol Assignment Assistant

- [ ] Synthetic protocol library (indications, contrast, duration)
- [ ] Retrieval of candidate protocols, then Claude picks primary + 2 alternatives with rationale and contrast flag
- [ ] One-click approve or change; approved duration sets the booking slot length
- [ ] Acceptance: every new requisition has a suggestion; adoption rate and common changes on a stats view
- [ ] Eval set: 40 indications with correct protocol; top-1 / top-3 hit rate

### System 7 · Contrast & Renal Checker

- [ ] Synthetic eGFR results and allergy records
- [ ] Rule thresholds set by the medical director on a settings page (placeholder values, not hard-coded)
- [ ] Status pass / needs eGFR / needs review / needs premedication with the basis written out
- [ ] Acceptance: every contrast booking has a status and basis; changing a threshold recomputes all statuses immediately

### System 8 · MRI Safety Screening Assistant

- [ ] Patient questionnaire in English, French, Chinese and Punjabi, sent by link
- [ ] Claude extracts devices from free-text answers and matches a synthetic device list
- [ ] Flags only ever ask for review; technologist or radiologist review records the decision
- [ ] Acceptance: 4 languages; a flagged appointment cannot be confirmed before review
- [ ] Eval set: 30 implant descriptions; device recognition accuracy

### System 9 · Patient Prep Instructions

- [ ] English templates per prep type, approved by clinical staff
- [ ] Claude drafts translations; a person approves them; only approved versions are sent
- [ ] Sent at booking and 48 hours before, in the patient's preferred language
- [ ] Acceptance: every new booking gets prep in the preferred language; unapproved translations cannot be sent

### System 10 · Prior Imaging Retrieval

- [ ] Mock outside image archives with configurable latency and failure rate
- [ ] Retrieval tasks created from the extraction and patient history after booking
- [ ] Worker with retry, backoff and timeout; states requested / received / not found / failed; received studies linked to the exam
- [ ] Acceptance: booking creates retrieval tasks shown on a board; forced failures retry by policy and end in a final state

## Phases 3–4

Status: not started

## Environment notes

- `ANTHROPIC_API_KEY` is not configured in the cloud environment; everything runs in mock mode there.
- The cloud network policy blocks the chest X-ray dataset hosts (NIH ChestX-ray14, Open-i).
- Public deployment target is not decided yet.
