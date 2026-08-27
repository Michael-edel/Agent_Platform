"""Usage-based billing v1: SKU config + monthly aggregation + usage_counted_at

Revision ID: p3q4r5s6
Revises: n9o0p3q4
Create Date: 2026-01-15

"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "p3q4r5s6"
down_revision: Union[str, None] = "n9o0p3q4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # AgentSKU usage pricing config (v1)
    op.add_column(
        "agent_skus",
        sa.Column("usage_included_per_month", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column("agent_skus", sa.Column("usage_price_cents", sa.Integer(), nullable=True))
    op.add_column(
        "agent_skus",
        sa.Column("usage_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "agent_skus",
        sa.Column("usage_unit", sa.String(), nullable=False, server_default=sa.text("'execution'")),
    )

    # Monthly aggregation table
    op.create_table(
        "tenant_usage_monthly",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("agent_code", sa.String(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("completed_executions", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "agent_code", "year", "month", name="uq_tenant_usage_monthly"),
    )
    op.create_index("idx_tenant_usage_monthly_tenant_id", "tenant_usage_monthly", ["tenant_id"])
    op.create_index("idx_tenant_usage_monthly_agent_code", "tenant_usage_monthly", ["agent_code"])
    op.create_index("idx_tenant_usage_monthly_tenant_period", "tenant_usage_monthly", ["tenant_id", "year", "month"])

    # Idempotency marker on executions
    op.add_column("agent_executions", sa.Column("usage_counted_at", sa.String(), nullable=True))

    # Configure demo.sentiment_basic as usage-priced
    now = datetime.now(timezone.utc).isoformat()
    op.get_bind().execute(
        sa.text(
            """
            UPDATE agent_skus
            SET usage_enabled = 1,
                usage_unit = 'execution',
                usage_price_cents = 10,
                usage_included_per_month = 1,
                updated_at = :now
            WHERE code = :code
            """
        ),
        {"code": "demo.sentiment_basic", "now": now},
    )


def downgrade() -> None:
    op.drop_column("agent_executions", "usage_counted_at")

    op.drop_index("idx_tenant_usage_monthly_tenant_period", table_name="tenant_usage_monthly")
    op.drop_index("idx_tenant_usage_monthly_agent_code", table_name="tenant_usage_monthly")
    op.drop_index("idx_tenant_usage_monthly_tenant_id", table_name="tenant_usage_monthly")
    op.drop_table("tenant_usage_monthly")

    op.drop_column("agent_skus", "usage_unit")
    op.drop_column("agent_skus", "usage_included_per_month")
    op.drop_column("agent_skus", "usage_price_cents")
    op.drop_column("agent_skus", "usage_enabled")

