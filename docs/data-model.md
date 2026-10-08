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
| DocumentReference | Discharge summary, patient instructions, handoff, medication reconciliation | type, docStatus (preliminary / final), author, authenticator, content | 7.2, 7.3 | 7.2, 7.3 |
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
| (none) | `consent.revoked` | scripted scenario (WP4 consent management later) | patient, consent; `category` |

Every event has `event_id` (stable per source message, so a resent HL7 message is the same event), `correlation_id`, `actor` (`system:demo-ehr`, or `user:<id>` when a person triggered it, e.g. a scripted scenario) and `occurred_at` (hospital time). Events carry references and codes only: no names, no MRN, no clinical text.

Storage (migration 0004, emptied by a demo reset): `domain_events` (the outbox; written in the same transaction as the FHIR changes), `event_consumers` (each subscriber's cursor), `event_deliveries` (per subscriber and `event_id`: done, failed, parked; the deduplication record).

The plan is the intent and the FHIR store is the truth: a planned bed that is taken (a scenario's patients, a bed manager's move) means another free bed in the unit or its overflow units, and if there is none the patient keeps boarding in the ED (bed request active) and the stay's events move 30 minutes later. A bed left for housekeeping without a planned cleaning gets one. The simulator writes the local FHIR store only; with `FHIR_BACKEND=hapi` the simulated day does not reach the HAPI server.

## Access: FhirGateway (WP2)

`app/ehr/gateway.py`. Modules never use the FHIR store or HTTP directly; two grep tests in `tests/test_fhir_gateway.py` enforce it. A gateway is opened for an actor (`Actor.of(user, request)`, later agents and system jobs) and a purpose module.

- Typed reads (`app/fhir/types` models): `get_patient(mrn)`, `search_encounters(mrn=, patient_id=, cls=, status=, unit=, active_at=, since=, until=)` (newest first), `get_active_encounter(mrn)`, `get_bed_board(unit)` (beds with status, occupant encounter and since when, plus encounters waiting at the unit without a bed), `get_orders(encounter)` (ServiceRequest), `get_medications(encounter)` (MedicationRequest), `get_home_meds(mrn)` (active MedicationStatement), `get_observations(encounter, codes, since)` (a code also matches a vital-panel component), `get_documents(encounter)`, `read(type, id)`.
- Write allow-list: Task (create, update, any status); DocumentReference (create as `docStatus=preliminary`, change only while preliminary; final goes through the signing service, WP4); Communication and Flag (create, update); Appointment (create as `status=proposed`, change only while proposed; booking needs a clerk, WP4b); `append_encounter_location(encounter, bed)` for bed managers (`operations_manager`): closes the current location entry, appends the bed, and, as the EHR's transfer does, marks the new bed occupied and the old one for housekeeping. Everything else, including any delete, raises `FhirAccessDenied` and leaves a `denied` audit record.
- Audit: one record per call (actor, action, resource type, resource id or the scope: patient, encounter or unit) with the purpose module in `reason` until WP4 adds a module column.
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

Codes, ids, references and clinical times stay. The free-text layer for partial names, mixed date formats and organisations, and the second model pass, are WP4 (6.3).

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
