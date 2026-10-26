"""BRIN indexes for the live monitor's time-window queries

Revision ID: 0004
Revises: 0003
"""
from alembic import op

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index('ix_attendance_events_time_brin', 'attendance_events', ['event_time'], postgresql_using='brin')
    op.create_index('ix_requests_created_brin', 'requests', ['created_at'], postgresql_using='brin')


def downgrade() -> None:
    op.drop_index('ix_requests_created_brin', table_name='requests')
    op.drop_index('ix_attendance_events_time_brin', table_name='attendance_events')
