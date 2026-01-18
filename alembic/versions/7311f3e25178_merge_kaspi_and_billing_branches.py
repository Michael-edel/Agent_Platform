"""merge kaspi and billing branches

Revision ID: 7311f3e25178
Revises: b3c4d5e6f7g8, y3z4a5b6
Create Date: 2026-01-18 12:23:39.145929

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7311f3e25178'
down_revision: Union[str, Sequence[str], None] = ('b3c4d5e6f7g8', 'y3z4a5b6')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
