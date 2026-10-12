# Narrator: human accuracy rating

The spec (7.1) asks for a person to rate each narrative's accuracy from 1 to 5 against the exception's facts (gate: mean >= 4). Fill in the Score column; the facts are in narrator/cases.jsonl.

| Exception | Rule | Severity | Narrative | Score (1-5) |
| --- | --- | --- | --- | --- |
| EXC-00211 | or_overrun | high | Total replacement of hip in OR-04 is predicted to take 147 minutes against 105 booked, 42 minutes over. The room's list is predicted to end at 18:40. | |
| EXC-00212 | or_overrun | high | Hysterectomy in OR-02 is predicted to take 173 minutes against 120 booked, 53 minutes over. The room's list is predicted to end at 18:14. | |
| EXC-00213 | or_overrun | high | Total replacement of hip in OR-04 is predicted to take 222 minutes against 105 booked, 117 minutes over. The room's list is predicted to end at 18:40. | |
| EXC-00214 | or_overrun | high | Spinal fusion in OR-04 is predicted to take 243 minutes against 195 booked, 48 minutes over. The room's list is predicted to end at 18:40. | |
| EXC-00215 | or_overrun | high | Hysterectomy in OR-02 is predicted to take 182 minutes against 120 booked, 62 minutes over. The room's list is predicted to end at 18:14. | |
| EXC-00216 | or_overrun | high | Hysterectomy in OR-02 is predicted to take 199 minutes against 120 booked, 79 minutes over. The room's list is predicted to end at 18:14. | |
| EXC-00217 | or_overrun | high | Excision of appendix in OR-04 is predicted to take 93 minutes against 60 booked, 33 minutes over. The room's list is predicted to end at 18:40. | |
| EXC-00218 | preop_gap | high | Total replacement of hip in OR-04 is booked for 07:28, 28 minutes from now, with 1 pre-op items still open. The case may start late unless they are completed. | |
| EXC-00219 | preop_gap | high | Total knee replacement in OR-01 is booked for 08:00, 60 minutes from now, with 1 pre-op items still open. The case may start late unless they are completed. | |
| EXC-00220 | unit_occupancy | med | Orthopedics is at 100% with 0 free beds: 1 admissions and 5 discharges are expected in the next 24 hours. Expected discharges cover the demand, but there is no slack for a surge. | |
| EXC-00221 | unit_occupancy | med | Surgery is at 100% with 0 free beds: 0 admissions and 5 discharges are expected in the next 24 hours. Expected discharges cover the demand, but there is no slack for a surge. | |
| EXC-00222 | preop_gap | low | Total replacement of hip in OR-04 is booked for 10:12, 192 minutes from now, with 1 pre-op items still open. The case may start late unless they are completed. | |
| EXC-00223 | preop_gap | low | Spinal fusion in OR-04 is booked for 10:15, 195 minutes from now, with 1 pre-op items still open. The case may start late unless they are completed. | |
| EXC-00224 | unit_occupancy | low | Medicine A is at 97% with 1 free beds: 0 admissions and 6 discharges are expected in the next 24 hours. Expected discharges cover the demand, but there is no slack for a surge. | |
| EXC-00225 | unit_occupancy | low | Medicine B is at 97% with 1 free beds: 0 admissions and 7 discharges are expected in the next 24 hours. Expected discharges cover the demand, but there is no slack for a surge. | |
| EXC-00226 | unit_occupancy | high | Intensive Care Unit is at 100% with 0 free beds: 5 admissions and 0 discharges are expected in the next 24 hours. That leaves the unit short of 5 beds unless patients leave earlier. | |
| EXC-00227 | ed_boarding | high | 5 admitted patients have waited in the ED for a bed for more than 120 minutes; the longest has waited 156 minutes. Their target units need beds freed or overflow opened. | |
| EXC-00228 | or_overrun | high | Total replacement of hip in OR-04 is predicted to take 186 minutes against 105 booked, 81 minutes over. The room's list is predicted to end at 01:21. | |
| EXC-00229 | preop_gap | high | Total replacement of hip in OR-04 is booked for 22:15, 75 minutes from now, with 1 pre-op items still open. The case may start late unless they are completed. | |
| EXC-00230 | or_overrun | high | Total replacement of hip in OR-04 is predicted to take 170 minutes against 105 booked, 65 minutes over. The room's list is predicted to end at 12:46. | |
| EXC-00231 | or_overrun | med | Excision of appendix in OR-04 is predicted to take 90 minutes against 60 booked, 30 minutes over. The room's list is predicted to end at 14:46. | |
| EXC-00232 | or_overrun | med | Excision of appendix in OR-04 is predicted to take 114 minutes against 60 booked, 54 minutes over. The room's list is predicted to end at 17:14. | |
| EXC-00233 | unit_occupancy | low | Medicine B is at 97% with 1 free beds: 2 admissions and 7 discharges are expected in the next 24 hours. Expected discharges cover the demand, but there is no slack for a surge. | |
| EXC-00234 | or_overrun | high | Total replacement of hip in OR-04 is predicted to take 147 minutes against 105 booked, 42 minutes over. The room's list is predicted to end at 18:40. | |
| EXC-00235 | or_overrun | high | Hysterectomy in OR-02 is predicted to take 173 minutes against 120 booked, 53 minutes over. The room's list is predicted to end at 18:14. | |
| EXC-00236 | or_overrun | high | Total replacement of hip in OR-04 is predicted to take 222 minutes against 105 booked, 117 minutes over. The room's list is predicted to end at 18:40. | |
| EXC-00237 | or_overrun | high | Spinal fusion in OR-04 is predicted to take 243 minutes against 195 booked, 48 minutes over. The room's list is predicted to end at 18:40. | |
| EXC-00238 | or_overrun | high | Hysterectomy in OR-02 is predicted to take 182 minutes against 120 booked, 62 minutes over. The room's list is predicted to end at 18:14. | |
| EXC-00239 | or_overrun | high | Hysterectomy in OR-02 is predicted to take 199 minutes against 120 booked, 79 minutes over. The room's list is predicted to end at 18:14. | |
| EXC-00240 | or_overrun | high | Excision of appendix in OR-04 is predicted to take 93 minutes against 60 booked, 33 minutes over. The room's list is predicted to end at 18:40. | |
