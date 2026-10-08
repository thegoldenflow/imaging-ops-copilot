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
- Extensions are `urn:demo-hospital:ext:<name>`: `arrival-mode`, `ed-disposition`, `expected-los-days`, `asa-class`, `surgical-urgency`, `encounter` (on Appointment), `drug-class`, `synthetic`, `hcn-version`, `name-language`, `bed-capacity`, `unit-kind`.

## Ids from the generator

Generated ids are deterministic (same seed and hospital day → same ids), so eval cases can refer to them.

| Thing | Id |
| --- | --- |
| Patient | `pat-0001` … `pat-1000` |
| ED visit (Encounter EMER) | `ed-NNNNN`; CTAS `ctas-ed-NNNNN`, first vitals `vit-ed-NNNNN`, bed request `bedreq-ed-NNNNN` |
| Admission (Encounter IMP) | `stay-NNNNN` |
| Surgery | Appointment `surg-NNNNN`, Procedure `proc-surg-NNNNN`, request `orreq-surg-NNNNN`, day-surgery Encounter `amb-surg-NNNNN` |
| Orders | ServiceRequest `ord-<encounter>-NN`, lab result Observation `obs-ord-<encounter>-NN` |
| Practitioners | `prac-doc-NN`, `prac-nurs-NN`, `prac-phar-NN`, `prac-cler-NN`, `prac-ops-NN`, surgeons `prac-surg-NN`; roles `role-<suffix>` |

## The hospital clock and plan

The hospital day starts at 07:00 on the seed date (nearest weekday at a weekend). The state at that moment is in the FHIR store; the next 36 hours (ED arrivals and their steps, admissions, transfers, discharges, housekeeping, surgery start and end, orders and results) are in the `hospital_plan` module state for the day simulator (WP3). The hospital clock (`hospital_clock`) moves only when the simulator runs.

## Imaging exams (mapping, WP2)

The imaging module's booked exam (`Appointment` + `ImagingStudy` + `Report`) maps to ServiceRequest + Encounter (class AMB) + DiagnosticReport; the old tables stay and the adapter is a two-way function with a round-trip test.
