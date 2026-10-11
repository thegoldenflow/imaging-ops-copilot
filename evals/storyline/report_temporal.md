# Storyline report (running API http://127.0.0.1:9445)

Run 2026-10-11T01:36:53 on Windows-10-10.0.19045-SP0, git a454d12-dirty; LLM provider the API's; Temporal on.

Result: **passed**: 12 of 12 steps; walk 399.2 s (reset 61.8 s); the demo script budgets 595 s of talking and clicking.

| Step | Who | Seconds | Result | Records |
| --- | --- | --- | --- | --- |
| 1. A daughter calls in Mandarin; the clerk confirms the ED registration | U-CLER-01 | 24.84 | ok: call VC-00083 (daughter), registration Task task-04835 | Encounter/ed-j0001 |
| 2. Triage: CTAS 2 suggested, admission probability 0.82 with three reasons | U-NURS-01 | 24.45 | ok: CTAS 2 suggested; Admission probability 0.82: CTAS 2, SpO₂ 91%, heart rate 112 | Observation/ctas-ed-j0001, Flag/flag-ed-j0001-fall-risk |
| 3. Hip X-rays and CT: urgency and protocol suggested, the ED physician orders | U-DOC-01 | 10.81 | ok: Imaging orders: X-ray and CT of the left hip (urgency P2) | ServiceRequest/ord-ed-j0001-xr-hip, ServiceRequest/ord-ed-j0001-ct-hip |
| 4. INR 3.8: a critical value routed to the ED physician | U-DOC-01 | 12.73 | ok: Critical value: INR 3.8 before surgery (roadmap stub) | Observation/obs-ord-ed-j0001-inr |
| 5. Orthopedics is full; Surgery has 2 discharge-ready patients; the bed manager approves | U-OPS | 26.28 | ok: Orthopedics at 100% occupancy (EXC-00667) approved: overflow_discharge_rounds, confirm_discharges | Encounter/stay-j0001, Location/SURG-04-B |
| 6. Admission MedRec: warfarin + naproxen (major), amlodipine left out | U-PHAR-01 | 9.86 | ok: 2 omission(s) incl. amlodipine; MedRec MR-03368 final | DocumentReference/medrec-stay-j0001-admission |
| 7. OR: predicted 115 min against 90 booked, past the block; the coordinator adjusts | U-OPS | 6.78 | ok: booked 90 -> 115 min (predicted 115) | Appointment/appt-surg-j0001-book-1 |
| 8. First night after surgery: NEWS2 7 raises a flag | U-NURS-13 | 56.36 | ok: NEWS2 alert on this patient: assess now and record the response (score and observations in the flag). | Flag/flag-00016 |
| 9. Discharge summary: the validator blocks an untraceable medicine | U-SURG-02 | 141.09 | ok: blocked ['condition/cond-j0001-htn-note', 'lisinopril']; removed and signed | DocumentReference/dsum-stay-j0001 |
| 10. Bilingual instructions: warfarin diet and wound red flags | U-NURS-13 | 4.89 | ok: pinstr-stay-j0001 (zh-Hans) signed | DocumentReference/pinstr-stay-j0001 |
| 11. 48 hours later: a Chinese follow-up call escalates heavy wound drainage | U-NURS-13 | 76.48 | ok: FU-stay-j0001: escalated (urgent), call-back recorded | Communication/communicatio-00101, Task/task-05074 |
| 12. Coding suggestion: main diagnosis and procedure codes | U-CLER-01 | 3.42 | ok: Coding suggestion: S72.0 and 1.VA.53 (roadmap stub) | Task/task-05016 |

Seconds are the walk's (API calls, waits for the background work and the clock's fast-forwards), not the presenter's; the budget per step is in app/modules/storyline/script.py and demo/hospital_journey.md.
