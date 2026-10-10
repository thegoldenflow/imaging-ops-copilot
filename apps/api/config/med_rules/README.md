# Medication rule tables (spec 7.2)

The deterministic rule layer of the order review and the medication reconciliation
(`app/modules/med_safety/rules.py`, `medrec.py`) reads these files. The spec's
`config/med_rules/` is `apps/api/config/med_rules/` here, next to the agent and tool
registries, and is copied into the API image.

| File | Rule | Content |
| --- | --- | --- |
| `renal_drugs.yaml` | renal dosing | 15 drugs to avoid or dose-adjust when the latest eGFR is below 30 mL/min/1.73 m², with the suggested dose |
| `interactions.yaml` | drug interaction | 25 pairs (ingredient or therapeutic class on each side), severity major or moderate, a one-line mechanism |
| `dose_bounds.yaml` | dose bounds | single, daily and weekly limits by age and weight for 15 drugs |
| `medrec.yaml` | duplicate test, reconciliation | the 72-hour duplicate-test window, the therapeutic classes that count as duplicate therapy, high-risk classes |

Every entry carries a `source` naming the public reference it paraphrases (mostly
FDA prescribing information, which is in the public domain). A test fails when an
entry has no source (`tests/test_med_rules.py`).

**These tables are demo simplifications, not clinical guidance.** They exist to show
the pipeline: deterministic rules first, AI commentary second, a pharmacist or
physician decides. They are short, incomplete and not maintained by a pharmacy
team. A production deployment replaces them with a commercial drug knowledge base
(for example First Databank (FDB) or Lexicomp) behind the same rule interface, with
its own update process and clinical governance.

Codes are RxNorm ingredients and the therapeutic classes of
`app/ehr/codes.py` (`MEDICATIONS`), the vocabulary the synthetic hospital uses.
