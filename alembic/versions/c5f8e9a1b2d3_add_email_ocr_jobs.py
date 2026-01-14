"""add_email_ocr_jobs

Revision ID: c5f8e9a1b2d3
Revises: b34d4a0d7a8e
Create Date: 2026-01-14 22:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c5f8e9a1b2d3'
down_revision: Union[str, Sequence[str], None] = 'b34d4a0d7a8e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema - add email_ocr_jobs table for auto-OCR with idempotency and retries."""
    
    # Email OCR Jobs (для идемпотентности и ретраев auto-OCR после email ingestion)
    op.create_table(
        'email_ocr_jobs',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('tenant_id', sa.Text(), nullable=False),
        sa.Column('document_artifact_id', sa.Text(), nullable=False),
        sa.Column('idempotency_key', sa.Text(), nullable=False, unique=True),  # SHA256 для дедупа
        sa.Column('status', sa.Text(), nullable=False, server_default='queued'),  # queued|processing|done|failed|dead
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('max_retries', sa.Integer(), nullable=False, server_default='5'),
        sa.Column('next_run_at', sa.Text(), nullable=True),  # ISO format timestamp
        sa.Column('last_error', sa.Text(), nullable=True),  # Короткое сообщение об ошибке (без контента)
        sa.Column('invoice_artifact_id', sa.Text(), nullable=True),  # Созданный invoice artifact (после успешного OCR)
        sa.Column('created_at', sa.Text(), nullable=False),
        sa.Column('updated_at', sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('idempotency_key', name='uq_email_ocr_jobs_idempotency_key')
    )
    
    # Indexes for email_ocr_jobs
    op.create_index('idx_email_ocr_jobs_tenant_id', 'email_ocr_jobs', ['tenant_id'])
    op.create_index('idx_email_ocr_jobs_document_artifact_id', 'email_ocr_jobs', ['document_artifact_id'])
    op.create_index('idx_email_ocr_jobs_status', 'email_ocr_jobs', ['status'])
    op.create_index('idx_email_ocr_jobs_idempotency_key', 'email_ocr_jobs', ['idempotency_key'], unique=True)
    # Composite index для эффективного выбора jobs для обработки
    op.create_index(
        'idx_email_ocr_jobs_tenant_status_next_run',
        'email_ocr_jobs',
        ['tenant_id', 'status', 'next_run_at']
    )


def downgrade() -> None:
    """Downgrade schema - remove email_ocr_jobs table."""
    
    # Drop indexes first
    op.drop_index('idx_email_ocr_jobs_tenant_status_next_run', table_name='email_ocr_jobs')
    op.drop_index('idx_email_ocr_jobs_idempotency_key', table_name='email_ocr_jobs')
    op.drop_index('idx_email_ocr_jobs_status', table_name='email_ocr_jobs')
    op.drop_index('idx_email_ocr_jobs_document_artifact_id', table_name='email_ocr_jobs')
    op.drop_index('idx_email_ocr_jobs_tenant_id', table_name='email_ocr_jobs')
    
    # Drop table
    op.drop_table('email_ocr_jobs')
