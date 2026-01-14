"""add_subscription_status_to_tenant_plans

Revision ID: b3c4d5e6f7g8
Revises: a2b3c4d5e6f7
Create Date: 2026-01-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b3c4d5e6f7g8"
down_revision: Union[str, Sequence[str], None] = "a2b3c4d5e6f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema - add subscription_status and failed_charges to tenant_plans."""
    op.add_column(
        "tenant_plans",
        sa.Column("subscription_status", sa.Text(), nullable=True),
    )
    op.add_column(
        "tenant_plans",
        sa.Column("failed_charges", sa.Integer(), nullable=False, server_default="0"),
    )

    op.create_index("idx_tenant_plans_subscription_status", "tenant_plans", ["subscription_status"])

    # Backfill status:
    # - trial -> trial
    # - others -> active
    op.execute("UPDATE tenant_plans SET subscription_status = 'trial' WHERE plan_id = 'trial'")
    op.execute("UPDATE tenant_plans SET subscription_status = 'active' WHERE subscription_status IS NULL")

    # Make subscription_status NOT NULL (SQLite doesn't support ALTER COLUMN easily via op.alter_column;
    # For MVP we keep it nullable but always set in code. Postgres migrations can enforce NOT NULL later.)


def downgrade() -> None:
    """Downgrade schema - remove subscription_status and failed_charges from tenant_plans."""
    op.drop_index("idx_tenant_plans_subscription_status", table_name="tenant_plans")
    op.drop_column("tenant_plans", "failed_charges")
    op.drop_column("tenant_plans", "subscription_status")

