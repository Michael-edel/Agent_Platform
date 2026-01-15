"""Add tenant_agents table

Revision ID: i4j5k6l7m8n9
Revises: h3i4j5k6l7m8
Create Date: 2026-01-15

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'i4j5k6l7m8n9'
down_revision: Union[str, None] = 'h3i4j5k6l7m8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'tenant_agents',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('agent_sku_id', sa.String(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('activated_at', sa.String(), nullable=True),
        sa.Column('disabled_at', sa.String(), nullable=True),
        sa.Column('created_at', sa.String(), nullable=False),
        sa.Column('updated_at', sa.String(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['agent_sku_id'], ['agent_skus.id'], name='fk_tenant_agents_agent_sku'),
        sa.UniqueConstraint('tenant_id', 'agent_sku_id', name='uq_tenant_agent'),
    )
    op.create_index('idx_tenant_agents_tenant_id', 'tenant_agents', ['tenant_id'])
    op.create_index('idx_tenant_agents_agent_sku_id', 'tenant_agents', ['agent_sku_id'])
    op.create_index('idx_tenant_agents_status', 'tenant_agents', ['status'])


def downgrade() -> None:
    op.drop_index('idx_tenant_agents_status', table_name='tenant_agents')
    op.drop_index('idx_tenant_agents_agent_sku_id', table_name='tenant_agents')
    op.drop_index('idx_tenant_agents_tenant_id', table_name='tenant_agents')
    op.drop_table('tenant_agents')
