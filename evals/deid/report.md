# Free-text de-identification eval (spec 6.3)

Run 2026-10-09T16:51:00 on 200 synthetic notes with 2513 planted PHI spans (`evals/deid/cases.jsonl`). Second pass: mock provider (ok 200).

**PASSED**: thresholds recall >= 0.98, precision >= 0.9 (rule layer + second pass).

| | Recall | Precision | Caught / gold | Correct / redacted | Known identifiers | Pattern only | Date offsets |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Rule layer | 0.9889 | 0.9992 | 2485 / 2513 | 2485 / 2487 | 1.0 | 0.9816 | 0.9332 |
| Rule layer + second pass | 0.9932 | 0.9992 | 2496 / 2513 | 2496 / 2498 | 1.0 | 0.9889 | 0.9332 |

Recall by kind (pipeline):

| Kind | Gold | Caught | Recall |
| --- | --- | --- | --- |
| ADDRESS | 121 | 121 | 1.0 |
| DATE | 773 | 771 | 0.9974 |
| EMAIL | 52 | 52 | 1.0 |
| HEALTH_CARD | 150 | 150 | 1.0 |
| MRN | 218 | 218 | 1.0 |
| ORG | 258 | 258 | 1.0 |
| PERSON | 577 | 565 | 0.9792 |
| PHONE | 202 | 202 | 1.0 |
| STAFF | 162 | 159 | 0.9815 |

Leaks (pipeline, first 17):

- deid-003 DATE: `first of November`
- deid-006 STAFF: `okonjo`
- deid-013 PERSON: `Maria`
- deid-013 PERSON: `Peter`
- deid-023 PERSON: `Peter`
- deid-023 PERSON: `Maria`
- deid-071 PERSON: `Marcus`
- deid-071 PERSON: `Nadia`
- deid-090 STAFF: `albright`
- deid-106 PERSON: `Alana`
- deid-106 PERSON: `Jason`
- deid-107 PERSON: `Derek`
- deid-107 PERSON: `Sophie`
- deid-110 STAFF: `yamada`
- deid-143 DATE: `second of November`
- deid-181 PERSON: `Sophie`
- deid-181 PERSON: `Hannah`

Redactions without PHI (pipeline, first 2):

- deid-078 PERSON (known): `Young`
- deid-134 PERSON (known): `Price`

_Synthetic notes generated alongside the rules (scripts/gen_deid_eval.py); the metrics show the pipeline works, not performance on real clinical text. With the mock provider the second pass is a heuristic stand-in, not a model._
