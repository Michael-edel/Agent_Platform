"""Add agent_executions table

Revision ID: j5k6l7m8n9o0
Revises: i4j5k6l7m8n9
Create Date: 2026-01-15

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'j5k6l7m8n9o0'
down_revision: Union[str, None] = 'i4j5k6l7m8n9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'agent_executions',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('agent_sku_id', sa.String(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('idempotency_key', sa.String(), nullable=False),
        sa.Column('input_json', sa.Text(), nullable=False),
        sa.Column('result_json', sa.Text(), nullable=True),
        sa.Column('error_code', sa.String(), nullable=True),
        sa.Column('error_message', sa.String(), nullable=True),
        sa.Column('created_at', sa.String(), nullable=False),
        sa.Column('updated_at', sa.String(), nullable=False),
        sa.Column('started_at', sa.String(), nullable=True),
        sa.Column('finished_at', sa.String(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['agent_sku_id'], ['agent_skus.id'], name='fk_agent_executions_sku'),
        sa.UniqueConstraint('tenant_id', 'agent_sku_id', 'idempotency_key', name='uq_execution_idempotency'),
    )
    op.create_index('idx_agent_executions_tenant_created', 'agent_executions', ['tenant_id', 'created_at'])
    op.create_index('idx_agent_executions_status', 'agent_executions', ['status'])
    op.create_index('idx_agent_executions_tenant_id', 'agent_executions', ['tenant_id'])
    op.create_index('idx_agent_executions_agent_sku_id', 'agent_executions', ['agent_sku_id'])


def downgrade() -> None:
    op.drop_index('idx_agent_executions_agent_sku_id', table_name='agent_executions')
    op.drop_index('idx_agent_executions_tenant_id', table_name='agent_executions')
    op.drop_index('idx_agent_executions_status', table_name='agent_executions')
    op.drop_index('idx_agent_executions_tenant_created', table_name='agent_executions')
    op.drop_table('agent_executions')
