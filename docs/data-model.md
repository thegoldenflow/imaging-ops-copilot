# Hospital data model (FHIR R4)

Reference for every hospital-platform work package (`docs/SPEC-hospital.md` 6.1). The unit of work is the **Encounter**: every clinical resource hangs off an Encounter, every Encounter off a Patient, and the MRN is the patient's primary identifier. All data is synthetic (`app/ehr/seed/`).

Storage: the default backend keeps each resource as encrypted JSON in PostgreSQL (`fhir_resources`, `app/ehr/fhirstore.py`) with plain search columns. HAPI FHIR is the alternative backend (compose profile `ehr`). Modules never touch either directly; they go through `FhirGateway` (WP2).

## Resources

"Source" says where demo data comes from. "Modules" are the spec sections that use the resource.

| Resource | Purpose | Key fields | Source | Modules |
| --- | --- | --- | --- | --- |
| Patient | Primary index | identifier (MRN + synthetic health card), name (official; Chinese name as a second `usual` name), birthDate, gender, address, telecom, contact, communication.language | generator | all |
| Encounter | Visit or stay | class (EMER / IMP / AMB), status, statusHistory (ED: arrived → triaged → in-progress), period, serviceType, reasonCode, hospitalization (admitSource, dischargeDisposition), location[] (bed history), basedOn (bed request), participant (attending) | generator + day simulator | all |
| Location | Unit → Room → Bed tree | physicalType (wa / ro / bd), operationalStatus (O / U / C / K, v2-0116), partOf, managingOrganization | generator (6.2 step 4) | 7.1 |
| Organization | The hospital | name, type | generator | all |
| Appointment / Schedule / Slot | OR cases, clinic bookings | serviceType, participant (Patient, Location, Practitioner), start/end, minutesDuration, status (proposed / booked / arrived / fulfilled / cancelled) | generator, 7.4 | 7.1, 7.4 |
| ServiceRequest | Labs, imaging, consults, surgery, bed requests | code, category, priority, intent, status, authoredOn, occurrenceDateTime (completion), requester, encounter, locationReference | generator + 7.2 | 7.1, 7.2, imaging |
| MedicationRequest | Inpatient and discharge orders | medicationCodeableConcept (RxNorm), dosageInstruction, status, intent (order / plan), encounter, requester | generator | 7.2, 7.3 |
| MedicationStatement | Home medications (stand-in for the provincial drug history, DHDR) | medication, effectivePeriod, informationSource, context | generator | 7.2 |
| AllergyIntolerance | Allergies | code, criticality, reaction, extension `drug-class` | generator | 7.2 |
| Observation | Vital signs, labs, scores | code (LOINC), value[x], component (vital panel), effectiveDateTime, category, interpretation, encounter | generator + simulator | 7.1, 7.2, 7.3 |
| DiagnosticReport | Lab and imaging reports | code, result[], conclusion, presentedForm, basedOn | generator + imaging module | 7.3 |
| Procedure | Surgery and procedures | code (SNOMED CT), performedPeriod, performer (surgeon), location (OR), extensions `asa-class`, `surgical-urgency` | generator | 7.1, 7.3 |
| Condition | Diagnoses and problem list | code (SNOMED CT), clinicalStatus, category (problem-list-item / encounter-diagnosis) | generator | 7.2, 7.3 |
| CarePlan | Discharge and follow-up plans | activity[], period, category | 7.3 | 7.3, 7.4 |
| DocumentReference | Discharge summary, patient instructions, handoff, medication reconciliation | type, docStatus (preliminary / final), author, authenticator, content, extensions `source-module`, `signature` | 7.2, 7.3; three demo drafts for the signing service (WP4) | 7.2, 7.3 |
| Task | Work items awaiting signature, review or action | code, status, priority, owner, performerType (role), focus, for, encounter, basedOn, restriction.period.end (due) | platform, generator (pre-op checklist), Control Tower (`ai-review` of an exception, `flow-action`) | all |
| Flag | Safety flags (ALC, fall risk, isolation, NEWS2) | code, status, period, subject, encounter | generator, 7.1, 8.1 | 7.1 |
| Communication | Follow-up calls, reminders, escalations | payload, status, sent, recipient, sender, about | 7.4 | 7.4 |
| Practitioner / PractitionerRole | Staff and roles | name; role code, specialty, location (unit) | generator | platform RBAC |
| Provenance | Source chain of every AI output and human action | target, agent (Device = agent id and version, Practitioner = signer), entity (inputs), recorded, extensions prompt_version / model / run_id | runtime (6.4) | all |
| Consent | Patient consent | category (ai_processing / followup_call / sms), status, provision.type (permit / deny), dateTime | generator (some missing on purpose) | 6.3, 7.3, 7.4 |
| EpisodeOfCare | Care across encounters (e.g. a fracture from ED to follow-up) | status, period, diagnosis, managingOrganization, careManager | workflows (6.5) | 6.5, 7.4 |

