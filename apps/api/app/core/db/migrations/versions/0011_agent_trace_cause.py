"""AI on demand: why each agent run happened (agent_traces.cause)

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-14 18:00:00

The trace of a run says who caused it, by doing what, for which item (app/agents/cause.py): the browser's demo
session, the person, the trigger (opened, asked, their action, a batch, a storyline step) and the item's id and
label, for the "What the AI did in this demo" panel. Ids and labels only, like the rest of the trace. Additive and
nullable: earlier traces and the background's own runs have none.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('agent_traces', sa.Column('cause', postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column('agent_traces', 'cause')
