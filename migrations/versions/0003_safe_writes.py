"""idempotency key on requests, calculating_started_at, pg_stat_statements

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('requests', sa.Column('idempotency_key', postgresql.UUID(as_uuid=True), nullable=True))
    op.create_unique_constraint('requests_idempotency_key_key', 'requests', ['idempotency_key'])
    op.add_column('payroll_periods', sa.Column('calculating_started_at', sa.DateTime(timezone=True), nullable=True))
    # Only creates the views; the library itself is loaded by shared_preload_libraries in compose.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_stat_statements")


def downgrade() -> None:
    op.drop_column('payroll_periods', 'calculating_started_at')
    op.drop_constraint('requests_idempotency_key_key', 'requests', type_='unique')
    op.drop_column('requests', 'idempotency_key')
    op.execute("DROP EXTENSION IF EXISTS pg_stat_statements")
