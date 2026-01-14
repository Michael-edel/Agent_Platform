"""initial_schema

This migration represents the initial baseline schema for PostgreSQL.
It captures the current database schema as used in production.

Revision ID: d01c4c0f81ed
Revises: 
Create Date: 2026-01-14 15:15:32.748557

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd01c4c0f81ed'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema - create all tables."""
    
    # ============================================
    # Billing Plans & Subscriptions (EntitlementService)
    # ============================================
    
    # Billing plans
    op.create_table(
        'billing_plans',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('currency', sa.Text(), nullable=False),
        sa.Column('price_minor', sa.Integer(), nullable=False),
        sa.Column('period', sa.Text(), nullable=False),
        sa.Column('active', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('created_at', sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Plan limits
    op.create_table(
        'billing_plan_limits',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('plan_id', sa.Text(), nullable=False),
        sa.Column('metric', sa.Text(), nullable=False),
        sa.Column('monthly_quota', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(['plan_id'], ['billing_plans.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_plan_limits_plan_id', 'billing_plan_limits', ['plan_id'])
    
    # Tenant subscriptions
    op.create_table(
        'tenant_subscriptions',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('tenant_id', sa.Text(), nullable=False),
        sa.Column('provider', sa.Text(), nullable=False),
        sa.Column('provider_customer_id', sa.Text(), nullable=True),
        sa.Column('provider_subscription_id', sa.Text(), nullable=True),
        sa.Column('plan_id', sa.Text(), nullable=False),
        sa.Column('status', sa.Text(), nullable=False),
        sa.Column('current_period_start', sa.Text(), nullable=True),
        sa.Column('current_period_end', sa.Text(), nullable=True),
        sa.Column('updated_at', sa.Text(), nullable=False),
        sa.Column('created_at', sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(['plan_id'], ['billing_plans.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id')
    )
    op.create_index('idx_subscriptions_tenant_id', 'tenant_subscriptions', ['tenant_id'])
    op.create_index('idx_subscriptions_provider', 'tenant_subscriptions', ['provider'])
    
    # Webhook events (idempotency)
    op.create_table(
        'billing_webhook_events',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('provider', sa.Text(), nullable=False),
        sa.Column('event_id', sa.Text(), nullable=False),
        sa.Column('received_at', sa.Text(), nullable=False),
        sa.Column('processed_at', sa.Text(), nullable=True),
        sa.Column('tenant_id', sa.Text(), nullable=True),
        sa.Column('raw_json', sa.Text(), nullable=False),
        sa.Column('status', sa.Text(), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('event_id')
    )
    op.create_index('idx_webhook_events_event_id', 'billing_webhook_events', ['event_id'], unique=True)
    op.create_index('idx_webhook_events_provider', 'billing_webhook_events', ['provider'])
    op.create_index('idx_webhook_events_status', 'billing_webhook_events', ['status'])
    
    # Payment profiles
    op.create_table(
        'billing_tenant_payment_profiles',
        sa.Column('tenant_id', sa.Text(), nullable=False),
        sa.Column('stripe_customer_id', sa.Text(), nullable=True),
        sa.Column('kaspi_token', sa.Text(), nullable=True),
        sa.Column('updated_at', sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint('tenant_id')
    )
    
    # Orders
    op.create_table(
        'billing_orders',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('tenant_id', sa.Text(), nullable=False),
        sa.Column('provider', sa.Text(), nullable=False),
        sa.Column('plan_id', sa.Text(), nullable=False),
        sa.Column('amount_minor', sa.Integer(), nullable=False),
        sa.Column('currency', sa.Text(), nullable=False),
        sa.Column('status', sa.Text(), nullable=False),
        sa.Column('external_order_id', sa.Text(), nullable=True),
        sa.Column('created_at', sa.Text(), nullable=False),
        sa.Column('paid_at', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['plan_id'], ['billing_plans.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_orders_tenant_id', 'billing_orders', ['tenant_id'])
    op.create_index('idx_orders_external_id', 'billing_orders', ['external_order_id'])
    op.create_index('idx_orders_status', 'billing_orders', ['status'])
    op.create_index('idx_orders_provider', 'billing_orders', ['provider'])
    
    # ============================================
    # Billing Rates & Usage (BillingService)
    # ============================================
    
    # Billing rates
    op.create_table(
        'billing_rates',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('tenant_id', sa.Text(), nullable=True),
        sa.Column('metric', sa.Text(), nullable=False),
        sa.Column('unit_price_minor', sa.Integer(), nullable=False),
        sa.Column('currency', sa.Text(), nullable=False),
        sa.Column('active', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('monthly_quota', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_billing_rates_tenant_metric', 'billing_rates', ['tenant_id', 'metric'])
    op.create_index('idx_billing_rates_active', 'billing_rates', ['active'])
    
    # Billing usage
    op.create_table(
        'billing_usage',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('tenant_id', sa.Text(), nullable=False),
        sa.Column('event_id', sa.Text(), nullable=False),
        sa.Column('artifact_id', sa.Text(), nullable=True),
        sa.Column('event_type', sa.Text(), nullable=False),
        sa.Column('metric', sa.Text(), nullable=False),
        sa.Column('units', sa.Numeric(), nullable=False),
        sa.Column('unit_price_minor', sa.Integer(), nullable=False),
        sa.Column('amount_minor', sa.Integer(), nullable=False),
        sa.Column('currency', sa.Text(), nullable=False),
        sa.Column('period', sa.Text(), nullable=False),
        sa.Column('created_at', sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('event_id')
    )
    op.create_index('idx_billing_usage_event_id', 'billing_usage', ['event_id'], unique=True)
    op.create_index('idx_billing_usage_tenant_period', 'billing_usage', ['tenant_id', 'period'])
    op.create_index('idx_billing_usage_period', 'billing_usage', ['period'])
    
    # Kaspi profiles
    op.create_table(
        'billing_kaspi_profiles',
        sa.Column('tenant_id', sa.Text(), nullable=False),
        sa.Column('kaspi_token', sa.Text(), nullable=False),
        sa.Column('updated_at', sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint('tenant_id')
    )
    
    # ============================================
    # Events & Artifacts
    # ============================================
    
    # Events
    op.create_table(
        'events',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('event_type', sa.Text(), nullable=False),
        sa.Column('tenant_id', sa.Text(), nullable=True),
        sa.Column('artifact_id', sa.Text(), nullable=True),
        sa.Column('payload', sa.Text(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_events_event_type', 'events', ['event_type'])
    op.create_index('idx_events_tenant_id', 'events', ['tenant_id'])
    op.create_index('idx_events_artifact_id', 'events', ['artifact_id'])
    op.create_index('idx_events_created_at', 'events', ['created_at'])
    
    # Artifacts
    op.create_table(
        'artifacts',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('kind', sa.Text(), nullable=False),
        sa.Column('source', sa.Text(), nullable=False),
        sa.Column('tenant_id', sa.Text(), nullable=True),
        sa.Column('data', sa.Text(), nullable=False),
        sa.Column('created_at', sa.TIMESTAMP(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_artifacts_kind', 'artifacts', ['kind'])
    op.create_index('idx_artifacts_source', 'artifacts', ['source'])
    op.create_index('idx_artifacts_tenant_id', 'artifacts', ['tenant_id'])
    op.create_index('idx_artifacts_created_at', 'artifacts', ['created_at'])
    
    # ============================================
    # Storage (Inventory, Jobs, etc.)
    # ============================================
    
    # Inventory
    op.create_table(
        'inventory',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('id_1c', sa.Text(), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('sku', sa.Text(), nullable=True),
        sa.Column('unit', sa.Text(), nullable=True),
        sa.Column('category', sa.Text(), nullable=True),
        sa.Column('is_active', sa.Integer(), server_default='1', nullable=True),
        sa.Column('updated_at', sa.TIMESTAMP(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('id_1c')
    )
    op.create_index('idx_inventory_sku', 'inventory', ['sku'])
    op.create_index('idx_inventory_name', 'inventory', ['name'])
    op.create_index('idx_inventory_active', 'inventory', ['is_active'])
    
    # Product mappings
    op.create_table(
        'product_mappings',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('supplier_item_name', sa.Text(), nullable=False),
        sa.Column('supplier_sku', sa.Text(), nullable=True),
        sa.Column('my_item_id_1c', sa.Text(), nullable=False),
        sa.Column('my_item_name', sa.Text(), nullable=False),
        sa.Column('confidence', sa.Integer(), server_default='100', nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=True),
        sa.Column('last_used_at', sa.TIMESTAMP(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=True),
        sa.Column('usage_count', sa.Integer(), server_default='1', nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('supplier_item_name', 'supplier_sku', 'my_item_id_1c')
    )
    op.create_index('idx_mapping_supplier_name', 'product_mappings', ['supplier_item_name'])
    op.create_index('idx_mapping_supplier_sku', 'product_mappings', ['supplier_sku'])
    
    # Runs (statistics)
    op.create_table(
        'runs',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('job_id', sa.Text(), nullable=False),
        sa.Column('client_id', sa.Text(), nullable=True),
        sa.Column('filename', sa.Text(), nullable=False),
        sa.Column('hash', sa.Text(), nullable=False),
        sa.Column('cached', sa.Integer(), server_default='0', nullable=True),
        sa.Column('openai_requests', sa.Integer(), server_default='0', nullable=True),
        sa.Column('tokens_in', sa.Integer(), server_default='0', nullable=True),
        sa.Column('tokens_out', sa.Integer(), server_default='0', nullable=True),
        sa.Column('cost_usd', sa.Numeric(), server_default='0.0', nullable=True),
        sa.Column('elapsed_ms', sa.Integer(), server_default='0', nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_runs_job_id', 'runs', ['job_id'])
    op.create_index('idx_runs_client_id', 'runs', ['client_id'])
    op.create_index('idx_runs_hash', 'runs', ['hash'])
    op.create_index('idx_runs_created_at', 'runs', ['created_at'])
    
    # Jobs
    op.create_table(
        'jobs',
        sa.Column('job_id', sa.Text(), nullable=False),
        sa.Column('client_id', sa.Text(), nullable=True),
        sa.Column('filename', sa.Text(), nullable=False),
        sa.Column('file_path', sa.Text(), nullable=False),
        sa.Column('file_hash', sa.Text(), nullable=True),
        sa.Column('status', sa.Text(), server_default='queued', nullable=False),
        sa.Column('result_json', sa.Text(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('stats_json', sa.Text(), nullable=True),
        sa.Column('billing_json', sa.Text(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=True),
        sa.Column('started_at', sa.TIMESTAMP(), nullable=True),
        sa.Column('completed_at', sa.TIMESTAMP(), nullable=True),
        sa.PrimaryKeyConstraint('job_id')
    )
    op.create_index('idx_jobs_status', 'jobs', ['status'])
    op.create_index('idx_jobs_client_id', 'jobs', ['client_id'])
    op.create_index('idx_jobs_created_at', 'jobs', ['created_at'])


def downgrade() -> None:
    """Downgrade schema - drop all tables."""
    
    # Drop tables in reverse order (respecting foreign keys)
    op.drop_table('jobs')
    op.drop_table('runs')
    op.drop_table('product_mappings')
    op.drop_table('inventory')
    op.drop_table('artifacts')
    op.drop_table('events')
    op.drop_table('billing_kaspi_profiles')
    op.drop_table('billing_usage')
    op.drop_table('billing_rates')
    op.drop_table('billing_orders')
    op.drop_table('billing_tenant_payment_profiles')
    op.drop_table('billing_webhook_events')
    op.drop_table('tenant_subscriptions')
    op.drop_table('billing_plan_limits')
    op.drop_table('billing_plans')
