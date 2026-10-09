# Demo: access, break-glass, consent, de-identification and audit (WP4, 6 minutes)

What it shows: the hospital platform layer of spec 6.3. Every read and write of the synthetic hospital EHR goes through the FHIR gateway, which decides by role and unit, not the screen: a Medicine A physician sees Medicine A, the bed manager sees MRNs and beds, the clerk sees demographics without clinical content, the administrator sees no patients at all. A clinician who needs someone else's patient breaks the glass with a reason; it is audited and reviewed. Drafts become final only through the signing service, and only modules in the safety-tier registry can write (since WP4b the agent registry, `apps/api/config/agents`; see demo/agent_runtime.md). Free text is de-identified before any model sees it, and every action lands in the hash-chained audit log.

Setup: `docker compose up -d postgres`, start the API and the web app (or the Playwright setup: API on 9001 with `LLM_PROVIDER=mock`). After upgrading from WP3, press **Reset demo** once: the hospital staff logins and the draft documents are part of the demo data. Logins used: Dr. Yara Lindqvist (physician, Medicine A), Jonah Eriksen (nurse, Medicine A), Rosa Pemberton (pharmacist), Maya Nakamura (registration clerk), Jordan Lee (operations manager), Casey Brooks (administrator). Names and MRNs below are from the demo data generated on 2026-10-08; another seed date gives other ones.

## Script (web app)

1. **Sign in as the physician** (login card "Physician"). The menu shows a Hospital section with one page, Patients; none of the imaging-centre pages.
   - Patients: "My unit is Medicine A, and the census is Medicine A: bed, MRN, name, since when. That list comes from the FHIR gateway, which knows my units from my PractitionerRole in the EHR." Open a patient: visit, consent, medication orders, latest observations, documents.
   - On the discharge summary draft: "Signed by physician (registry: discharge_summary)". Press **Sign**: the draft becomes Final. "Nothing becomes part of the record without a human signature, and the gateway enforces it: the signing service is the only path from preliminary to final."

2. **Break-glass.** Type the MRN of an ICU patient into "Open a patient by MRN" (switch to the operations manager for a moment: the ICU census lists MRNs without names; or use `40000545` on the 2026-10-08 data).
   - The record is closed: "MRN … is outside your units". "That came from the server, as a 403 with a reason code. The UI did not decide anything."
   - **Emergency access (break-glass)**: the dialog wants a reason; "ICU call" is too short (the counter says at least 10 characters). Type "Rapid response call on the ICU, covering for the attending" and open the record for 4 hours.
   - The chart opens with a "Break-glass access until …" badge, and a red banner now sits on top of every page with the MRN and the time left. "Every read I make now carries the grant id in the audit log."

3. **The pharmacist** (switch role): Patients shows any unit; open the Medicine A patient with the medication reconciliation draft. Only medication orders, lab results and medication documents are there; no notes, no vital signs. **Co-sign**: it stays Draft, "waiting for physician". "Med rec is co-signed by pharmacist and physician; the registry says so, the signing service counts."

4. **The clerk**: demographics and visits without the reason for the visit. In Consent, **Record withdrawal** of SMS consent: "This is audited as consent_change and published on the event bus as consent.revoked, so the messaging module stops sending. Without consent nothing breaks: no SMS goes out and the desk gets a task to call instead; without follow-up call consent a nurse follows up by hand; without AI processing consent documentation offers the template only."

5. **The operations manager**: open any patient. "An individual patient is an MRN and a bed. Aggregates for the whole hospital, clinical content for nobody in this role."

6. **The administrator**: Break-glass review lists the access: who, which patient, the reason, how many records were read, and the review deadline (24 hours). Add a note and mark it **Justified**. Then Audit log → Break-glass: the grant and the review, both in the hash chain ("Chain intact"). Type the ICU patient's MRN into the patient filter: every event for that patient, matched by its keyed hash (the log stores no MRN). **Export CSV**: the export itself is an `export` event.

## Script (terminal, 1 minute)

`cd apps/api && uv run python scripts/platform_demo.py` (rolled back at the end) prints the role matrix, the break-glass round trip, the signing service, a registry refusal, the three consent degradations and a consent event, the free-text de-identification of a nursing note, and the audit events of the run:

```text
== Who sees what (FhirGateway decides, not the UI): MEDA patient pat-0388, ICU patient pat-0545 ==
                     MEDA patient        ICU patient         MEDA vitals         MEDA med orders     ICU bed board
  physician          ok                  refused (break-glass)ok                  ok                  refused
  nurse              ok                  refused (break-glass)ok                  ok                  refused
  pharmacist         ok                  ok                  ok                  ok                  ok
  clerk              ok                  ok                  refused             refused             ok
  operations_manager ok                  ok                  refused             refused             ok
  admin              refused             refused             refused             refused             refused
...
== Free-text de-identification (rule layer + second pass through the LLM gateway, mock provider) ==
  in:  Nursing note. Mrs. Prescott (MRN 40000388) seen by Dr. Yara Lindqvist on Oct 6, 2026; follow-up 10/20. Daughter Emily Santos at (416) 555-0123 ext. 234, ...
  out: Nursing note. Mrs. [PERSON_1] (MRN [MRN_86016769b7]) seen by [STAFF_1] on [DATE_1:D-2]; follow-up [DATE_2:D+12]. Daughter [PERSON_3] at [PHONE_1], ...
  second pass: ok; caught after the rules: ['PERSON']
```

("MEDA vitals" for the pharmacist reads only the laboratory results among the observations.) The de-identified note keeps the timeline as days from the note date (`D-2`), and the second pass caught "Hannah Novak", a name with no cue the rules could use; it is stored, encrypted, as a rule miss.

## Eval

`uv run python scripts/deid_eval.py --check` scores the de-identification on 200 synthetic notes (`evals/deid/report.md`): rule layer recall 0.9887 / precision 0.9992, with the second pass (mock provider) 0.9932 / 0.9992, against the gate of 0.98 / 0.90. "The notes are synthetic and were written alongside the rules, so this proves the pipeline, not performance on real notes. The misses that remain are the honest failure modes: given names with no cue ('Peter and Maria visited'), a lower-case 'dr. yamada', dates in words."
