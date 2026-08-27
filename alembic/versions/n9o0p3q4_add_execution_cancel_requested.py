"""Add cancel_requested fields to agent_executions

Revision ID: n9o0p3q4
Revises: m8n9o0p3
Create Date: 2026-01-15

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "n9o0p3q4"
down_revision: Union[str, None] = "m8n9o0p3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agent_executions",
        sa.Column(
            "cancel_requested",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "agent_executions",
        sa.Column("cancel_requested_at", sa.String(), nullable=True),
    )
    op.alter_column(
        "agent_executions",
        "cancel_requested",
        server_default=None,
    )



def downgrade() -> None:
    op.drop_column("agent_executions", "cancel_requested_at")
    op.drop_column("agent_executions", "cancel_requested")

