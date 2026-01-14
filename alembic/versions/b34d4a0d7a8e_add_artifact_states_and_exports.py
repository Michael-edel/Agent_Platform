"""add_artifact_states_and_exports

Revision ID: b34d4a0d7a8e
Revises: d01c4c0f81ed
Create Date: 2026-01-14 18:35:30.512033

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b34d4a0d7a8e'
down_revision: Union[str, Sequence[str], None] = 'd01c4c0f81ed'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema - add artifact_states and exports tables for UI/product layer."""
    
    # ============================================
    # Product/UI Layer: Artifact States
    # ============================================
    
    # Artifact states (UI statuses, workflow, filtering)
    op.create_table(
        'artifact_states',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('tenant_id', sa.Text(), nullable=False),
        sa.Column('artifact_id', sa.Text(), nullable=False),
        sa.Column('ui_status', sa.Text(), nullable=False, server_default='pending'),
        sa.Column('source_artifact_id', sa.Text(), nullable=True),  # invoice → document link
        sa.Column('error_code', sa.Text(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('confirmed_at', sa.Text(), nullable=True),  # ISO format timestamp
        sa.Column('exported_at', sa.Text(), nullable=True),  # ISO format timestamp
        sa.Column('export_target', sa.Text(), nullable=True),  # 'excel', 'json', '1c', etc.
        sa.Column('created_at', sa.Text(), nullable=False),
        sa.Column('updated_at', sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('artifact_id')  # One state per artifact
    )
    
    # Indexes for artifact_states
    op.create_index('idx_artifact_states_tenant_id', 'artifact_states', ['tenant_id'])
    op.create_index('idx_artifact_states_artifact_id', 'artifact_states', ['artifact_id'], unique=True)
    op.create_index('idx_artifact_states_ui_status', 'artifact_states', ['ui_status'])
    op.create_index('idx_artifact_states_source_artifact_id', 'artifact_states', ['source_artifact_id'])
    op.create_index('idx_artifact_states_created_at', 'artifact_states', ['created_at'])
    
    # ============================================
    # Product/UI Layer: Exports
    # ============================================
    
    # Exports (track invoice exports for audit and reproducibility)
    op.create_table(
        'exports',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('tenant_id', sa.Text(), nullable=False),
        sa.Column('artifact_id', sa.Text(), nullable=False),
        sa.Column('export_type', sa.Text(), nullable=False),  # 'excel', 'json', '1c', etc.
        sa.Column('file_id', sa.Text(), nullable=True),  # Reference to stored file
        sa.Column('file_path', sa.Text(), nullable=True),  # Local path (for dev/staging)
        sa.Column('export_config', sa.Text(), nullable=True),  # JSON config for export
        sa.Column('status', sa.Text(), nullable=False, server_default='pending'),  # 'pending', 'completed', 'failed'
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.Text(), nullable=False),
        sa.Column('completed_at', sa.Text(), nullable=True),  # ISO format timestamp
        sa.PrimaryKeyConstraint('id')
    )
    
    # Indexes for exports
    op.create_index('idx_exports_tenant_id', 'exports', ['tenant_id'])
    op.create_index('idx_exports_artifact_id', 'exports', ['artifact_id'])
    op.create_index('idx_exports_export_type', 'exports', ['export_type'])
    op.create_index('idx_exports_status', 'exports', ['status'])
    op.create_index('idx_exports_created_at', 'exports', ['created_at'])


def downgrade() -> None:
    """Downgrade schema - remove artifact_states and exports tables."""
    
    # Drop indexes first
    op.drop_index('idx_exports_created_at', table_name='exports')
    op.drop_index('idx_exports_status', table_name='exports')
    op.drop_index('idx_exports_export_type', table_name='exports')
    op.drop_index('idx_exports_artifact_id', table_name='exports')
    op.drop_index('idx_exports_tenant_id', table_name='exports')
    
    op.drop_index('idx_artifact_states_created_at', table_name='artifact_states')
    op.drop_index('idx_artifact_states_source_artifact_id', table_name='artifact_states')
    op.drop_index('idx_artifact_states_ui_status', table_name='artifact_states')
    op.drop_index('idx_artifact_states_artifact_id', table_name='artifact_states')
    op.drop_index('idx_artifact_states_tenant_id', table_name='artifact_states')
    
    # Drop tables
    op.drop_table('exports')
    op.drop_table('artifact_states')
