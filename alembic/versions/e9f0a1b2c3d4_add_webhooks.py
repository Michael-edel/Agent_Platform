"""add_webhooks

Revision ID: e9f0a1b2c3d4
Revises: d7e8f9a0b1c2
Create Date: 2026-01-15 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e9f0a1b2c3d4'
down_revision: Union[str, Sequence[str], None] = 'd7e8f9a0b1c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema - add webhooks and webhook_deliveries tables for outgoing webhooks."""
    
    # Webhooks (конфигурация webhooks для tenant)
    op.create_table(
        'webhooks',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('tenant_id', sa.Text(), nullable=False),
        sa.Column('url', sa.Text(), nullable=False),
        sa.Column('events', sa.Text(), nullable=False),  # JSON array: ["invoice.ready", ...]
        sa.Column('secret', sa.Text(), nullable=False),  # Для HMAC подписи
        # sa.true() корректно компилируется в TRUE для PostgreSQL и в 1 для SQLite.
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Indexes for webhooks
    op.create_index('idx_webhooks_tenant_id', 'webhooks', ['tenant_id'])
    op.create_index('idx_webhooks_active', 'webhooks', ['active'])
    op.create_index('idx_webhooks_tenant_active', 'webhooks', ['tenant_id', 'active'])
    
    # Webhook Deliveries (история доставок)
    op.create_table(
        'webhook_deliveries',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('webhook_id', sa.Text(), nullable=False),
        sa.Column('event_type', sa.Text(), nullable=False),
        sa.Column('payload', sa.Text(), nullable=False),  # JSON payload
        sa.Column('status', sa.Text(), nullable=False, server_default='queued'),  # queued|sent|failed|dead
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('max_retries', sa.Integer(), nullable=False, server_default='5'),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('next_run_at', sa.Text(), nullable=True),  # ISO timestamp для retry
        sa.Column('created_at', sa.Text(), nullable=False),
        sa.Column('sent_at', sa.Text(), nullable=True),  # ISO timestamp когда успешно отправлен
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['webhook_id'], ['webhooks.id'], ondelete='CASCADE')
    )
    
    # Indexes for webhook_deliveries
    op.create_index('idx_webhook_deliveries_webhook_id', 'webhook_deliveries', ['webhook_id'])
    op.create_index('idx_webhook_deliveries_status', 'webhook_deliveries', ['status'])
    op.create_index('idx_webhook_deliveries_event_type', 'webhook_deliveries', ['event_type'])
    op.create_index('idx_webhook_deliveries_next_run', 'webhook_deliveries', ['status', 'next_run_at'])
    # Composite index для эффективного выбора deliveries для обработки
    op.create_index(
        'idx_webhook_deliveries_status_next_run',
        'webhook_deliveries',
        ['status', 'next_run_at']
    )


def downgrade() -> None:
    """Downgrade schema - remove webhooks and webhook_deliveries tables."""
    
    # Drop indexes first
    op.drop_index('idx_webhook_deliveries_status_next_run', table_name='webhook_deliveries')
    op.drop_index('idx_webhook_deliveries_next_run', table_name='webhook_deliveries')
    op.drop_index('idx_webhook_deliveries_event_type', table_name='webhook_deliveries')
    op.drop_index('idx_webhook_deliveries_status', table_name='webhook_deliveries')
    op.drop_index('idx_webhook_deliveries_webhook_id', table_name='webhook_deliveries')
    
    op.drop_index('idx_webhooks_tenant_active', table_name='webhooks')
    op.drop_index('idx_webhooks_active', table_name='webhooks')
    op.drop_index('idx_webhooks_tenant_id', table_name='webhooks')
    
    # Drop tables
    op.drop_table('webhook_deliveries')
    op.drop_table('webhooks')
