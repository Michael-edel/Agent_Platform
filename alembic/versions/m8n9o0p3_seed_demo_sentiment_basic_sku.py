"""Seed demo.sentiment_basic AgentSKU (paid subscription add-on)

Revision ID: m8n9o0p3
Revises: l7m8n9o0p2
Create Date: 2026-01-15

"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "m8n9o0p3"
down_revision: Union[str, None] = "l7m8n9o0p2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    now = datetime.now(timezone.utc).isoformat()

    params = {
        "id": str(uuid.uuid4()),
        "code": "demo.sentiment_basic",
        "name": "Demo: Sentiment Basic",
        "description": "Paid demo agent: simple lexicon-based sentiment (RU/EN).",
        "status": "active",
        "pricing_model": "subscription",
        "created_at": now,
        "updated_at": now,
    }

    if bind.dialect.name == "sqlite":
        bind.execute(
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
        bind.execute(
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
    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM agent_skus WHERE code = :code"), {"code": "demo.sentiment_basic"})
