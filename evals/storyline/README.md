# Storyline report (WP9, spec 8.2)

The hospital storyline walked from start to finish: one synthetic patient, twelve steps. Each step is "AI proposes → a person signs → audit trail". There is no model of its own to judge, so the report shows that the story runs end to end. For each step it records what was proposed, who decided, which EHR records were written and how long the step took.

- Runner: `apps/api/scripts/storyline_report.py`. It drives the same walk as the backend test (`app/modules/storyline/walk.py`), through the HTTP API, signed in as each step's person.
- `report_offline.json` and `.md` come from a running API without Temporal: the story walked step by step.
- `report_temporal.json` and `.md` come from a running API with a Temporal server and a worker: her InpatientJourneyWorkflow (`journey-stay-j0001`) strings steps 2–12 together.
- `--in-process` writes `report_in_process.*`: the walk in one process, rolled back. Not kept here; the backend test `tests/test_storyline.py` runs the same walk.
- Threshold: all twelve steps pass, with Temporal on and with it off.

The seconds are the walk's own: API calls, waits for the API's background loops, and the clock's fast-forwards. They are not the presenter's. The talking and clicking budget is in `demo/hospital_journey.md` (under 10 minutes). The runs here were made on the demo database with the mock model, while the machine was shared with other sessions; the `git` field reads `-dirty` when the run was made before the commit.
