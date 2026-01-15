"""Add tenant_agent_subscriptions table

Revision ID: k6l7m8n9o0p1
Revises: j5k6l7m8n9o0
Create Date: 2026-01-15

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'k6l7m8n9o0p1'
down_revision: Union[str, None] = 'j5k6l7m8n9o0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'tenant_agent_subscriptions',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('agent_sku_id', sa.String(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('starts_at', sa.String(), nullable=False),
        sa.Column('ends_at', sa.String(), nullable=True),
        sa.Column('cancel_at_period_end', sa.Integer(), nullable=False, default=0),
        sa.Column('source', sa.String(), nullable=False),
        sa.Column('external_ref', sa.String(), nullable=True),
        sa.Column('created_at', sa.String(), nullable=False),
        sa.Column('updated_at', sa.String(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['agent_sku_id'], ['agent_skus.id'], name='fk_tenant_agent_subscriptions_sku'),
        sa.UniqueConstraint('tenant_id', 'agent_sku_id', name='uq_tenant_agent_subscription'),
    )
    op.create_index('idx_tenant_agent_subscriptions_tenant_id', 'tenant_agent_subscriptions', ['tenant_id'])
    op.create_index('idx_tenant_agent_subscriptions_agent_sku_id', 'tenant_agent_subscriptions', ['agent_sku_id'])
    op.create_index('idx_tenant_agent_subscriptions_status', 'tenant_agent_subscriptions', ['status'])


def downgrade() -> None:
    op.drop_index('idx_tenant_agent_subscriptions_status', table_name='tenant_agent_subscriptions')
    op.drop_index('idx_tenant_agent_subscriptions_agent_sku_id', table_name='tenant_agent_subscriptions')
    op.drop_index('idx_tenant_agent_subscriptions_tenant_id', table_name='tenant_agent_subscriptions')
    op.drop_table('tenant_agent_subscriptions')
