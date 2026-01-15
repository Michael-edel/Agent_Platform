"""Billing jobs pipeline + invoice payment_status

Revision ID: t7u8v9w0
Revises: r5s6t7u8
Create Date: 2026-01-15

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "t7u8v9w0"
down_revision: Union[str, None] = "r5s6t7u8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # usage_invoices.payment_status
    op.add_column(
        "usage_invoices",
        sa.Column("payment_status", sa.String(), nullable=False, server_default=sa.text("'unpaid'")),
    )

    # billing_jobs
    op.create_table(
        "billing_jobs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("invoice_id", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default=sa.text("10")),
        sa.Column("last_error_code", sa.String(), nullable=True),
        sa.Column("last_error_message", sa.String(), nullable=True),
        sa.Column("next_attempt_at", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.Column("processing_started_at", sa.String(), nullable=True),
        sa.Column("finished_at", sa.String(), nullable=True),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("provider_ref", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_billing_jobs_idempotency_key"),
        sa.ForeignKeyConstraint(["invoice_id"], ["usage_invoices.id"], name="fk_billing_jobs_invoice", ondelete="CASCADE"),
    )
    op.create_index("idx_billing_jobs_idempotency_key", "billing_jobs", ["idempotency_key"], unique=True)
    op.create_index("idx_billing_jobs_status_next", "billing_jobs", ["status", "next_attempt_at"])
    op.create_index("idx_billing_jobs_invoice", "billing_jobs", ["invoice_id"])


def downgrade() -> None:
    op.drop_index("idx_billing_jobs_invoice", table_name="billing_jobs")
    op.drop_index("idx_billing_jobs_status_next", table_name="billing_jobs")
    op.drop_index("idx_billing_jobs_idempotency_key", table_name="billing_jobs")
    op.drop_table("billing_jobs")

    op.drop_column("usage_invoices", "payment_status")

