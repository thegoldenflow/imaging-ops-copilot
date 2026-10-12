# Demo: the event bus and the day simulator (WP3, 3 minutes)

What it shows: a hospital is event driven, and its heartbeat is the ADT feed. The day simulator plays the EHR through a planned day: it writes each change into the FHIR store and sends the HL7 v2 message a real EHR would send. An adapter, standing in for the interface engine, turns each message into a domain event (`patient.admitted`, `result.available`, ...). Modules subscribe to those events only and read content through the FHIR gateway. Replacing the simulator with a real HL7 feed changes nothing behind the adapter. There is no screen yet (the Control Tower's control bar in WP5 drives the same API); the demo runs in a terminal.

Setup: `docker compose up -d postgres` and the API's demo data (start the API once, or `uv run python -m app.seed`). The script migrates the database, then works in one transaction that it rolls back at the end, so the demo hospital stays at 07:00 (`--commit` keeps the changes).

## Script

1. `cd apps/api && uv run python scripts/simulator_demo.py --scenario icu_surge --full-day`
   - Clock and census: "07:00 on the hospital day. The plan holds the next 36 hours: ED arrivals, admissions, transfers, discharges, OR cases, orders and results. The census line is read through the FHIR gateway: beds occupied per unit, dirty beds, ED patients waiting for a bed."
   - Advance one hour. "Each planned event becomes a FHIR change plus an HL7 message. The adapter maps ADT^A01 to `patient.admitted`, ORU^R01 to `result.available`, SIU to the OR events. The subscriber gets them in timeline order; its work, the delivery record and its position in the log commit together, and it deduplicates by event id, so a message the interface engine resends is handled once."
   - The ER7 of the first arrival: "This is what the interface engine sees: ids and codes only. The event on the bus carries references, never names or clinical text. A subscriber that needs the content asks the gateway, which audits the read."

2. Inject the ICU surge.
   - "Critically ill patients who need ICU arrive every three minutes, enough to fill ICU plus one. ICU has twelve beds and no overflow unit. When it is full, the simulator does what a hospital does: the last patients board in the ED with an open bed request, and planned ICU admissions wait too. That is the exception the Control Tower will flag in WP5." The census line goes to ICU 12/12 with ED boarders.
   - The scenario's events share one correlation id, and the actor is the user who injected them. "The audit log has the injection under the same id, so you can trace a scripted scenario end to end."

3. Fast-forward to 08:00 tomorrow.
   - "25 hospital hours in about ten seconds: some 1,600 events of eight or nine kinds, still in timeline order. The consistency check afterwards finds one patient per bed, and every bed's status matches its occupancy. Every resource the simulator wrote validates against our FHIR types."

4. Optional: the same in the API (operations manager or admin), e.g. from the API docs page `/docs`:
   - `GET /api/hospital/simulator` (clock, next events, scenarios), `POST /api/hospital/simulator/advance {"minutes": 60}`, `/fast-forward`, `/run {"rate": 60}` (one hospital hour per minute; the API's background loop ticks every second), `/pause`, `/inject {"scenario": "ed_surge"}`.
   - `GET /api/hospital/events` (the log) and `/events/consumers` (each subscriber's cursor, backlog, failing and parked deliveries).
   - As admin, `POST /api/hospital/hl7` with a raw ER7 message: the same adapter answers with an HL7 ACK (`AA`); a message it cannot map gets `AE` and is parked as a dead letter on the integrations page.

## Expected output (abridged, hospital day Fri 2026-10-09)

```text
== Hospital clock Fri 2026-10-09 07:00 (plan until Sat 19:00), 1741 planned events left ==
census: ED 2/30 bays + 1 waiting | MEDA 31/32 (dirty 0) | MEDB 31/32 (dirty 0) | SURG 32/32 (dirty 0) | ORTH 24/24 (dirty 0) | ICU 11/12 (dirty 0) | ED boarders 0
== Advance 1 hour(s): the simulator writes FHIR, the HL7 adapter publishes events ==
applied 39 plan events -> 39 domain events {'result.available': 16, 'order.placed': 15, 'appointment.updated': 4, 'patient.admitted': 2, 'encounter.updated': 2}
  Fri 07:00  ORU^R01  -> result.available       stay-01164   ord-stay-01164-04 category=laboratory, encounter_class=IMP
== What the interface engine received for the first arrival (ids and codes only) ==
MSH|^~\&|DEMO-EHR|DEMO-HOSPITAL|IOC|IOC|20261009073343||ADT^A01^ADT_A01|SIM06B3023EAA6275A4E|P|2.5.1
EVN|A01|20261009073343||||20261009073343
PID|1||pat-0651^^^DEMO-HOSPITAL^PI
PV1|1|E|ED^^^DEMO-HOSPITAL||||||||||||||||ed-03255^^^DEMO-HOSPITAL^VN
== Inject scenario icu_surge: ... ==
3 patients ed-x0001 .. ed-x0003, arriving 08:01-08:07; ...; ICU beds free now: 1
2.5 hours later: 30 events carry the scenario's correlation id, actor user:U-OPS; 6 admissions or transfers waited for a bed
census: ED 7/30 bays + 3 waiting | ... | ICU 12/12 (dirty 0) | ED boarders 2
== Fast-forward to 08:00 tomorrow ==
13.6 s: 1596 events {...}
order problems: none
census: ED 7/30 bays + 0 waiting | MEDA 30/32 (dirty 0) | MEDB 32/32 (dirty 0) | SURG 27/32 (dirty 0) | ORTH 22/24 (dirty 0) | ICU 12/12 (dirty 0) | ED boarders 3
consistency problems: none
```

Numbers depend on the day the demo data was generated (the hospital day starts at 07:00 on the seed date). The wards start nearly full and ICU at 11 of its 12 beds on this data (on a weekday morning the generated ICU holds about 8 patients), so the default surge, enough to fill ICU plus one but at least three, sends three patients and the last of them boards in the ED.

## Report

`uv run python scripts/simulator_demo.py --report` runs the planned day twice (as planned, and with the ICU surge), each in a rolled-back transaction, and writes `evals/simulator/day_run.json`: plan events applied, domain events and HL7 messages by type, admissions that had to wait for a bed, order and consistency problems (must be none), and invalid resources (must be none). There is no model in this work package, so this consistency check is its eval.
