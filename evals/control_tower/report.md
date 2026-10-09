# Control Tower evals

Run 2026-10-09T15:49:54. Synthetic data only.

## Rule engine

20 of 20 scripted scenarios match their expected exceptions and severities (passed).

| Case | Scenario | Expected | Got | Passed |
| --- | --- | --- | --- | --- |
| R01 | Medicine A at 94%: below the threshold, no exception | none | none | yes |
| R02 | Medicine A at 97%, expected discharges cover the demand: low | unit_occupancy:MEDA (low) | unit_occupancy:MEDA (low) | yes |
| R03 | Medicine A full with no ED demand: med (no free bed) | unit_occupancy:MEDA (med) | unit_occupancy:MEDA (med) | yes |
| R04 | Medicine B at 97% and short of one bed: med | unit_occupancy:MEDB (med) | unit_occupancy:MEDB (med) | yes |
| R05 | Orthopedics full while an ED patient waits for it: high | unit_occupancy:ORTH (high) | unit_occupancy:ORTH (high) | yes |
| R06 | Surgery at 97% and short of three beds: high | unit_occupancy:SURG (high) | unit_occupancy:SURG (high) | yes |
| R07 | ICU full and short of two beds (the ICU surge): high | unit_occupancy:ICU (high) | unit_occupancy:ICU (high) | yes |
| R08 | Two units over 95% at different severities, a third below | unit_occupancy:MEDA (med), unit_occupancy:ORTH (low) | unit_occupancy:MEDA (med), unit_occupancy:ORTH (low) | yes |
| R09 | Five boarders but only two over 2 hours: no exception | none | none | yes |
| R10 | Three boarders over 2 hours: med | ed_boarding:ED (med) | ed_boarding:ED (med) | yes |
| R11 | Three boarders at exactly 2 hours: not more than 2 hours, no exception | none | none | yes |
| R12 | Five boarders over 2 hours: high | ed_boarding:ED (high) | ed_boarding:ED (high) | yes |
| R13 | Three boarders over 2 hours, one for more than 4 hours: high | ed_boarding:ED (high) | ed_boarding:ED (high) | yes |
| R14 | OR case predicted 29 minutes over: below the threshold | none | none | yes |
| R15 | OR case predicted 30 minutes over: med | or_overrun:c15 (med) | or_overrun:c15 (med) | yes |
| R16 | OR case 35 minutes over pushes the room's list 35 minutes past the block: high; the next case on time otherwise | or_overrun:c16a (high) | or_overrun:c16a (high) | yes |
| R17 | A running case 140 minutes in, booked for 90: high | or_overrun:c17 (high) | or_overrun:c17 (high) | yes |
| R18 | Pre-op gaps: one item open 3 h before (low), consent open 3.5 h before (high); a case 65 minutes over (high) | or_overrun:c18c (high), preop_gap:c18a (low), preop_gap:c18b (high) | or_overrun:c18c (high), preop_gap:c18a (low), preop_gap:c18b (high) | yes |
| R19 | Two items open 3 h before (med); one open 5 h before (no exception); a started case keeps its open items quiet | preop_gap:c19a (med) | preop_gap:c19a (med) | yes |
| R20 | A busy morning: full ward short of beds, boarders, a pre-op gap 90 minutes out, an OR overrun; cancelled and finished cases ignored | ed_boarding:ED (med), or_overrun:c20b (med), preop_gap:c20a (high), unit_occupancy:MEDA (high) | ed_boarding:ED (med), or_overrun:c20b (med), preop_gap:c20a (high), unit_occupancy:MEDA (high) | yes |

## Narrator

Model: mock. 30 exceptions from the seeded hospital while the simulator played the ICU surge, the ED surge and the OR overrun (ed_boarding 1, or_overrun 18, preop_gap 5, unit_occupancy 6).

| Metric | Result | Gate |
| --- | --- | --- |
| Schema validity | 100% | 100% |
| Evidence references resolvable in FHIR (grounding rate) | 100% | 100% |
| Accepted by the guards (no fallback to the engine's template) | 100% | - |
| Human rating of accuracy | pending: score each narrative 1-5 for accuracy in narrator/rating_sheet.md (gate 4/5) | mean >= 4/5 |

Gate (schema and grounding on at least 30 exceptions): passed.

The mock model writes its narrative from the facts in the prompt, so these results show that the pipeline (context, schema, guards, evidence check, review Task) holds; run `--live` for a real model's numbers.
