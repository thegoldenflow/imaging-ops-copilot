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
| Task | Work items awaiting signature, review or action | code, status, priority, owner, performerType (role), focus, for, encounter, restriction.period.end (due) | platform, generator (pre-op checklist) | all |
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
| Surgery | case `surg-NNNNN`: Appointment `appt-NNNNNN` (basedOn the request), Procedure `proc-surg-NNNNN`, request `orreq-surg-NNNNN`, day-surgery Encounter `amb-surg-NNNNN` |
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

- Typed reads (`app/fhir/types` models): `get_patient(mrn)`, `search_encounters(mrn=, patient_id=, cls=, status=, unit=, active_at=, since=, until=)` (newest first), `get_active_encounter(mrn)`, `get_bed_board(unit)` (beds with status, occupant encounter and since when, plus encounters waiting at the unit without a bed), `get_orders(encounter)` (ServiceRequest), `get_medications(encounter)` (MedicationRequest), `get_home_meds(mrn)` (active MedicationStatement), `get_observations(encounter, codes, since)` (a code also matches a vital-panel component), `get_documents(encounter)`, `read(type, id)`.
- Write allow-list: Task (create, update, any status); DocumentReference (create as `docStatus=preliminary`, change only while preliminary; final only through the signing service, `sign_document`); Consent (`record_consent`, consent management only); Communication and Flag (create, update); Appointment (create as `status=proposed`, change only while proposed; booking needs a clerk, WP4b); `append_encounter_location(encounter, bed)` for bed managers (`operations_manager`): closes the current location entry, appends the bed, and, as the EHR's transfer does, marks the new bed occupied and the old one for housekeeping. Everything else, including any delete, raises `FhirAccessDenied` and leaves a `denied` audit record.
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

Safety-tier registry: `apps/api/config/modules.registry.json` (`app/core/registry.py`): per module `tier`, `required_signoff_role`, `cosign`, `writes_allowed` (resource type -> statuses; `Encounter.location` -> `append`), `consent_required`. FhirGateway refuses writes of modules without an entry or outside `writes_allowed`; reads only need a purpose module. WP4b moves these fields into the agent registry.

## Free-text de-identification (WP4)

`app/llm/freetext_deid.py`: `FreeTextDeidentifier.learn_fhir(resources)` (or `add_person` / `add_known`) collects the record's identifiers; `detect(text)` finds the spans (dictionary and patterns), `apply` replaces them with typed tokens of the shared `Pseudonymizer`: `[PERSON_n]`, `[STAFF_n]`, `[DATE_n:D-2]` (days from the reference date), `[ADDRESS_n]`, `[PHONE_n]`, `[EMAIL_n]`, `[HEALTH_CARD_n]`, `[MRN_<keyed hash>]` for a known MRN (else `[MRN_n]`), `[ORG_n]`, `[ID_n]`; one token per person whatever the spelling; `restore()` re-identifies. `deidentify(text, deid)` adds the second pass (`deid_check@1` through the LLM gateway, structured findings; hits replaced and stored encrypted in `deid_misses`). Eval: `evals/deid/` (200 notes from `scripts/gen_deid_eval.py`, scored by `scripts/deid_eval.py`).

## Audit log (WP4)

`audit_events` (append-only, hash chain) gained `event_type` (`read`, `write`, `ai_call`, `sign`, `break_glass`, `export`, `consent_change`, `simulator_event`, plus `login`), `patient_mrn_hash` (keyed blind index of the MRN), `encounter_id`, `module`, `prompt_version` (ai_call). The spec's `timestamp`, `actor_id`, `actor_role` are the existing `ts`, `user_id`, `role`. Null new fields are left out of the digest, so events written before them verify unchanged. The LLM gateway writes an `ai_call` event per call (actor from the request, else `system`; module = the task). Admin: `GET /api/admin/audit` (filters outcome, event_type, module, mrn, user_id) and `GET /api/admin/audit/export` (CSV, itself an `export` event).

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
