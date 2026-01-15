"""Add tenant_portal_tokens table

Revision ID: g2h3i4j5k6l7
Revises: f1a2b3c4d5e6
Create Date: 2026-01-15

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'g2h3i4j5k6l7'
down_revision: Union[str, None] = 'f1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'tenant_portal_tokens',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('token_hash', sa.String(), nullable=False),
        sa.Column('token_prefix', sa.String(), nullable=False),
        sa.Column('created_at', sa.String(), nullable=False),
        sa.Column('revoked_at', sa.String(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_tenant_portal_tokens_tenant_id', 'tenant_portal_tokens', ['tenant_id'])
    op.create_index('idx_tenant_portal_tokens_revoked_at', 'tenant_portal_tokens', ['revoked_at'])
    op.create_index('idx_tenant_portal_tokens_tenant_active', 'tenant_portal_tokens', ['tenant_id', 'revoked_at'])


def downgrade() -> None:
    op.drop_index('idx_tenant_portal_tokens_tenant_active', table_name='tenant_portal_tokens')
    op.drop_index('idx_tenant_portal_tokens_revoked_at', table_name='tenant_portal_tokens')
    op.drop_index('idx_tenant_portal_tokens_tenant_id', table_name='tenant_portal_tokens')
    op.drop_table('tenant_portal_tokens')
