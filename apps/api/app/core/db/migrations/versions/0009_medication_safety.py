"""medication safety (7.2): the order review queue and medication reconciliations

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-09 18:37:57.227901

Follows WP4c's 0008 (durable workflows); the two were written side by side and this one was rebased onto it.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('medrec_sessions',
    sa.Column('row_key', sa.Text(), nullable=False),
    sa.Column('seq', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('id', sa.Text(), nullable=True),
    sa.Column('encounter_id', sa.Text(), nullable=True),
    sa.Column('patient_id', sa.Text(), nullable=True),
    sa.Column('kind', sa.Text(), nullable=True),
    sa.Column('unit_id', sa.Text(), nullable=True),
    sa.Column('status', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.Column('severity', sa.Text(), nullable=True),
    sa.Column('items', sa.Text(), nullable=True),
    sa.Column('ai_status', sa.Text(), nullable=True),
    sa.Column('ai_detail', sa.Text(), nullable=True),
    sa.Column('document_id', sa.Text(), nullable=True),
    sa.Column('signatures', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('change_tasks', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('discharge_table', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('row_key')
    )
    op.create_index('ix_medrec_sessions_encounter_id', 'medrec_sessions', ['encounter_id'], unique=False)
    op.create_index('ix_medrec_sessions_id', 'medrec_sessions', ['id'], unique=False)
    op.create_index('ix_medrec_sessions_seq', 'medrec_sessions', ['seq'], unique=False)
    op.create_index('ix_medrec_sessions_status', 'medrec_sessions', ['status'], unique=False)
    op.create_table('order_reviews',
    sa.Column('row_key', sa.Text(), nullable=False),
    sa.Column('seq', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('id', sa.Text(), nullable=True),
    sa.Column('order_ref', sa.Text(), nullable=True),
    sa.Column('kind', sa.Text(), nullable=True),
    sa.Column('patient_id', sa.Text(), nullable=True),
    sa.Column('encounter_id', sa.Text(), nullable=True),
    sa.Column('unit_id', sa.Text(), nullable=True),
    sa.Column('order', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('authored_at', sa.DateTime(), nullable=True),
    sa.Column('reviewed_at', sa.DateTime(), nullable=True),
    sa.Column('reviewer_roles', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('findings', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('severity', sa.Text(), nullable=True),
    sa.Column('blocking', sa.Boolean(), nullable=True),
    sa.Column('status', sa.Text(), nullable=True),
    sa.Column('commentary_status', sa.Text(), nullable=True),
    sa.Column('commentary', sa.Text(), nullable=True),
    sa.Column('review_task_id', sa.Text(), nullable=True),
    sa.Column('decision', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('row_key')
    )
    op.create_index('ix_order_reviews_id', 'order_reviews', ['id'], unique=False)
    op.create_index('ix_order_reviews_patient_id', 'order_reviews', ['patient_id'], unique=False)
    op.create_index('ix_order_reviews_seq', 'order_reviews', ['seq'], unique=False)
    op.create_index('ix_order_reviews_status', 'order_reviews', ['status'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_order_reviews_status', table_name='order_reviews')
    op.drop_index('ix_order_reviews_seq', table_name='order_reviews')
    op.drop_index('ix_order_reviews_patient_id', table_name='order_reviews')
    op.drop_index('ix_order_reviews_id', table_name='order_reviews')
    op.drop_table('order_reviews')
    op.drop_index('ix_medrec_sessions_status', table_name='medrec_sessions')
    op.drop_index('ix_medrec_sessions_seq', table_name='medrec_sessions')
    op.drop_index('ix_medrec_sessions_id', table_name='medrec_sessions')
    op.drop_index('ix_medrec_sessions_encounter_id', table_name='medrec_sessions')
    op.drop_table('medrec_sessions')
