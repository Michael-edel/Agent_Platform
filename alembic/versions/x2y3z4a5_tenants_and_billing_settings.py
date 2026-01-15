"""Tenants + per-tenant billing settings

Revision ID: x2y3z4a5
Revises: w0x1y2z3
Create Date: 2026-01-16
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "x2y3z4a5"
down_revision: Union[str, None] = "w0x1y2z3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_tenants_is_active", "tenants", ["is_active"])
    op.create_index("idx_tenants_created_at", "tenants", ["created_at"])

    op.create_table(
        "tenant_billing_settings",
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("default_provider", sa.String(), nullable=True),
        sa.Column("stripe_enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("kaspi_enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id"),
    )
    op.create_index("idx_tenant_billing_settings_tenant_id", "tenant_billing_settings", ["tenant_id"])
    op.create_index(
        "idx_tenant_billing_settings_default_provider",
        "tenant_billing_settings",
        ["default_provider"],
    )


def downgrade() -> None:
    op.drop_index("idx_tenant_billing_settings_default_provider", table_name="tenant_billing_settings")
    op.drop_index("idx_tenant_billing_settings_tenant_id", table_name="tenant_billing_settings")
    op.drop_table("tenant_billing_settings")

    op.drop_index("idx_tenants_created_at", table_name="tenants")
    op.drop_index("idx_tenants_is_active", table_name="tenants")
    op.drop_table("tenants")

