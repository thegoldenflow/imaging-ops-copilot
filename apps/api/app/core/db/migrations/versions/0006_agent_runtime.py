"""agent runtime (6.4): run traces and tool idempotency records

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-08 23:08:18.109616
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('agent_traces',
    sa.Column('row_key', sa.Text(), nullable=False),
    sa.Column('seq', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('run_id', sa.Text(), nullable=True),
    sa.Column('agent_id', sa.Text(), nullable=True),
    sa.Column('agent_version', sa.Text(), nullable=True),
    sa.Column('kind', sa.Text(), nullable=True),
    sa.Column('mode', sa.Text(), nullable=True),
    sa.Column('model', sa.Text(), nullable=True),
    sa.Column('prompt_version', sa.Text(), nullable=True),
    sa.Column('eval_status', sa.Text(), nullable=True),
    sa.Column('evaluated', sa.Boolean(), nullable=True),
    sa.Column('actor_id', sa.Text(), nullable=True),
    sa.Column('actor_role', sa.Text(), nullable=True),
    sa.Column('encounter_id', sa.Text(), nullable=True),
    sa.Column('context_id', sa.Text(), nullable=True),
    sa.Column('input_refs', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('llm_calls', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('tool_calls', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('output_refs', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('confidence', sa.Float(), nullable=True),
    sa.Column('human_action', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('outcome', sa.Text(), nullable=True),
    sa.Column('tokens_in', sa.BigInteger(), nullable=True),
    sa.Column('tokens_out', sa.BigInteger(), nullable=True),
    sa.Column('cost_usd', sa.Float(), nullable=True),
    sa.Column('provenance_id', sa.Text(), nullable=True),
    sa.Column('started_at', sa.DateTime(), nullable=True),
    sa.Column('finished_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('row_key')
    )
    op.create_index('ix_agent_traces_agent_id', 'agent_traces', ['agent_id'], unique=False)
    op.create_index('ix_agent_traces_seq', 'agent_traces', ['seq'], unique=False)
    op.create_index('ix_agent_traces_started_at', 'agent_traces', ['started_at'], unique=False)
    op.create_table('tool_idempotency',
    sa.Column('row_key', sa.Text(), nullable=False),
    sa.Column('seq', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('key', sa.Text(), nullable=True),
    sa.Column('agent_id', sa.Text(), nullable=True),
    sa.Column('tool_id', sa.Text(), nullable=True),
    sa.Column('encounter_id', sa.Text(), nullable=True),
    sa.Column('run_id', sa.Text(), nullable=True),
    sa.Column('status', sa.Text(), nullable=True),
    sa.Column('result', sa.Text(), nullable=True),
    sa.Column('approval_task_id', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('expires_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('row_key')
    )
    op.create_index('ix_tool_idempotency_expires_at', 'tool_idempotency', ['expires_at'], unique=False)
    op.create_index('ix_tool_idempotency_seq', 'tool_idempotency', ['seq'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_tool_idempotency_seq', table_name='tool_idempotency')
    op.drop_index('ix_tool_idempotency_expires_at', table_name='tool_idempotency')
    op.drop_table('tool_idempotency')
    op.drop_index('ix_agent_traces_started_at', table_name='agent_traces')
    op.drop_index('ix_agent_traces_seq', table_name='agent_traces')
    op.drop_index('ix_agent_traces_agent_id', table_name='agent_traces')
    op.drop_table('agent_traces')
