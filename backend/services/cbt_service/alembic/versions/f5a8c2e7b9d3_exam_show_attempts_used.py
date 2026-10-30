"""Exams: show candidates how many attempts they've used (off by default)

Revision ID: f5a8c2e7b9d3
Revises: e2c7a9d4f1b6
Create Date: 2026-10-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f5a8c2e7b9d3"
down_revision: Union[str, Sequence[str], None] = "e2c7a9d4f1b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "exams",
        sa.Column("show_attempts_used", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("exams", "show_attempts_used")