## Relationship rules

- Every clinical resource (Observation, Condition, MedicationRequest, MedicationStatement, AllergyIntolerance, Procedure, DiagnosticReport, ServiceRequest) carries an `encounter` reference (MedicationStatement: `context`) to an Encounter that exists. Problem-list entries, home medications and allergies point to the patient's most recent outpatient visit. A test enforces this (`tests/test_ehr_store.py`).
- Location tree has exactly three levels, Unit → Room → Bed. Ids: unit `ORTH`, room `ORTH-05`, bed `ORTH-05-A`; the unit of a bed or room is the part before the first dash. Operating rooms are `OR-01`..`OR-04` under the `OR` location (no beds).
- `Encounter.location[]` records the bed history, one entry per move; the current bed has `status: active` and no period end. An ED patient waiting for a bay is at the unit location `ED`.
- An admission from the ED: the ED Encounter (EMER) gets a bed request ServiceRequest `bedreq-<visit>`; the inpatient Encounter (IMP) has `basedOn` that request and `hospitalization.admitSource = emd`.
- Patient identifiers: MRN `system = urn:demo-hospital:mrn` (8 digits); health card `system = urn:demo-hospital:hcn` (10 digits, version code in extension `hcn-version`, extension `synthetic = true`).
- Code systems as Synthea writes them: SNOMED CT (conditions, procedures), LOINC (observations), RxNorm ingredients (medications). ICD-10-CA and CCI appear only in the 8.1 coding stub. Local concepts use `urn:demo-hospital:*` (`codes.py`): CTAS is LOINC 11283-9 with an integer value 1–5; NEWS2, bed requests, pre-op checks, task, flag and document codes are local.
- Extensions are `urn:demo-hospital:ext:<name>`: `arrival-mode`, `ed-disposition`, `expected-los-days`, `asa-class`, `surgical-urgency`, `encounter` (on Appointment), `drug-class`, `synthetic`, `hcn-version`, `name-language`, `bed-capacity`, `unit-kind`; `imaging-appointment`, `imaging-study`, `imaging-report` (exam mapping below); `age-years` (de-identified Patient only).

## Ids from the generator

Generated ids are deterministic (same seed and hospital day → same ids), so eval cases can refer to them.

| Thing | Id |
| --- | --- |
| Patient | `pat-0001` … `pat-1000` |
| ED visit (Encounter EMER) | `ed-NNNNN`; CTAS `ctas-ed-NNNNN`, first vitals `vit-ed-NNNNN`, bed request `bedreq-ed-NNNNN` |
| Admission (Encounter IMP) | `stay-NNNNN` |
| Surgery | case `surg-NNNNN`: Appointment `appt-NNNNNN` (basedOn the request), Procedure `proc-surg-NNNNN`, request `orreq-surg-NNNNN`, day-surgery Encounter `amb-surg-NNNNN`; pre-op checks are Tasks `preop-consent` / `preop-npo` / `preop-labs` / `preop-blood-type` with focus `Appointment/appt-NNNNNN` (WP5 fix: the generator wrote the case id `Appointment/surg-NNNNN`, so the simulator never found them to close at the start) |
| Orders | ServiceRequest `ord-<encounter>-NN`, lab result Observation `obs-ord-<encounter>-NN` |
| Practitioners | `prac-doc-NN`, `prac-nurs-NN`, `prac-phar-NN`, `prac-cler-NN`, `prac-ops-NN`, surgeons `prac-surg-NN`; roles `role-<suffix>` |
| Written by the day simulator | planned entities keep their plan ids; resources the generator would number from a counter get ids scoped to the plan event (`cond-ed-03250-dec-1`, `medrx-stay-01190-adm-3`, `appt-surg-00450-book-1`); ward vital signs `vit-<stay>-<YYYYMMDDHH>`; patients added by a scenario `ed-x0001`, `stay-x0001`, `surg-x0001` |

