"""add_kaspi_orders

Revision ID: a2b3c4d5e6f7
Revises: f1a2b3c4d5e6
Create Date: 2026-01-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a2b3c4d5e6f7"
down_revision: Union[str, Sequence[str], None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema - add kaspi_orders table to link kaspi_order_id with tenant and plan."""
    op.create_table(
        "kaspi_orders",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("plan_id", sa.Text(), nullable=False),  # plans.id: "pro" | "enterprise"
        sa.Column("kaspi_order_id", sa.Text(), nullable=False),  # external_order_id from Kaspi
        sa.Column("status", sa.Text(), nullable=False, server_default="created"),  # created|paid|failed|canceled
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("paid_at", sa.Text(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("raw_payload", sa.Text(), nullable=True),  # JSON (safe fields only)
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kaspi_order_id", name="uq_kaspi_orders_kaspi_order_id"),
    )

    op.create_index("idx_kaspi_orders_tenant_id", "kaspi_orders", ["tenant_id"])
    op.create_index("idx_kaspi_orders_plan_id", "kaspi_orders", ["plan_id"])
    op.create_index("idx_kaspi_orders_status", "kaspi_orders", ["status"])


def downgrade() -> None:
    """Downgrade schema - remove kaspi_orders table."""
    op.drop_index("idx_kaspi_orders_status", table_name="kaspi_orders")
    op.drop_index("idx_kaspi_orders_plan_id", table_name="kaspi_orders")
    op.drop_index("idx_kaspi_orders_tenant_id", table_name="kaspi_orders")
    op.drop_table("kaspi_orders")

