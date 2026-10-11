# Storyline report (running API http://127.0.0.1:9445)

Run 2026-10-10T23:40:53 on Windows-10-10.0.19045-SP0, git a454d12-dirty; LLM provider the API's; Temporal off.

Result: **passed**: 12 of 12 steps; walk 389.2 s (reset 44.3 s); the demo script budgets 595 s of talking and clicking.

| Step | Who | Seconds | Result | Records |
| --- | --- | --- | --- | --- |
| 1. A daughter calls in Mandarin; the clerk confirms the ED registration | U-CLER-01 | 12.16 | ok: call VC-00076 (daughter), registration Task task-03211 | Encounter/ed-j0001 |
| 2. Triage: CTAS 2 suggested, admission probability 0.82 with three reasons | U-NURS-01 | 10.76 | ok: CTAS 2 suggested; Admission probability 0.82: CTAS 2, SpO₂ 91%, heart rate 112 | Observation/ctas-ed-j0001, Flag/flag-ed-j0001-fall-risk |
| 3. Hip X-rays and CT: urgency and protocol suggested, the ED physician orders | U-DOC-01 | 7.89 | ok: Imaging orders: X-ray and CT of the left hip (urgency P2) | ServiceRequest/ord-ed-j0001-xr-hip, ServiceRequest/ord-ed-j0001-ct-hip |
| 4. INR 3.8: a critical value routed to the ED physician | U-DOC-01 | 9.11 | ok: Critical value: INR 3.8 before surgery (roadmap stub) | Observation/obs-ord-ed-j0001-inr |
| 5. Orthopedics is full; Surgery has 2 discharge-ready patients; the bed manager approves | U-OPS | 21.72 | ok: Orthopedics at 100% occupancy (EXC-00512) approved: overflow_discharge_rounds, confirm_discharges | Encounter/stay-j0001, Location/SURG-10-B |
| 6. Admission MedRec: warfarin + naproxen (major), amlodipine left out | U-PHAR-01 | 11.3 | ok: 2 omission(s) incl. amlodipine; MedRec MR-02634 final | DocumentReference/medrec-stay-j0001-admission |
| 7. OR: predicted 115 min against 90 booked, past the block; the coordinator adjusts | U-OPS | 4.17 | ok: booked 90 -> 115 min (predicted 115) | Appointment/appt-surg-j0001-book-1 |
| 8. First night after surgery: NEWS2 7 raises a flag | U-NURS-13 | 51.86 | ok: NEWS2 7 at 02:00: assess now (roadmap stub) | Flag/flag-00009 |
| 9. Discharge summary: the validator blocks an untraceable medicine | U-SURG-02 | 95.16 | ok: blocked ['condition/cond-j0001-htn-note', 'lisinopril']; removed and signed | DocumentReference/dsum-stay-j0001 |
| 10. Bilingual instructions: warfarin diet and wound red flags | U-NURS-13 | 7.09 | ok: pinstr-stay-j0001 (zh-Hans) signed | DocumentReference/pinstr-stay-j0001 |
| 11. 48 hours later: a Chinese follow-up call escalates heavy wound drainage | U-NURS-13 | 146.73 | ok: FU-stay-j0001: escalated (urgent), call-back recorded | Communication/communicatio-00080, Task/task-03398 |
| 12. Coding suggestion: main diagnosis and procedure codes | U-CLER-01 | 9.52 | ok: Coding suggestion: S72.0 and 1.VA.53 (roadmap stub) | Task/task-03363 |

Seconds are the walk's (API calls, waits for the background work and the clock's fast-forwards), not the presenter's; the budget per step is in app/modules/storyline/script.py and demo/hospital_journey.md.
