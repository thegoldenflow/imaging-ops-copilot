"""control tower (7.1): the exception stream (rule engine findings, narratives, decisions)

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-09 04:18:52.377748
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('flow_exceptions',
    sa.Column('row_key', sa.Text(), nullable=False),
    sa.Column('seq', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('id', sa.Text(), nullable=True),
    sa.Column('key', sa.Text(), nullable=True),
    sa.Column('rule', sa.Text(), nullable=True),
    sa.Column('severity', sa.Text(), nullable=True),
    sa.Column('status', sa.Text(), nullable=True),
    sa.Column('title', sa.Text(), nullable=True),
    sa.Column('summary', sa.Text(), nullable=True),
    sa.Column('unit_id', sa.Text(), nullable=True),
    sa.Column('subject_ref', sa.Text(), nullable=True),
    sa.Column('facts', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('evidence_refs', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('menu', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('detected_at', sa.DateTime(), nullable=True),
    sa.Column('changed_at', sa.DateTime(), nullable=True),
    sa.Column('cleared_at', sa.DateTime(), nullable=True),
    sa.Column('narration_status', sa.Text(), nullable=True),
    sa.Column('narration', sa.Text(), nullable=True),
    sa.Column('review_task_id', sa.Text(), nullable=True),
    sa.Column('decision', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('remind_at', sa.DateTime(), nullable=True),
    sa.Column('reminders', sa.BigInteger(), nullable=True),
    sa.PrimaryKeyConstraint('row_key')
    )
    op.create_index('ix_flow_exceptions_key', 'flow_exceptions', ['key'], unique=False)
    op.create_index('ix_flow_exceptions_seq', 'flow_exceptions', ['seq'], unique=False)
    op.create_index('ix_flow_exceptions_status', 'flow_exceptions', ['status'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_flow_exceptions_status', table_name='flow_exceptions')
    op.drop_index('ix_flow_exceptions_seq', table_name='flow_exceptions')
    op.drop_index('ix_flow_exceptions_key', table_name='flow_exceptions')
    op.drop_table('flow_exceptions')
