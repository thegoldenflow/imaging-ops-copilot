"""domain event log (event bus outbox, consumer cursors and deliveries)

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-08 23:40:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('domain_events',
    sa.Column('seq', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('event_id', sa.Text(), nullable=False),
    sa.Column('type', sa.Text(), nullable=False),
    sa.Column('occurred_at', sa.DateTime(), nullable=False),
    sa.Column('recorded_at', sa.DateTime(), nullable=False),
    sa.Column('correlation_id', sa.Text(), nullable=False),
    sa.Column('actor', sa.Text(), nullable=False),
    sa.Column('source', sa.Text(), nullable=False),
    sa.Column('message_id', sa.Text(), nullable=True),
    sa.Column('patient', sa.Text(), nullable=True),
    sa.Column('encounter', sa.Text(), nullable=True),
    sa.Column('refs', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('attrs', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.PrimaryKeyConstraint('seq')
    )
    op.create_index('ix_domain_events_encounter', 'domain_events', ['encounter'], unique=False)
    op.create_index('ix_domain_events_event_id', 'domain_events', ['event_id'], unique=False)
    op.create_index('ix_domain_events_type_seq', 'domain_events', ['type', 'seq'], unique=False)
    op.create_table('event_consumers',
    sa.Column('consumer', sa.Text(), nullable=False),
    sa.Column('cursor', sa.BigInteger(), nullable=False),
    sa.Column('duplicates', sa.BigInteger(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('consumer')
    )
    op.create_table('event_deliveries',
    sa.Column('consumer', sa.Text(), nullable=False),
    sa.Column('event_id', sa.Text(), nullable=False),
    sa.Column('seq', sa.BigInteger(), nullable=False),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('attempts', sa.BigInteger(), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('consumer', 'event_id')
    )
    op.create_index('ix_event_deliveries_status', 'event_deliveries', ['status'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_event_deliveries_status', table_name='event_deliveries')
    op.drop_table('event_deliveries')
    op.drop_table('event_consumers')
    op.drop_index('ix_domain_events_type_seq', table_name='domain_events')
    op.drop_index('ix_domain_events_event_id', table_name='domain_events')
    op.drop_index('ix_domain_events_encounter', table_name='domain_events')
    op.drop_table('domain_events')
