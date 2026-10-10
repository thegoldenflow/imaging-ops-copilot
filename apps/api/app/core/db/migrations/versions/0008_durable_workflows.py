"""durable workflows (6.5): workflow timelines, the bridge's outbox and waits, low-confidence reviews, exception outcomes

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-09 18:12:34.512927
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('agent_reviews',
    sa.Column('row_key', sa.Text(), nullable=False),
    sa.Column('seq', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('id', sa.Text(), nullable=True),
    sa.Column('run_id', sa.Text(), nullable=True),
    sa.Column('agent_id', sa.Text(), nullable=True),
    sa.Column('agent_version', sa.Text(), nullable=True),
    sa.Column('prompt_version', sa.Text(), nullable=True),
    sa.Column('encounter_id', sa.Text(), nullable=True),
    sa.Column('patient_id', sa.Text(), nullable=True),
    sa.Column('confidence', sa.Float(), nullable=True),
    sa.Column('threshold', sa.Float(), nullable=True),
    sa.Column('input', sa.Text(), nullable=True),
    sa.Column('output', sa.Text(), nullable=True),
    sa.Column('output_refs', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('status', sa.Text(), nullable=True),
    sa.Column('task_id', sa.Text(), nullable=True),
    sa.Column('workflow_id', sa.Text(), nullable=True),
    sa.Column('correction', sa.Text(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('reviewed_by', sa.Text(), nullable=True),
    sa.Column('reviewer_role', sa.Text(), nullable=True),
    sa.Column('reviewed_at', sa.DateTime(), nullable=True),
    sa.Column('case_id', sa.Text(), nullable=True),
    sa.Column('regression', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('row_key')
    )
    op.create_index('ix_agent_reviews_run_id', 'agent_reviews', ['run_id'], unique=False)
    op.create_index('ix_agent_reviews_seq', 'agent_reviews', ['seq'], unique=False)
    op.create_index('ix_agent_reviews_status', 'agent_reviews', ['status'], unique=False)
    op.create_table('workflow_commands',
    sa.Column('seq', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('workflow_type', sa.Text(), nullable=True),
    sa.Column('workflow_id', sa.Text(), nullable=False),
    sa.Column('signal', sa.Text(), nullable=True),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('next_attempt_at', sa.DateTime(), nullable=False),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('sent_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('seq'),
    sa.UniqueConstraint('id')
    )
    op.create_index('ix_workflow_commands_status_seq', 'workflow_commands', ['status', 'seq'], unique=False)
    op.create_index('ix_workflow_commands_workflow_id', 'workflow_commands', ['workflow_id'], unique=False)
    op.create_table('workflow_runs',
    sa.Column('row_key', sa.Text(), nullable=False),
    sa.Column('seq', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('id', sa.Text(), nullable=True),
    sa.Column('workflow_type', sa.Text(), nullable=True),
    sa.Column('status', sa.Text(), nullable=True),
    sa.Column('current_step', sa.Text(), nullable=True),
    sa.Column('version', sa.BigInteger(), nullable=True),
    sa.Column('encounter_id', sa.Text(), nullable=True),
    sa.Column('patient_id', sa.Text(), nullable=True),
    sa.Column('unit_id', sa.Text(), nullable=True),
    sa.Column('exception_id', sa.Text(), nullable=True),
    sa.Column('run_id', sa.Text(), nullable=True),
    sa.Column('steps', sa.Text(), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('meta', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('temporal_run_id', sa.Text(), nullable=True),
    sa.Column('started_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.Column('finished_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('row_key')
    )
    op.create_index('ix_workflow_runs_encounter_id', 'workflow_runs', ['encounter_id'], unique=False)
    op.create_index('ix_workflow_runs_exception_id', 'workflow_runs', ['exception_id'], unique=False)
    op.create_index('ix_workflow_runs_seq', 'workflow_runs', ['seq'], unique=False)
    op.create_index('ix_workflow_runs_workflow_type_status', 'workflow_runs', ['workflow_type', 'status'], unique=False)
    op.create_table('workflow_waits',
    sa.Column('ref', sa.Text(), nullable=False),
    sa.Column('workflow_id', sa.Text(), nullable=False),
    sa.Column('step', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('ref')
    )
    op.create_index('ix_workflow_waits_workflow_id', 'workflow_waits', ['workflow_id'], unique=False)
    op.add_column('flow_exceptions', sa.Column('outcome', postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column('flow_exceptions', 'outcome')
    op.drop_index('ix_workflow_waits_workflow_id', table_name='workflow_waits')
    op.drop_table('workflow_waits')
    op.drop_index('ix_workflow_runs_workflow_type_status', table_name='workflow_runs')
    op.drop_index('ix_workflow_runs_seq', table_name='workflow_runs')
    op.drop_index('ix_workflow_runs_exception_id', table_name='workflow_runs')
    op.drop_index('ix_workflow_runs_encounter_id', table_name='workflow_runs')
    op.drop_table('workflow_runs')
    op.drop_index('ix_workflow_commands_workflow_id', table_name='workflow_commands')
    op.drop_index('ix_workflow_commands_status_seq', table_name='workflow_commands')
    op.drop_table('workflow_commands')
    op.drop_index('ix_agent_reviews_status', table_name='agent_reviews')
    op.drop_index('ix_agent_reviews_seq', table_name='agent_reviews')
    op.drop_index('ix_agent_reviews_run_id', table_name='agent_reviews')
    op.drop_table('agent_reviews')
