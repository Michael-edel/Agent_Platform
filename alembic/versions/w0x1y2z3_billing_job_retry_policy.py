"""BillingJob retry metadata + indexes

Revision ID: w0x1y2z3
Revises: v9w0x1y2
Create Date: 2026-01-15

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "w0x1y2z3"
down_revision: Union[str, None] = "v9w0x1y2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Columns already exist: attempt_count, max_attempts, next_attempt_at, provider_ref (nullable)
    # Add retry/locking metadata columns if missing.
    op.add_column("billing_jobs", sa.Column("last_attempt_at", sa.String(), nullable=True))
    op.add_column("billing_jobs", sa.Column("locked_at", sa.String(), nullable=True))
    op.add_column("billing_jobs", sa.Column("locked_by", sa.String(), nullable=True))

    # Index provider_ref for lookup/reconcile.
    op.create_index("idx_billing_jobs_provider_ref", "billing_jobs", ["provider_ref"])

    # Default max_attempts: best-effort (works in Postgres; SQLite may ignore).
    try:
        op.alter_column("billing_jobs", "max_attempts", server_default=sa.text("5"))
    except Exception:
        pass


def downgrade() -> None:
    try:
        op.alter_column("billing_jobs", "max_attempts", server_default=None)
    except Exception:
        pass
    try:
        op.drop_index("idx_billing_jobs_status_next", table_name="billing_jobs")
    except Exception:
        pass
    op.drop_index("idx_billing_jobs_provider_ref", table_name="billing_jobs")

    op.drop_column("billing_jobs", "locked_by")
    op.drop_column("billing_jobs", "locked_at")
    op.drop_column("billing_jobs", "last_attempt_at")

