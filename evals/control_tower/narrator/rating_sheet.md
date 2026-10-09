# Narrator: human accuracy rating

The spec (7.1) asks for a person to rate each narrative's accuracy from 1 to 5 against the exception's facts (gate: mean >= 4). Fill in the Score column; the facts are in narrator/cases.jsonl.

| Exception | Rule | Severity | Narrative | Score (1-5) |
| --- | --- | --- | --- | --- |
| EXC-00128 | or_overrun | high | Hysterectomy in OR-02 is predicted to take 173 minutes against 120 booked, 53 minutes over. The room's list is predicted to end at 18:06. | |
| EXC-00129 | or_overrun | high | Hysterectomy in OR-02 is predicted to take 180 minutes against 120 booked, 60 minutes over. The room's list is predicted to end at 18:06. | |
| EXC-00130 | or_overrun | high | Hysterectomy in OR-02 is predicted to take 194 minutes against 120 booked, 74 minutes over. The room's list is predicted to end at 18:06. | |
| EXC-00131 | preop_gap | high | Total knee replacement in OR-01 is booked for 08:00, 60 minutes from now, with 1 pre-op items still open. The case may start late unless they are completed. | |
| EXC-00132 | preop_gap | high | Total replacement of hip in OR-04 is booked for 08:00, 60 minutes from now, with 1 pre-op items still open. The case may start late unless they are completed. | |
| EXC-00133 | or_overrun | med | Spinal fusion in OR-04 is predicted to take 240 minutes against 195 booked, 45 minutes over. The room's list is predicted to end at 14:17. | |
| EXC-00134 | unit_occupancy | med | Orthopedics is at 100% with 0 free beds: 0 admissions and 5 discharges are expected in the next 24 hours. Expected discharges cover the demand, but there is no slack for a surge. | |
| EXC-00135 | preop_gap | low | Spinal fusion in OR-04 is booked for 10:15, 195 minutes from now, with 1 pre-op items still open. The case may start late unless they are completed. | |
| EXC-00136 | unit_occupancy | low | Medicine B is at 97% with 1 free beds: 0 admissions and 6 discharges are expected in the next 24 hours. Expected discharges cover the demand, but there is no slack for a surge. | |
| EXC-00137 | unit_occupancy | high | Intensive Care Unit is at 100% with 0 free beds: 3 admissions and 0 discharges are expected in the next 24 hours. That leaves the unit short of 3 beds unless patients leave earlier. | |
| EXC-00138 | ed_boarding | med | 3 admitted patients have waited in the ED for a bed for more than 120 minutes; the longest has waited 129 minutes. Their target units need beds freed or overflow opened. | |
| EXC-00139 | or_overrun | high | Total replacement of hip in OR-04 is predicted to take 187 minutes against 105 booked, 82 minutes over. The room's list is predicted to end at 14:15. | |
| EXC-00140 | unit_occupancy | med | Orthopedics is at 96% with 0 free beds: 1 admissions and 3 discharges are expected in the next 24 hours. Expected discharges cover the demand, but there is no slack for a surge. | |
| EXC-00141 | preop_gap | low | Total replacement of hip in OR-04 is booked for 17:55, 235 minutes from now, with 1 pre-op items still open. The case may start late unless they are completed. | |
| EXC-00142 | preop_gap | low | Excision of appendix in OR-04 is booked for 20:06, 186 minutes from now, with 1 pre-op items still open. The case may start late unless they are completed. | |
| EXC-00143 | preop_gap | high | Total replacement of hip in OR-04 is booked for 21:45, 45 minutes from now, with 1 pre-op items still open. The case may start late unless they are completed. | |
| EXC-00144 | unit_occupancy | high | Intensive Care Unit is at 100% with 0 free beds: 2 admissions and 0 discharges are expected in the next 24 hours. That leaves the unit short of 2 beds unless patients leave earlier. | |
| EXC-00145 | preop_gap | med | Total replacement of hip in OR-04 is booked for 23:57, 177 minutes from now, with 2 pre-op items still open. The case may start late unless they are completed. | |
| EXC-00128 | or_overrun | high | Hysterectomy in OR-02 is predicted to take 173 minutes against 120 booked, 53 minutes over. The room's list is predicted to end at 18:06. | |
| EXC-00129 | or_overrun | high | Hysterectomy in OR-02 is predicted to take 180 minutes against 120 booked, 60 minutes over. The room's list is predicted to end at 18:06. | |
| EXC-00130 | or_overrun | high | Hysterectomy in OR-02 is predicted to take 194 minutes against 120 booked, 74 minutes over. The room's list is predicted to end at 18:06. | |
| EXC-00131 | preop_gap | high | Total knee replacement in OR-01 is booked for 08:00, 60 minutes from now, with 1 pre-op items still open. The case may start late unless they are completed. | |
| EXC-00132 | preop_gap | high | Total replacement of hip in OR-04 is booked for 08:00, 60 minutes from now, with 1 pre-op items still open. The case may start late unless they are completed. | |
| EXC-00133 | or_overrun | med | Spinal fusion in OR-04 is predicted to take 240 minutes against 195 booked, 45 minutes over. The room's list is predicted to end at 14:17. | |
| EXC-00134 | unit_occupancy | med | Orthopedics is at 100% with 0 free beds: 0 admissions and 5 discharges are expected in the next 24 hours. Expected discharges cover the demand, but there is no slack for a surge. | |
| EXC-00135 | preop_gap | low | Spinal fusion in OR-04 is booked for 10:15, 195 minutes from now, with 1 pre-op items still open. The case may start late unless they are completed. | |
| EXC-00136 | unit_occupancy | low | Medicine B is at 97% with 1 free beds: 0 admissions and 6 discharges are expected in the next 24 hours. Expected discharges cover the demand, but there is no slack for a surge. | |
| EXC-00146 | unit_occupancy | high | Intensive Care Unit is at 100% with 0 free beds: 4 admissions and 0 discharges are expected in the next 24 hours. That leaves the unit short of 4 beds unless patients leave earlier. | |
| EXC-00147 | ed_boarding | med | 3 admitted patients have waited in the ED for a bed for more than 120 minutes; the longest has waited 144 minutes. Their target units need beds freed or overflow opened. | |
| EXC-00148 | or_overrun | high | Coronary artery bypass grafting in OR-03 is predicted to take 310 minutes against 240 booked, 70 minutes over. The room's list is predicted to end at 13:10. | |
