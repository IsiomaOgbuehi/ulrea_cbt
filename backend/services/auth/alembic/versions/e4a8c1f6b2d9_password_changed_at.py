"""When each person last changed their password

Sign-ins that began before it end at their next token refresh.

Revision ID: e4a8c1f6b2d9
Revises: c9e6a2d4f8b1
Create Date: 2026-11-17
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e4a8c1f6b2d9"
down_revision: Union[str, Sequence[str], None] = "c9e6a2d4f8b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("password_changed_at", sa.TIMESTAMP(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "password_changed_at")
