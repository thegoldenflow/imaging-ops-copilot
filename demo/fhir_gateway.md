# Demo: the FHIR gateway (WP2, 3 minutes)

What it shows: every module reads the hospital EHR through one gateway, with typed results and an audit record per call; the AI layer may only write work items and drafts; what a model sees is de-identified; the imaging center's exams map onto the same FHIR model and back without loss. There is no screen for this yet (the Control Tower in WP5 is the first); the demo runs in a terminal.

Setup: `docker compose up -d postgres` and the API's demo data (start the API once, or `uv run python -m app.seed`). For the HAPI part also `docker compose --profile ehr up -d hapi` and `uv run python scripts/load_fhir.py` (about 10 minutes for 1,000 patients).

## Script

1. `cd apps/api && uv run python scripts/gateway_demo.py`
   - Bed board for Medicine A: "One call gives the ops manager the unit: 32 beds, how many are occupied, who is in each bed since when. It comes from FHIR Location and Encounter resources, not a private table."
   - Typed reads for one patient: active stay, orders, medications, home medications, the latest heart rate from the vital-sign panel. "Modules ask the gateway, never the database or the FHIR server. A grep test fails the build if any module code talks to FHIR directly."

2. De-identified view.
   - "Before anything goes to a model, names become [PERSON_1], the health card [HEALTH_CARD_1], the birth date an age, and the MRN a keyed hash. Codes, references and clinical times stay, so the model can still reason over the stay. The token map never leaves the server, so the answer can be re-identified."

3. Writes.
   - The Task is created; the lab result is refused, and the last three audit lines show the read, the create and the refusal with the purpose module. "The AI layer sits beside the EHR. It may create tasks, flags, messages, draft documents and proposed appointments. It cannot write results or orders, cannot make a document final, cannot book. That is enforced in the gateway, not in the UI."

4. Imaging exam round trip.
   - "The imaging center's booked exam becomes a ServiceRequest, an ambulatory Encounter and a DiagnosticReport, and comes back unchanged. A test checks this on 20 exams of every kind; locally all 19,468 seeded exams pass."

5. Same answers from HAPI: `uv run python scripts/gateway_demo.py --backend hapi`
   - "Same code, same answers, now from a HAPI FHIR server with the 1,000 patients loaded. Pointing the gateway at a real EHR means changing the base URL and the authentication mode (SMART Backend Services is defined, not implemented in the demo)."
   - The demo's Task is written to HAPI; delete it afterwards if the server should stay equal to the loaded data.

## Expected output (local backend, abridged)

```text
== Bed board MEDA (local backend), as Jordan Lee ==
Medicine A: 32 beds, by status {'O': 29, 'U': 3}
...
== Writes: the allow-list ==
created Task/task-00001 (allowed: the AI layer writes work items)
refused Observation: Observation is not on the gateway's write allow-list
audit #...: Jordan Lee create Observation/None denied (gateway_demo: Observation is not on the gateway's write allow-list)
== An imaging exam as FHIR, and back ==
ServiceRequest/img-ord-AP-00001 + Encounter/img-enc-AP-00001
lossless round trip: True
```

Numbers depend on the day the demo data was generated (the hospital day starts at 07:00 on the seed date).
