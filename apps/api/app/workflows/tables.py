"""The bridge's tables (spec 6.5); truncated by a demo reset (app/core/db/schema.py FIXED_DATA_TABLES).

- `workflow_commands`: the outbox between the event bus and Temporal. The bridge's subscriber inserts a start or
  signal command in the event drain's transaction (insert-first: a command id per event and target, so a
  redelivered event adds nothing); the dispatcher sends pending commands to Temporal in order and records the
  result. Payloads hold references and ids only.
- `workflow_waits`: what a workflow waits for (a Task to be decided, a document to be signed), by FHIR reference,
  so the bridge signals exactly that workflow when the event comes.
"""

from __future__ import annotations

from sqlalchemy import BigInteger, Column, DateTime, Identity, Index, Integer, Table, Text
from sqlalchemy.dialects.postgresql import JSONB

from app.core.db.schema import metadata

workflow_commands = Table(
    "workflow_commands", metadata,
    Column("seq", BigInteger, Identity(), primary_key=True),
    Column("id", Text, nullable=False, unique=True),  # deduplication key: <event id>:<kind>:<workflow id>
    Column("kind", Text, nullable=False),  # start, signal, terminate
    Column("workflow_type", Text),
    Column("workflow_id", Text, nullable=False),
    Column("signal", Text),
    Column("payload", JSONB, nullable=False),
    Column("status", Text, nullable=False),  # pending, sent, duplicate (already started), dropped (no workflow)
    Column("attempts", Integer, nullable=False),
    Column("next_attempt_at", DateTime, nullable=False),
    Column("last_error", Text),
    Column("created_at", DateTime, nullable=False),
    Column("sent_at", DateTime),
)
Index("ix_workflow_commands_status_seq", workflow_commands.c.status, workflow_commands.c.seq)
Index("ix_workflow_commands_workflow_id", workflow_commands.c.workflow_id)

workflow_waits = Table(
    "workflow_waits", metadata,
    Column("ref", Text, primary_key=True),  # "Task/task-00012", "DocumentReference/doc-00003"
    Column("workflow_id", Text, nullable=False),
    Column("step", Text, nullable=False),
    Column("created_at", DateTime, nullable=False),
)
Index("ix_workflow_waits_workflow_id", workflow_waits.c.workflow_id)
