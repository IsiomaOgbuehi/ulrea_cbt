"""When a student first opened External Exams (for the menu's "New" badge)

Revision ID: e2b7d4f9a1c6
Revises: d8e3f5a2c7b1
Create Date: 2026-11-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e2b7d4f9a1c6"
down_revision: Union[str, Sequence[str], None] = "d8e3f5a2c7b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("external_exams_seen_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "external_exams_seen_at")
