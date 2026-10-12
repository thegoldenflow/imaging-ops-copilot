"""hospital platform access (6.3): staff unit scope, break-glass grants, de-identification misses, audit fields

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-08 19:49:33.137114
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('break_glass_grants',
    sa.Column('row_key', sa.Text(), nullable=False),
    sa.Column('seq', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('id', sa.Text(), nullable=True),
    sa.Column('user_id', sa.Text(), nullable=True),
    sa.Column('user_name', sa.Text(), nullable=True),
    sa.Column('role', sa.Text(), nullable=True),
    sa.Column('patient_id', sa.Text(), nullable=True),
    sa.Column('mrn_hash', sa.Text(), nullable=True),
    sa.Column('mrn', sa.Text(), nullable=True),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('granted_at', sa.DateTime(), nullable=True),
    sa.Column('expires_at', sa.DateTime(), nullable=True),
    sa.Column('review_due', sa.DateTime(), nullable=True),
    sa.Column('review_status', sa.Text(), nullable=True),
    sa.Column('reviewed_by', sa.Text(), nullable=True),
    sa.Column('reviewed_by_name', sa.Text(), nullable=True),
    sa.Column('reviewed_at', sa.DateTime(), nullable=True),
    sa.Column('review_note', sa.Text(), nullable=True),
    sa.Column('audit_seq', sa.BigInteger(), nullable=True),
    sa.PrimaryKeyConstraint('row_key')
    )
    op.create_index('ix_break_glass_grants_review_status', 'break_glass_grants', ['review_status'], unique=False)
    op.create_index('ix_break_glass_grants_seq', 'break_glass_grants', ['seq'], unique=False)
    op.create_index('ix_break_glass_grants_user_id', 'break_glass_grants', ['user_id'], unique=False)
    op.create_table('deid_misses',
    sa.Column('seq', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('ts', sa.DateTime(), nullable=True),
    sa.Column('task', sa.Text(), nullable=True),
    sa.Column('kind', sa.Text(), nullable=True),
    sa.Column('text', sa.Text(), nullable=True),
    sa.Column('context', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('seq')
    )
    op.add_column('audit_events', sa.Column('event_type', sa.Text(), nullable=True))
    op.add_column('audit_events', sa.Column('patient_mrn_hash', sa.Text(), nullable=True))
    op.add_column('audit_events', sa.Column('encounter_id', sa.Text(), nullable=True))
    op.add_column('audit_events', sa.Column('module', sa.Text(), nullable=True))
    op.add_column('audit_events', sa.Column('prompt_version', sa.Text(), nullable=True))
    op.create_index('ix_audit_events_event_type', 'audit_events', ['event_type'], unique=False)
    op.create_index('ix_audit_events_patient_mrn_hash', 'audit_events', ['patient_mrn_hash'], unique=False)
    op.create_index('ix_audit_events_user_id', 'audit_events', ['user_id'], unique=False)
    op.add_column('staff', sa.Column('unit_ids', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('staff', sa.Column('practitioner_id', sa.Text(), nullable=True))
    op.execute("UPDATE staff SET unit_ids = '[]'::jsonb WHERE unit_ids IS NULL")  # existing users: no unit scope


def downgrade() -> None:
    op.drop_column('staff', 'practitioner_id')
    op.drop_column('staff', 'unit_ids')
    op.drop_index('ix_audit_events_user_id', table_name='audit_events')
    op.drop_index('ix_audit_events_patient_mrn_hash', table_name='audit_events')
    op.drop_index('ix_audit_events_event_type', table_name='audit_events')
    op.drop_column('audit_events', 'prompt_version')
    op.drop_column('audit_events', 'module')
    op.drop_column('audit_events', 'encounter_id')
    op.drop_column('audit_events', 'patient_mrn_hash')
    op.drop_column('audit_events', 'event_type')
    op.drop_table('deid_misses')
    op.drop_index('ix_break_glass_grants_user_id', table_name='break_glass_grants')
    op.drop_index('ix_break_glass_grants_seq', table_name='break_glass_grants')
    op.drop_index('ix_break_glass_grants_review_status', table_name='break_glass_grants')
    op.drop_table('break_glass_grants')
