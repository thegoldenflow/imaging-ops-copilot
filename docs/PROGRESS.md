# Progress

## Demo-scope decisions (agreed with the owner)

Time is short and the goal is a working demo, so the first batch deviates from the spec's stack in these ways:

| Spec | Demo build | Why / how to upgrade |
| --- | --- | --- |
| PostgreSQL + SQLAlchemy + Alembic | In-memory store (`app/core/store.py`) rebuilt from a seeded generator on start and on "Reset demo" | All reads and writes go through the store object; a database repository can replace it later |
| Field-level PHI encryption | Not implemented (nothing is persisted) | Needed once a database is added |
| Temporal workflows | In-process loops: message dispatch every 5 s; a worker every 2 s for requisitions, prior retrieval (10), critical-result escalation (12) and nightly peer-review sampling (13) | State lives in the store, so a restart loses in-flight steps; move these to Temporal with the database |
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

## Environment notes

- `ANTHROPIC_API_KEY` is not configured in the cloud environment; everything runs in mock mode there.
- The cloud network policy blocks the chest X-ray dataset hosts (NIH ChestX-ray14, Open-i).
- Public deployment target is not decided yet.
