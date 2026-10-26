"""append-only trigger on attendance_events, payroll_periods.calculated_at

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa

from app.modules.attendance.models import EVENTS_GUARD_FUNCTION, EVENTS_GUARD_TRIGGER

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('payroll_periods', sa.Column('calculated_at', sa.DateTime(timezone=True), nullable=True))
    op.execute(EVENTS_GUARD_FUNCTION)
    op.execute(EVENTS_GUARD_TRIGGER)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_attendance_events_append_only ON attendance_events")
    op.execute("DROP FUNCTION IF EXISTS attendance_events_append_only()")
    op.drop_column('payroll_periods', 'calculated_at')
