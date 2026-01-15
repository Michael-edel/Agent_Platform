"""Usage invoices: finalized snapshots + lines + event_emitted_at

Revision ID: r5s6t7u8
Revises: p3q4r5s6
Create Date: 2026-01-15

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "r5s6t7u8"
down_revision: Union[str, None] = "p3q4r5s6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "usage_invoices",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("period_year", sa.Integer(), nullable=False),
        sa.Column("period_month", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("finalized_at", sa.String(), nullable=True),
        sa.Column("event_emitted_at", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "period_year", "period_month", name="uq_usage_invoice_period"),
    )
    op.create_index("idx_usage_invoices_tenant_id", "usage_invoices", ["tenant_id"])
    op.create_index("idx_usage_invoices_tenant_period", "usage_invoices", ["tenant_id", "period_year", "period_month"])

    op.create_table(
        "usage_invoice_lines",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("invoice_id", sa.String(), nullable=False),
        sa.Column("agent_code", sa.String(), nullable=False),
        sa.Column("unit", sa.String(), nullable=False),
        sa.Column("used", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("included", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("billable", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("price_cents", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("amount_cents", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["invoice_id"], ["usage_invoices.id"], name="fk_usage_invoice_lines_invoice", ondelete="CASCADE"),
    )
    op.create_index("idx_usage_invoice_lines_invoice", "usage_invoice_lines", ["invoice_id"])
    op.create_index("idx_usage_invoice_lines_agent", "usage_invoice_lines", ["agent_code"])


def downgrade() -> None:
    op.drop_index("idx_usage_invoice_lines_agent", table_name="usage_invoice_lines")
    op.drop_index("idx_usage_invoice_lines_invoice", table_name="usage_invoice_lines")
    op.drop_table("usage_invoice_lines")

    op.drop_index("idx_usage_invoices_tenant_period", table_name="usage_invoices")
    op.drop_index("idx_usage_invoices_tenant_id", table_name="usage_invoices")
    op.drop_table("usage_invoices")

