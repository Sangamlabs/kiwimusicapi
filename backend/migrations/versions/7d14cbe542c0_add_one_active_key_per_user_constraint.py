"""add_one_active_key_per_user_constraint

Revision ID: 7d14cbe542c0
Revises: d60224ac0e1b
Create Date: 2026-11-18 09:48:27.656329

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7d14cbe542c0'
down_revision: Union[str, Sequence[str], None] = 'd60224ac0e1b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index(
        'uq_api_keys_user_active',
        'api_keys',
        ['user_id'],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
        sqlite_where=sa.text("status = 'active'"),
        if_not_exists=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        'uq_api_keys_user_active',
        table_name='api_keys',
        postgresql_where=sa.text("status = 'active'"),
        sqlite_where=sa.text("status = 'active'"),
        if_exists=True,
    )
