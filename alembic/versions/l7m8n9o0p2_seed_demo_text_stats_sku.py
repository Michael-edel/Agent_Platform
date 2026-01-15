"""Seed demo.text_stats AgentSKU (free)

Revision ID: l7m8n9o0p2
Revises: k6l7m8n9o0p1
Create Date: 2026-01-15

"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "l7m8n9o0p2"
down_revision: Union[str, None] = "k6l7m8n9o0p1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    now = datetime.now(timezone.utc).isoformat()

    params = {
        "id": str(uuid.uuid4()),
        "code": "demo.text_stats",
        "name": "Demo: Text Stats",
        "description": "Counts chars/words/lines and top words (built-in demo agent).",
        "status": "active",
        "pricing_model": "free",
        "created_at": now,
        "updated_at": now,
    }

    if bind.dialect.name == "sqlite":
        op.execute(
            sa.text(
                """
                INSERT OR IGNORE INTO agent_skus
                  (id, code, name, description, status, pricing_model, created_at, updated_at)
                VALUES
                  (:id, :code, :name, :description, :status, :pricing_model, :created_at, :updated_at)
                """
            ),
            params,
        )
    else:
        op.execute(
            sa.text(
                """
                INSERT INTO agent_skus
                  (id, code, name, description, status, pricing_model, created_at, updated_at)
                VALUES
                  (:id, :code, :name, :description, :status, :pricing_model, :created_at, :updated_at)
                ON CONFLICT (code) DO NOTHING
                """
            ),
            params,
        )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM agent_skus WHERE code = :code"), {"code": "demo.text_stats"})

