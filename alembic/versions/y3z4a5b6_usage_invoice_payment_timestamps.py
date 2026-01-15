"""UsageInvoice payment status timestamps (SLA)

Revision ID: y3z4a5b6
Revises: x2y3z4a5
Create Date: 2026-01-16
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "y3z4a5b6"
down_revision: Union[str, None] = "x2y3z4a5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("usage_invoices", sa.Column("payment_status_updated_at", sa.String(), nullable=True))
    op.add_column("usage_invoices", sa.Column("paid_at", sa.String(), nullable=True))
    op.add_column("usage_invoices", sa.Column("failed_at", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("usage_invoices", "failed_at")
    op.drop_column("usage_invoices", "paid_at")
    op.drop_column("usage_invoices", "payment_status_updated_at")

