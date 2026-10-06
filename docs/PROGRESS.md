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

## Phases 2–4

Status: not started

## Environment notes

- `ANTHROPIC_API_KEY` is not configured in the cloud environment; everything runs in mock mode there.
- The cloud network policy blocks the chest X-ray dataset hosts (NIH ChestX-ray14, Open-i).
- Public deployment target is not decided yet.
