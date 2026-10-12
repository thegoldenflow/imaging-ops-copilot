"""Durable workflows (spec 6.5): the patient journey, the capacity exception loop and the low-confidence review loop.

Temporal runs the workflow code (`journey.py`, `capacity.py`, `review.py`, all sandbox-safe) in a worker
(`python -m app.workflows.worker`); activities (`activities.py`) do the work, every side effect through the Tool
Gateway. `bridge.py` turns domain events into workflow starts and signals, `progress.py` keeps the timeline the
workflow view shows, `router.py` is the API. Without TEMPORAL_ADDRESS none of it runs and every module works as before.

Keep this file free of imports: the workflow sandbox imports the package.
"""
