"""add_billing_plans

Revision ID: f1a2b3c4d5e6
Revises: e9f0a1b2c3d4
Create Date: 2026-01-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, Sequence[str], None] = 'e9f0a1b2c3d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema - add plans and tenant_plans tables for billing plans."""
    
    # Plans (тарифные планы: trial, pro, enterprise)
    op.create_table(
        'plans',
        sa.Column('id', sa.Text(), nullable=False),  # trial, pro, enterprise
        sa.Column('name', sa.Text(), nullable=False),  # Trial, Pro, Enterprise
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('quotas', sa.Text(), nullable=False),  # JSON: {"document_upload": 20, "invoice_extracted": 10, ...}
        sa.Column('price_minor', sa.Integer(), nullable=True),  # null для trial
        sa.Column('currency', sa.Text(), nullable=True),  # null для trial
        # Важно: default должен быть кросс-БД (SQLite/PostgreSQL).
        # sa.true() корректно компилируется в TRUE для PostgreSQL и в 1 для SQLite.
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Indexes for plans
    op.create_index('idx_plans_active', 'plans', ['active'])
    
    # Tenant Plans (назначенные планы для tenant)
    op.create_table(
        'tenant_plans',
        sa.Column('tenant_id', sa.Text(), nullable=False),
        sa.Column('plan_id', sa.Text(), nullable=False),
        sa.Column('started_at', sa.Text(), nullable=False),  # ISO timestamp
        sa.Column('expires_at', sa.Text(), nullable=True),  # ISO timestamp, null для paid планов
        sa.Column('created_at', sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint('tenant_id'),
        sa.ForeignKeyConstraint(['plan_id'], ['plans.id'], ondelete='RESTRICT')
    )
    
    # Indexes for tenant_plans
    op.create_index('idx_tenant_plans_tenant_id', 'tenant_plans', ['tenant_id'])
    op.create_index('idx_tenant_plans_plan_id', 'tenant_plans', ['plan_id'])
    op.create_index('idx_tenant_plans_expires_at', 'tenant_plans', ['expires_at'])
    
    # Seed default plans (кросс-БД, PostgreSQL-safe)
    import json
    from datetime import datetime

    now = datetime.now().isoformat()

    plans_table = sa.table(
        "plans",
        sa.column("id", sa.Text()),
        sa.column("name", sa.Text()),
        sa.column("description", sa.Text()),
        sa.column("quotas", sa.Text()),
        sa.column("price_minor", sa.Integer()),
        sa.column("currency", sa.Text()),
        sa.column("active", sa.Boolean()),
        sa.column("created_at", sa.Text()),
    )

    op.bulk_insert(
        plans_table,
        [
            {
                "id": "trial",
                "name": "Trial",
                "description": "14-day free trial",
                "quotas": json.dumps(
                    {"document_upload": 20, "invoice_extracted": 10, "page_processed": 50},
                    ensure_ascii=False,
                ),
                "price_minor": None,
                "currency": None,
                "active": True,
                "created_at": now,
            },
            {
                "id": "pro",
                "name": "Pro",
                "description": "Professional plan for small teams",
                "quotas": json.dumps(
                    {"document_upload": 100, "invoice_extracted": 50, "page_processed": 500},
                    ensure_ascii=False,
                ),
                "price_minor": 2900,
                "currency": "USD",
                "active": True,
                "created_at": now,
            },
            {
                "id": "enterprise",
                "name": "Enterprise",
                "description": "Enterprise plan with unlimited usage",
                "quotas": json.dumps(
                    {"document_upload": None, "invoice_extracted": None, "page_processed": None},
                    ensure_ascii=False,
                ),
                "price_minor": 9900,
                "currency": "USD",
                "active": True,
                "created_at": now,
            },
        ],
    )


def downgrade() -> None:
    """Downgrade schema - remove plans and tenant_plans tables."""
    
    # Drop indexes first
    op.drop_index('idx_tenant_plans_expires_at', table_name='tenant_plans')
    op.drop_index('idx_tenant_plans_plan_id', table_name='tenant_plans')
    op.drop_index('idx_tenant_plans_tenant_id', table_name='tenant_plans')
    op.drop_index('idx_plans_active', table_name='plans')
    
    # Drop tables
    op.drop_table('tenant_plans')
    op.drop_table('plans')