## The hospital clock and plan

The hospital day starts at 07:00 on the seed date (nearest weekday at a weekend). The state at that moment is in the FHIR store; the next 36 hours (ED arrivals and their steps, admissions, ALC designations, transfers, discharges, housekeeping, OR bookings, cancellations, day-surgery visits, surgery start and end, orders and results) are in the `hospital_plan` module state for the day simulator (WP3). Events at the same minute are ordered so a bed is cleaned and patients leave before others arrive (`plan_order`). The hospital clock (`hospital_clock`: `now`, `rate`, `running`, `last_wall`, `applied`, `stopped`) moves only when the simulator runs; `app/ehr/clock.py` `hospital_now()` reads it.

## Events and the day simulator (WP3)

`app/ehr/simulator.py` plays the EHR: as the clock passes a planned event it writes the change into the FHIR store and sends the HL7 v2 message an EHR would send. `app/ehr/hl7.py` (the interface engine's converter) maps each message to a domain event; `app/ehr/events.py` publishes it. Subscribers see domain events only and read content through `FhirGateway`.

| HL7 v2 | Domain event | Simulator source | Event refs and attributes |
| --- | --- | --- | --- |
| ADT^A01 | `patient.admitted` | ED arrival (class EMER), admission (IMP), day-surgery check-in (AMB) | patient, encounter, location; `encounter_class` |
| ADT^A02 | `patient.transferred` | bed move (ICU step-down) | from_location, location |
| ADT^A03 | `patient.discharged` | end of an ED visit (home, lwbs, admitted), stay, day-surgery visit | from_location; `encounter_class`, `disposition` |
| ADT^A08 | `encounter.updated` | ED triage, seen by a physician, disposition decided, ALC designation | location; `change` (triaged, seen, decision, alc), `disposition` |
| ADT^A20 | `bed.status_changed` | housekeeping done (K → U) | location; `status` |
| ORM^O01 | `order.placed` | lab, imaging and consult orders | order (ServiceRequest); `category` |
| ORU^R01 | `result.available` | lab and imaging results, consult done, ward vital signs every 6 h | order, results (Observation / DiagnosticReport); `category` |
| SIU^S12 / S14 / S15 | `appointment.scheduled` / `updated` / `cancelled` | OR case booked, started or finished, cancelled | appointment, location (OR); `status` |
| (none) | `consent.revoked` / `consent.granted` | consent management (WP4, `app/ehr/consent.py`) and the scripted consent scenario | patient, consent; `category`, `previous` |

Every event has `event_id` (stable per source message, so a resent HL7 message is the same event), `correlation_id`, `actor` (`system:demo-ehr`, or `user:<id>` when a person triggered it, e.g. a scripted scenario) and `occurred_at` (hospital time). Events carry references and codes only: no names, no MRN, no clinical text.

Storage (migration 0004, emptied by a demo reset): `domain_events` (the outbox; written in the same transaction as the FHIR changes), `event_consumers` (each subscriber's cursor), `event_deliveries` (per subscriber and `event_id`: done, failed, parked; the deduplication record).

The plan is the intent and the FHIR store is the truth: a planned bed that is taken (a scenario's patients, a bed manager's move) means another free bed in the unit or its overflow units, and if there is none the patient keeps boarding in the ED (bed request active) and the stay's events move 30 minutes later. A bed left for housekeeping without a planned cleaning gets one. The simulator writes the local FHIR store only; with `FHIR_BACKEND=hapi` the simulated day does not reach the HAPI server.

## Access: FhirGateway (WP2)

`app/ehr/gateway.py`. Modules never use the FHIR store or HTTP directly; two grep tests in `tests/test_fhir_gateway.py` enforce it. A gateway is opened for an actor (`Actor.of(user, request)`, later agents and system jobs) and a purpose module.

- Hospital-wide reads (WP5, for actors whose scope is the whole hospital: system jobs, agents on their own, hospital-wide roles): `search(type, **params)` (plain dicts, the store's search parameters), `resolve_refs(refs)` (which `Type/id` references exist), and `batch(label)`, a block whose patient-less reads are audited as one record (resource type `Bundle`, the reads listed in the reason; patient reads, refusals and writes are still audited one by one).
- Typed reads (`app/fhir/types` models): `get_patient(mrn)`, `search_encounters(mrn=, patient_id=, cls=, status=, unit=, active_at=, since=, until=)` (newest first), `get_active_encounter(mrn)`, `get_bed_board(unit)` (beds with status, occupant encounter and since when, plus encounters waiting at the unit without a bed), `get_orders(encounter)` (ServiceRequest), `get_medications(encounter)` (MedicationRequest), `get_home_meds(mrn)` (active MedicationStatement), `get_observations(encounter, codes, since)` (a code also matches a vital-panel component), `get_documents(encounter)`, `read(type, id)`.
- Write allow-list: Task (create, update, any status); DocumentReference (create as `docStatus=preliminary`, change only while preliminary; final only through the signing service, `sign_document`); Consent (`record_consent`, consent management only); Communication and Flag (create, update); Appointment (create as `status=proposed`, change only while proposed; booked only through `book_appointment`, by a clerk: the privileged tool bookAppointment after a recorded approval, WP4b); Provenance (`record_provenance`, the agent runtime only, WP4b); `append_encounter_location(encounter, bed)` for bed managers (`operations_manager`): closes the current location entry, appends the bed, and, as the EHR's transfer does, marks the new bed occupied and the old one for housekeeping. Everything else, including any delete, raises `FhirAccessDenied` and leaves a `denied` audit record.
- Audit: one record per call (actor and role, action and event type, resource type, resource id or the scope: patient, encounter or unit, the patient's MRN hash, the encounter, and the purpose module in `module`; see "Audit log" below).
- Access (WP4): see "Access, break-glass, consent and signing" below.
- Backends (`FHIR_BACKEND`): `local` (this PostgreSQL store, in the request's unit of work) and `hapi` (`app/ehr/hapi.py`, `FHIR_BASE_URL`, `FHIR_AUTH_MODE=none`; `smart_backend` is defined, not implemented). The HAPI backend narrows the server query only with parameters that map exactly or to a superset, then matches every result on the same index columns as the local store, so both return the same resources (checked against a running HAPI in `test_both_backends_answer_the_same`).
- The day simulator (WP3) and the generator play the EHR itself and write the store directly; the allow-list is for the AI layer.

## De-identification of resources (WP2)

`app/llm/fhir_deid.py`, `FhirDeidentifier.redact_all(resources)` before anything goes into a prompt; `restore()` re-identifies model output on the server. Field list per resource type in `STRUCTURED` and `FREE_TEXT`:

| What | Replaced by |
| --- | --- |
| Patient and contact names (every spelling, Chinese names included), staff names, reference displays naming a person | `[PERSON_n]` / `[STAFF_n]`, one token per person |
| Birth date | extension `age-years` |
| Address, phone, email | `[ADDRESS_n]`, `[PHONE_n]`, `[EMAIL_n]` |
| Health card (and its version code), other patient identifiers | `[HEALTH_CARD_n]`, `[ID_n]` |
| MRN | `[MRN_<keyed hash>]` (blind-index HMAC, so not reversible by hashing all 8-digit numbers) |
| Free text: note, conclusion, presentedForm, document content, descriptions, comments, payloads, valueString, reaction descriptions, care plan activities | every learned identifier, then the regex layer (emails, 10-digit numbers, ISO and long dates, phone numbers); text attachments decoded and re-encoded, binary attachments dropped |
| Narrative (`text`) | dropped |

Codes, ids, references and clinical times stay. Free text outside resources (notes, handoffs, transcripts) goes through `app/llm/freetext_deid.py` (WP4, below). The LLM gateway takes input de-identified either way with `structured(..., pseudonymizer=)`: it then repeats only the replacement of known identifiers (no date patterns, so a resource's clinical times stay) and re-identifies the output with the same map.

## Access, break-glass, consent and signing (WP4, spec 6.3)

Roles (`app/core/models.py` `Role`): `physician`, `nurse`, `pharmacist`, `clerk` join the imaging roles; the spec's `ops_manager` is `operations_manager`. Hospital staff are StaffUsers built from the generated Practitioner / PractitionerRole (`app/ehr/seed/platform.py`): `practitioner_id`, `unit_ids` (PractitionerRole.location), user id `U-<practitioner suffix>` (`prac-doc-09` -> `U-DOC-09`). Demo logins: `U-DOC-09` and `U-NURS-05` (Medicine A), `U-PHAR-01`, `U-CLER-01`; the imaging centre's `U-OPS` and `U-ADMIN` are the operations manager and admin.

`app/ehr/access.py` (enforced in FhirGateway; the hospital API goes through it):

| Role | Resource types | Patient scope | Views | Writes |
| --- | --- | --- | --- | --- |
| physician | all | own units (or attending), encounters active or finished in the last 72 h; others need break-glass | full | Task; DocumentReference of modules it signs for; signs per the registry |
| nurse | all | same | full | Task, Flag, Communication; DocumentReference of modules it signs for |
| pharmacist | directory, Patient, Encounter, MedicationRequest / Statement, AllergyIntolerance, Observation (laboratory only), DocumentReference (medrec, discharge med list) | hospital | full | DocumentReference of modules it signs for |
| clerk | directory, Patient, Encounter, Appointment, Schedule, Slot, Consent, Task (performer clerk) | hospital | Encounter without reasonCode / diagnosis | Appointment (proposed), Task (performer clerk), Consent |
| operations_manager | directory, Patient, Encounter, Appointment, Schedule, Slot, ServiceRequest (bed and surgery requests), Task (performer ops), Flag | hospital | Patient = MRN only; Encounter, Appointment, ServiceRequest without clinical elements | Task (performer ops), Encounter.location |
| admin | directory | none | - | none |

"Directory" = Location, Organization, Practitioner, PractitionerRole. Imaging roles have no hospital access; system actors (`Actor.system`) are trusted and audited. A patient outside a unit-scoped user's scope raises `BreakGlassRequired` (HTTP 403 with `code: break_glass_required`).

Break-glass (`app/ehr/breakglass.py`, table `break_glass_grants`; reason, MRN and review note encrypted): reason of at least 10 characters, one grant per user and patient for 4 hours (wall clock), a `break_glass` audit event, review due within 24 hours, the review (justified / not_justified with a note) as a second `break_glass` event. Reads under a grant carry `break-glass BG-...` in their audit reason.

Consent (`app/ehr/consent.py`): per category `permit` / `deny` / `missing` (the latest active Consent). Degradations: no `followup_call` -> no call, nurse Task `followup-manual`; no `ai_processing` -> template only; no `sms` -> Communication `not-done` plus a clerk Task `contact-patient`. `change_consent` writes through `FhirGateway.record_consent` (module `consent_management`) and publishes `consent.revoked` / `consent.granted`.

Signing (`app/ehr/signing.py`): a DocumentReference carries the extension `source-module` (set by the gateway on create); signing it needs that module's registry entry, a signer role in its `required_signoff_role` (every listed role with `cosign`), the patient in the signer's scope, and a person (not a system actor). Each signature is an extension `signature` (role, signer, time); the last one sets `docStatus=final` and `authenticator`. Demo drafts: `doc-demo-discharge` and `doc-demo-handoff` (one Medicine A inpatient), `doc-demo-medrec` (another).

Safety-tier registry: WP4's `config/modules.registry.json` is now the agent registry (WP4b, below): per entry `risk_tier`, `required_signoff_role`, `cosign`, `consent_required`, and the writes derived from its tools. FhirGateway refuses writes of modules without an entry or outside those writes; reads only need a purpose module.

## Agent runtime and tool gateway (WP4b, spec 6.4)

Every AI function is a registered agent calling registered tools (`app/agents/`).

Agent registry `apps/api/config/agents/<agent_id>.yaml` (`app/agents/registry.py`; it replaced WP4's `modules.registry.json`): `agent_id`, `version` (semver), `kind`, `owner`, `purpose`, `risk_tier` (ops / documentation / clinical_ds), `allowed_tools`, `data_scope` {`resources`, `scope`: encounter / own_unit / hospital / none}, `model_policy` {`tier` (the model id comes from the environment), `max_tokens`, `temperature_max`}, `prompt_version` (the pointer to the prompt in use), `required_signoff_role`, `cosign`, `consent_required`, `eval_status` {`status`: pending / passed / failed / not_applicable, `run_id`, `note`}, `deployment_status` (dev / demo / prod_ready), `entrypoint`, `confidence_threshold`. Kinds: `runtime_agent` (hosted by the runtime; code in `entrypoint`), `embedded_agent` (the imaging systems' model calls, made by their modules through the LLM gateway), `module` (no model; registered for its EHR writes). 27 entries: 14 embedded agents (13 imaging tasks and the de-identification second pass), 8 runtime agents (the 7.1–7.4 agents with their WP4 rights, code in WP5–WP8, and the reference agent `patient_message_triage`), 5 modules (registration, consent management, bedside nursing, patient messaging, `agent_runtime` itself).

Tool registry `apps/api/config/tools/<tool_id>.yaml`: `tool_id`, `description`, `side_effect_level` (read / recommend / action / privileged), `handler` (the domain binding in `app/agents/handlers.py`), `domain`, `reads` (resource types returned), `fhir_writes` (resource type -> statuses), `input_schema` / `output_schema` (JSON Schema, the subset in `app/agents/jsonschema.py`), `permission_policy` {`roles` (caller roles, `system` for background runs), `approver_roles` (privileged: a list or `signoff` = the agent's signers), `target` (the argument naming the encounter, patient, document, task, appointment, flag or unit)}, `idempotency` {`required`, `window_hours`}, `timeout_ms`, `retry` {`max_attempts`, `backoff_ms`}, `audit_level`, `internal`. 30 tools: 13 read, 2 recommend (draftDocument, submitForReview), 11 action, 4 privileged (signDocumentFinal, bookAppointment, appendEncounterLocation, sendPatientSMS). What an entry may write to the EHR is the union of its tools' `fhir_writes`; FhirGateway checks writes against it and the signing service reads `required_signoff_role` / `cosign`.

Tool Gateway (`app/agents/gateway.py`), in order: agent registered and allowed by the release gate; tool registered and on the allow-list, arguments valid against the closed input schema; caller role in `permission_policy.roles`; data scope (returned types in `data_scope.resources`; target in the run's encounter / the user's units / any patient the user may see; agents never use a user's break-glass grant); consent (every `consent_required` category `permit` for the target patient); then by level: recommend output must be a draft or proposal (DocumentReference preliminary, Task intent proposal); action and privileged calls carry the idempotency key `sha256(agent_id, encounter_id, tool_id, canonical(args))` and a repeat within 24 hours returns the first result (`tool_idempotency`, encrypted); privileged calls need an approval record (a completed `approval-request` Task for exactly these arguments, decided by an approver role, plus that person's `approve` audit event); without `approval_task_id` the call opens such a Task and returns `approval_required`. Privileged calls run on the approver's authority with 1 attempt and two audit events (`privileged_call`: approved, then the result). Handlers run with the registry's retries for transient errors; afterwards the caller's fallback, else a `needs-human` Task. Every call is a `tool_call` audit event (module = agent).

Runtime (`app/agents/runtime.py`): `runtime.start(agent_id, user=, encounter_id=)` loads a registered, deployable agent (`UnregisteredAgent`, `AgentNotDeployable`) and checks the user may act on the encounter; `run.gather(...)` runs read tools and de-identifies their resources (`fhir_deid`, learning the identifiers for `freetext_deid`); `run.model(...)` calls the LLM gateway with that token map and puts untrusted text in its own `<untrusted>` block (the prompt must carry the rule that such text is data, never instructions); `run.tool(...)` goes through the Tool Gateway; `run.finish()` closes the trace and writes the Provenance. A privileged call resumes the run after the approval (`execute_approved`). APP_MODE=prod refuses agents whose eval_status is not passed or whose deployment_status is not prod_ready; demo mode runs them and flags their output.

Trace (`agent_traces`, one row per run, references and hashes only): `run_id`, `agent_id`, `agent_version`, `kind` (run / call), `mode`, `model`, `prompt_version`, `eval_status`, `evaluated`, `actor_id`, `actor_role`, `encounter_id`, `context_id` (the phone agent's call session), `input_refs`, `llm_calls` (call id, agent, model, prompt version, status, tokens, cost, latency), `tool_calls` (tool, level, `args_hash`, status, reason, `result_ref`, `latency_ms`, attempts, idempotency key, approval task), `output_refs`, `confidence`, `human_action` {role, user_id, decision, at, task_id}, `outcome`, `tokens_in`, `tokens_out`, `cost_usd`, `provenance_id`, `started_at`, `finished_at`. An embedded agent's model call gets a one-call trace whose output is the LlmCall.

Provenance `prov-<run_id>` (written by the runtime through `FhirGateway.record_provenance`): `target` = the FHIR resources the run wrote, `agent` = the agent (`who` a Device reference by identifier `urn:demo-hospital:agent|<agent_id>@<version>`, `onBehalfOf` the user's Practitioner) plus every signer as `verifier`, `entity` = the input resources (`source`), extensions `prompt-version`, `model`, `run-id`. Every resource an agent writes carries the extensions `ai-run` (run id) and `ai-agent` (`agent_id@version`), so `lineage(ref)` (`GET /api/agents/lineage?ref=`) goes from any output to its trace and Provenance.

Tasks of the runtime (`urn:demo-hospital:task`): `approval-request` (input: tool, agent, run, args_hash, args, on_behalf_of; output after the decision: decision, decided_by, note), `ai-review` (intent proposal; input: recommendation, evidence), `needs-human`. Decisions: `POST /api/agents/tasks/{id}/decision` (approve / reject; an approved privileged call then runs), queue `GET /api/agents/approvals`.

## Free-text de-identification (WP4)

`app/llm/freetext_deid.py`: `FreeTextDeidentifier.learn_fhir(resources)` (or `add_person` / `add_known`) collects the record's identifiers; `detect(text)` finds the spans (dictionary and patterns), `apply` replaces them with typed tokens of the shared `Pseudonymizer`: `[PERSON_n]`, `[STAFF_n]`, `[DATE_n:D-2]` (days from the reference date), `[ADDRESS_n]`, `[PHONE_n]`, `[EMAIL_n]`, `[HEALTH_CARD_n]`, `[MRN_<keyed hash>]` for a known MRN (else `[MRN_n]`), `[ORG_n]`, `[ID_n]`; one token per person whatever the spelling; `restore()` re-identifies. `deidentify(text, deid)` adds the second pass (`deid_check@1` through the LLM gateway, structured findings; hits replaced and stored encrypted in `deid_misses`). Eval: `evals/deid/` (200 notes from `scripts/gen_deid_eval.py`, scored by `scripts/deid_eval.py`).

## Audit log (WP4)

`audit_events` (append-only, hash chain) gained `event_type` (`read`, `write`, `ai_call`, `sign`, `break_glass`, `export`, `consent_change`, `simulator_event`, plus `login`; WP4b: `tool_call`, `approve`), `patient_mrn_hash` (keyed blind index of the MRN), `encounter_id`, `module`, `prompt_version` (ai_call). The spec's `timestamp`, `actor_id`, `actor_role` are the existing `ts`, `user_id`, `role`. Null new fields are left out of the digest, so events written before them verify unchanged. The LLM gateway writes an `ai_call` event per call (actor from the request, else `system`; module = the task). Admin: `GET /api/admin/audit` (filters outcome, event_type, module, mrn, user_id) and `GET /api/admin/audit/export` (CSV, itself an `export` event).

## Imaging exams (mapping, WP2)

The imaging module's booked exam (`Appointment` + its `ImagingStudy` + `Report`, or a study without an appointment such as the chest X-ray worklist) maps to ServiceRequest + Encounter (class AMB) + DiagnosticReport; the old tables stay. `app/ehr/imaging.py`: `exam_to_fhir(exam)`, `exam_from_fhir(sr, enc, dr)`, `load_exam(store, appointment_id= | study_id=)`.

| Imaging field | FHIR |
| --- | --- |
| appointment id, study id, report id | `identifier` (`urn:demo-imaging:appointment` / `study` / `report`); resource ids `img-ord-<key>`, `img-enc-<key>`, `img-rpt-<report>` |
| patient, referrer | `subject`, ServiceRequest `requester` (the imaging records: linking them to hospital MRNs is the 8.1 MPI card) |
| exam code | ServiceRequest and DiagnosticReport `code` (`urn:demo-imaging:exam`) |
| urgency P1–P4 | ServiceRequest `priority` stat / asap / urgent / routine |
| booked at, slot | ServiceRequest `authoredOn`, `occurrenceDateTime`; Encounter `period` |
| status | Encounter `status` (booked, confirmed → planned; completed → finished; cancelled, no-show → cancelled), ServiceRequest `status` (active / completed / revoked) |
| site | Encounter `location` |
| requisition, approved protocol | ServiceRequest `requisition`, `orderDetail` |
| indication, DICOM study UID | ServiceRequest `reasonCode.text`, Encounter `identifier` (`urn:dicom:uid`) |
| report status, signer, signed at, impression, full text | DiagnosticReport `status` (draft → preliminary, signed → final), `resultsInterpreter`, `issued`, `conclusion`, `presentedForm` |
| everything else (no-show risk and factors, reminder state, scanner, image key, outside facility, AI draft sections, urgent findings, model and prompt version, edit ratio, ...) | complex extensions `urn:demo-hospital:ext:imaging-appointment` / `imaging-study` / `imaging-report`, one typed sub-extension per field |

Empty text is left out (FHIR has no empty strings); an empty optional text comes back as None.

## Control Tower (WP5, spec 7.1)

`app/modules/control_tower/` (README there), web `apps/web/src/features/control-tower/`, API `/api/control-tower/...`.

- Boards (`snapshot.py`): ED, beds and OR read through FhirGateway as the Control Tower (agent actor `agent:control_tower`, data scope Location, Encounter, Patient, ServiceRequest, Appointment, Procedure, Observation, Task, Flag), ten searches in one audited batch; rebuilt when `store.version`, the hospital clock or the last domain event changes; the API's hospital loop refreshes after each event drain.
- Flow models (`features.py`, `flowdata.py`, `flowmodels.py`; files and report in `apps/api/models/flow/`): admission from triage, discharge within 24 h, surgical duration, ED wait; scikit-learn histogram gradient boosting; per-feature path attributions for the one-sentence explanation.
- Rules (`rules.py`): unit occupancy >= 95%, >= 3 ED boarders over 2 h, OR case predicted >= 30 min over its booking, pre-op check open < 4 h before the start; severities low / med / high; each finding carries the engine's facts, FHIR evidence references and action menu (owner roles physician, nurse, operations_manager).
- Exceptions: table `flow_exceptions` (migration 0007; the narration encrypted): `id` `EXC-NNNNN`, `key` (`unit_occupancy:MEDA`, `ed_boarding:ED`, `or_overrun:appt-…`, `preop_gap:appt-…`), rule, severity, status (open, deferred, approved, rejected, cleared), title, summary, unit, subject, facts, evidence, menu, hospital-clock times (detected, changed, cleared, remind), narration, review Task, decision.
- Narrator (`agent.py`, runtime agent `control_tower` 0.2.0, prompt `control_tower@1`): reads the unit's bed board and up to three encounters through its read tools, writes the spec's schema (plus each action's `action_id` from the menu), guarded (menu actions and owner roles, only the engine's numbers, no claim of execution, the engine's severity and evidence; one regeneration, then the engine's template); evidence resolved in FHIR before display. Tools: `explainException` (recommend; an `ai-review` Task, intent proposal, performerType ops + nurse, focus the unit's Location, input exception, severity, source, recommendation, evidence) and `createFlowTask` (action; a Task code `flow-action` for the action's owner role, focus the Location or the encounter, basedOn the review Task, input exception, action, approved_by; idempotent per exception and action).
- Decisions: approve (`approvals.decide` on the review Task = the `approve` audit event; then the agent, acting as itself, calls `createFlowTask` once per action; the run's trace records the human action), reject (reason >= 5 characters), defer (15–720 hospital minutes; a `write` audit event on `FlowException`; reopens as a reminder when the clock passes it). Deciders: the operations manager, or a nurse for an exception on one of their units. Without a deployable agent (prod mode before its eval passes) the review Task is missing, the approval is audited on the FlowException and the Tasks are written by the module.
- Patient card: read as the user (`FhirGateway(Actor.of(user))`): the operations manager gets MRN, bed, admission and expected discharge (admission + `expected-los-days`) and the list of hidden fields; nurse and physician of the unit the clinical card; outside their units 403 `break_glass_required`.
- Simulator: `POST /api/hospital/simulator/fast-forward/start` runs the fast-forward as a background job (`app/ehr/simjobs.py`, steps of 30 hospital minutes, each committed and drained); `GET /api/hospital/simulator` reports it. A demo reset holds `REWRITE_LOCK` (exclusive) and every background step takes it shared, so the reset's TRUNCATE never deadlocks with a step. A person's simulator action (advance, fast-forward, run, pause, inject) waits up to 5 s for a background tick holding the simulator lock (`simulator.LOCK_WAIT`) before answering 409; the tick itself only tries the lock.
