"""add_api_key_column

Revision ID: 9c21ef4190ba
Revises: 7d14cbe542c0
Create Date: 2026-11-18 10:04:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '9c21ef4190ba'
down_revision: Union[str, Sequence[str], None] = '7d14cbe542c0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.add_column('api_keys', sa.Column('api_key', sa.String(length=64), nullable=True))

def downgrade() -> None:
    op.drop_column('api_keys', 'api_key')

