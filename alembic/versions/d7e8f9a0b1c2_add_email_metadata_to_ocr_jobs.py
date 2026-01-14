"""add_email_metadata_to_ocr_jobs

Revision ID: d7e8f9a0b1c2
Revises: c5f8e9a1b2d3
Create Date: 2026-01-14 23:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd7e8f9a0b1c2'
down_revision: Union[str, Sequence[str], None] = 'c5f8e9a1b2d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema - add email metadata columns to email_ocr_jobs for inbox feature."""
    
    # Добавляем метаданные email для inbox
    op.add_column('email_ocr_jobs', sa.Column('email_from', sa.Text(), nullable=True))
    op.add_column('email_ocr_jobs', sa.Column('email_to', sa.Text(), nullable=True))
    op.add_column('email_ocr_jobs', sa.Column('email_subject', sa.Text(), nullable=True))
    op.add_column('email_ocr_jobs', sa.Column('attachment_filename', sa.Text(), nullable=True))
    op.add_column('email_ocr_jobs', sa.Column('attachment_size', sa.Integer(), nullable=True))
    
    # Индекс для сортировки по дате получения (created_at используется как received_at)
    op.create_index('idx_email_ocr_jobs_tenant_created', 'email_ocr_jobs', ['tenant_id', 'created_at'])


def downgrade() -> None:
    """Downgrade schema - remove email metadata columns from email_ocr_jobs."""
    
    # Drop index
    op.drop_index('idx_email_ocr_jobs_tenant_created', table_name='email_ocr_jobs')
    
    # Drop columns
    op.drop_column('email_ocr_jobs', 'attachment_size')
    op.drop_column('email_ocr_jobs', 'attachment_filename')
    op.drop_column('email_ocr_jobs', 'email_subject')
    op.drop_column('email_ocr_jobs', 'email_to')
    op.drop_column('email_ocr_jobs', 'email_from')
