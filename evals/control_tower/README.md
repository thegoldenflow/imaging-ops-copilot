# Control Tower evals (spec 7.1)

Two evals for the hospital flow Control Tower: the rule engine that finds exceptions, and the narrator agent
(`control_tower`) that explains them and recommends actions. Runner: `apps/api/scripts/control_tower_eval.py`
(`--rules` for the engine only, `--live` for the configured model, `--check` to fail below a gate). Results:
`report.json`, `report.md`. All data is synthetic.

## Input resources

- Rule engine: `rules/cases.jsonl`, 20 scripted scenarios. Each describes a hospital in a few lines (unit
  occupancy and demand, ED boarders and their waits, OR cases with booked and predicted minutes, open pre-op
  items) and the exceptions expected with their severity. `app/modules/control_tower/evals.py`
  (`scenario_snapshot`) turns a scenario into the boards the engine reads; the OR schedule goes through the same
  code as the live snapshot. The scenarios sit on every threshold edge (94% / 95% occupancy, 2 / 3 boarders,
  exactly 120 minutes, 29 / 30 minutes over, 4 hours to the start, a started case, cancelled and finished cases).
- Narrator: 30 exceptions from the seeded hospital, collected while the day simulator plays two scripted walks
  (ICU surges of 6 and 7, ED surges, OR overrun, hours of the planned day) inside rolled-back transactions. Their
  facts, menus and FHIR references are in `narrator/cases.jsonl`. The agent reads the unit's bed board and up to
  three of the named encounters through its read tools (de-identified by the runtime).

## Output schema

The narrator's output (validated by the LLM gateway, then by the guards):
`{exception_id, severity: low|med|high, narrative, recommended_actions: [{action_id, action, rationale,
owner_role: physician|nurse|operations_manager, expected_effect}] (1-3), evidence_refs: ["Type/id"]}`. The spec's
fields plus `action_id`, the engine's menu entry each action comes from (its wording and owner role are the
engine's). It goes into the review queue of the bed manager and the charge nurses as an `ai-review` Task
(`explainException`); approving creates one `flow-action` Task per action for its owner role (`createFlowTask`).

## Evaluation and thresholds

| Eval | Metric | Gate |
| --- | --- | --- |
| Rule engine | expected exceptions and severities match, per scenario | 20 / 20 |
| Narrator | schema validity | 100% of 30 |
| Narrator | grounding rate: every `evidence_refs` entry resolves in FHIR (checked through `FhirGateway.resolve_refs` before display) | 100% of 30 |
| Narrator | accepted by the guards (actions from the menu with its owner role, only the engine's numbers, no claim that anything was done, the engine's severity, evidence from the engine) | reported; a refusal falls back to the engine's template after one regeneration |
| Narrator | human rating of narrative accuracy, 1-5 | mean >= 4 (spec); `narrator/rating_sheet.md` is ready for a reviewer, **not done yet** |

The backend suite runs the rule eval and the narrator's guard, fallback and evidence checks
(`tests/test_control_tower.py`). With the mock model (no API key) the narrator's narrative is written from the
facts in the prompt, so 100% there shows the pipeline holds (context, schema, guards, evidence check, review
Task), not a model's quality; `--live` runs the configured model through the same path.

## Known failure modes

- The rule thresholds are the spec's examples, not a hospital's policy; severity tiers are this project's choice
  (documented in `app/modules/control_tower/rules.py`).
- A live model may write numbers that are true but not in the facts (a sum, a percentage of a count); the guard
  refuses them, and after one regeneration the engine's template is shown instead. That is safe but loses the
  model's wording; the template rate is in the report.
- The guard's check for claims of execution is a list of phrasings (English and Chinese); an unusual phrasing
  could pass it. Nothing is executed either way: only an approval creates Tasks.
- The narrator eval needs the seeded hospital in DATABASE_URL; the exceptions differ with the seeding weekday
  (the hospital day is the seeding day), so `cases.jsonl` is a record of the last run, not a fixed set.
- Human rating is pending: `eval_status` of `control_tower` stays `pending` until a reviewer fills the sheet, so
  its output carries the "Not evaluated" flag in demo mode and the agent is refused in prod mode (the rule
  engine, the boards and the drawer keep working without it).
