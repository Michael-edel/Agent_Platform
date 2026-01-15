"""Admin audit log table

Revision ID: v9w0x1y2
Revises: t7u8v9w0
Create Date: 2026-01-15

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "v9w0x1y2"
down_revision: Union[str, None] = "t7u8v9w0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "admin_audit_log",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("actor_username", sa.String(), nullable=False),
        sa.Column("actor_role", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=True),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("entity_type", sa.String(), nullable=False),
        sa.Column("entity_id", sa.String(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_admin_audit_created_at", "admin_audit_log", ["created_at"])
    op.create_index("idx_admin_audit_actor", "admin_audit_log", ["actor_username"])
    op.create_index("idx_admin_audit_tenant", "admin_audit_log", ["tenant_id"])
    op.create_index("idx_admin_audit_action", "admin_audit_log", ["action"])
    op.create_index("idx_admin_audit_entity_type", "admin_audit_log", ["entity_type"])
    op.create_index("idx_admin_audit_entity_id", "admin_audit_log", ["entity_id"])


def downgrade() -> None:
    op.drop_index("idx_admin_audit_entity_id", table_name="admin_audit_log")
    op.drop_index("idx_admin_audit_entity_type", table_name="admin_audit_log")
    op.drop_index("idx_admin_audit_action", table_name="admin_audit_log")
    op.drop_index("idx_admin_audit_tenant", table_name="admin_audit_log")
    op.drop_index("idx_admin_audit_actor", table_name="admin_audit_log")
    op.drop_index("idx_admin_audit_created_at", table_name="admin_audit_log")
    op.drop_table("admin_audit_log")

