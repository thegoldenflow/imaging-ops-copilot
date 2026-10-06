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

Status: in progress, branch `feat/phase-3-radiology-ops`

Shared groundwork: a technologist marks an exam done → the appointment becomes completed, an `ImagingStudy` is created, and completion hooks run (assign a reader, record CT dose). The seed adds studies and signed reports for the last 14 days of completed exams, CT dose records for 90 days, and four more synthetic radiologists with reading credentials (demo login stays one user per role). Long-running flows (critical-result escalation, nightly peer-review sampling) run in the in-process worker loop, like prior retrieval in phase 2, instead of Temporal.

### Shared step · Exam completion and reading roster

- [ ] Technologist marks an exam done; study created; completion hooks for later systems
- [ ] `Report` covers dictated (non-AI) reports as well as AI drafts; signer id recorded
- [ ] Synthetic radiologists with credentialed modalities; shift roster
- [ ] 14 days of synthetic studies and signed reports; unread studies form the starting backlog

### System 11 · Reporting Backlog & Turnaround Tracker

- [ ] Unreported studies grouped by site, exam type, priority and age
- [ ] Turnaround = signed − completed; targets per priority (configurable); at-risk and overdue flags
- [ ] Per-radiologist queue; reassignment suggestions to on-shift, credentialed readers
- [ ] Board refreshes live with new studies and sign-offs; reassignment is audited

### System 12 · Critical Results Tracker

- [ ] Case opened from a confirmed finding (report sign-off or dictation): finding, level, ordering physician, deadline
- [ ] Worker notifies the ordering physician (mock phone/fax), re-notifies, then escalates to the medical director per configurable policy
- [ ] Acknowledgement records who, when and how (staff entry or referrer portal); a case cannot close without one
- [ ] Full timeline for every case; unacknowledged demo case escalates on its own

### System 13 · Peer Review / QA

- [ ] Scheduled sampling at a configurable rate; blind assignment, never to the original reader
- [ ] Graded review (concur / minor / significant) with discrepancy type
- [ ] QA report by radiologist and exam type, QA lead (medical director) only; CSV export

### System 14 · CT Dose Monitoring

- [ ] Every completed CT has a dose record (CTDIvol, DLP) shaped like a DICOM RDSR
- [ ] Reference levels per protocol (medical director); exceedances listed and reviewable
- [ ] Trends by scanner and protocol

## Phase 4

Status: not started

## Environment notes

- `ANTHROPIC_API_KEY` is not configured in the cloud environment; everything runs in mock mode there.
- The cloud network policy blocks the chest X-ray dataset hosts (NIH ChestX-ray14, Open-i).
- Public deployment target is not decided yet.
