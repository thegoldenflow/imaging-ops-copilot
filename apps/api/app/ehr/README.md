# Hospital EHR platform (spec 6.1–6.3)

The synthetic hospital the AI modules sit beside: a FHIR R4 store, the only door into it (`FhirGateway`), the HL7
v2 adapter and event bus, the day simulator, and the platform rules every module inherits (role-based access,
break-glass, consent, signing, audit). The AI layer never replaces the EHR: it reads through the gateway, suggests in
the UI and writes drafts only. Web: **Patients** and the patient chart, **Break-glass review** (admin), the
Control Tower's control bar. API: `/api/hospital/...` (`router.py`, `platform_router.py`). Data model:
[docs/data-model.md](../../../../docs/data-model.md).

| File | What it does |
| --- | --- |
| `seed/` | The deterministic Synthea-style generator: 1,000 patients, 60 practitioners, 132 inpatient beds plus ED and OR, a 60-day timeline, today's state at 07:00 and the next 36 hours as the simulator's plan; the storyline preset (`seed/journey.py`) |
| `fhirstore.py`, `hapi.py` | The FHIR store in PostgreSQL (encrypted bodies, plain search columns, blind indexes for MRN and health card); the HAPI FHIR backend (`FHIR_BACKEND=hapi`) |
| `gateway.py` | `FhirGateway`: typed reads, the write allow-list, the role policy, audit of every call |
| `access.py`, `breakglass.py`, `consent.py`, `signing.py` | RBAC per role (6.3), break-glass grants and review, consent per category with degradations, the only path to `docStatus=final` |
| `events.py`, `hl7.py` | Domain events with a transactional outbox, per-consumer cursor and deduplication; the HL7 v2 adapter (ADT, ORM, ORU, SIU) |
| `simulator.py`, `scenarios.py`, `clock.py`, `simjobs.py` | The day simulator on the hospital clock; ICU surge, ED surge, OR overrun, consent withdrawal; background fast-forward |
| `storyline.py`, `accepted.py` | The 8.2 storyline's script; people's EHR actions on accepted suggestion cards |
| `imaging.py` | The imaging exam ↔ FHIR adapter (ServiceRequest, Encounter AMB, DiagnosticReport) |

## Input resources

- The generator's seed (`SEED`, default 42) and the seeding day: the hospital day starts at 07:00 on the seed date
  (the nearest weekday at a weekend); its data changes with the weekday, not the hour.
- Inbound HL7 v2 (`POST /api/hospital/hl7`, admin) and the simulator's plan events; scripted scenarios with their
  operator.
- Optional: a Synthea export localised with `scripts/localize_synthea.py`, and a HAPI FHIR server
  (`docker compose --profile ehr up -d hapi`, `scripts/load_fhir.py`).
- Callers: a signed-in user (`Actor.of(user)`: role, units, break-glass grant) or a system actor with a purpose
  module that has a registry entry (writes only).

## Output schema

- FHIR R4 resources as Pydantic models in `app/fhir/types/` (Patient, Encounter, Location, Observation,
  ServiceRequest, MedicationRequest / Statement, Condition, Procedure, AllergyIntolerance, DiagnosticReport,
  DocumentReference, CarePlan, Task, Flag, Communication, Appointment, Consent, Provenance, EpisodeOfCare, ...);
  one example per type in `app/fhir/examples/`; local codes and extensions under `urn:demo-hospital:*`
  (docs/data-model.md).
- `DomainEvent` (`events.py`): id, type (`patient.admitted`, `order.placed`, `flag.raised`, `document.signed`,
  `exception.opened`, ...), hospital time, correlation id, actor, source, references and routing attributes only.
- Audit events (`app/core/audit.py`): `read`, `write`, `ai_call`, `sign`, `break_glass`, `export`,
  `consent_change`, `simulator_event`, `approve`, `tool_call`, `login`, with the patient's MRN hash, encounter,
  module and prompt version; hash-chained, append-only.
- Gateway refusals: `FhirAccessDenied`, `BreakGlassRequired` (HTTP 403 with `break_glass_required`).

## Eval set and threshold

No model runs here; the checks are acceptance tests and reports:

- Day simulator: `evals/simulator/day_run.json` (`scripts/simulator_demo.py --report`): a planned day and the same
  day with the ICU surge; gate: at least six event types in timeline order, one patient per bed, every written
  resource valid.
- FHIR types and the exam adapter: `tests/test_fhir_types.py`, `tests/test_fhir_imaging.py` (lossless round trip of
  every seeded appointment).
- Access: `tests/test_access.py` (6 roles × visibility, signing and writes, one test per cell),
  `tests/test_break_glass.py`, `tests/test_consent.py` (one test per degradation), `tests/test_audit_extension.py`.
- Free-text and FHIR de-identification feed the AI layer: see [app/llm/README.md](../llm/README.md) and
  `evals/deid/` (recall >= 0.98, precision >= 0.90).

The registry modules that write here without a model (`registration`, `consent_management`, `bedside_nursing`,
`patient_messaging`, `workflow_engine`, `agent_runtime`, `ed_decision_support`) have eval status
`not_applicable`.

## Known failure modes

- The simulated day ends at the plan horizon (07:00 + 36 h; the storyline extends it); replaying means a demo reset
  (about 30–60 s). Patients waiting for a bed are admitted in plan order; nothing re-prioritises bed requests.
- Delivery is in process, in the API's single worker; a slow handler delays the others. Redis Pub/Sub is an
  interface only; a parked delivery stays parked (no redrive tool: integration health is an 8.1 roadmap card).
- With `FHIR_BACKEND=hapi` the simulator still writes the local store, so the boards follow the simulator only on the
  local backend; HAPI searches the gateway cannot express are filtered after fetching.
- Break-glass grants and the banner run on the wall clock, not the simulated hospital clock; break-glass widens
  patient scope, never resource types, and only physicians and nurses have it.
- Unit scope counts encounters active or finished within 72 hours, so a nurse loses a follow-up patient's details
  after that (the item stays visible with its priority).
- Several tests depend on the weekday the data is generated for (the hospital day follows the seeding weekday);
  `docs/PROGRESS.md` lists the known cases.
