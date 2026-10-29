"""exams.auto_mark_short_answers (off by default: short answers are marked by a person)

Revision ID: c4e8b2a6d913
Revises: a7d2e5f8c931
Create Date: 2026-10-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c4e8b2a6d913"
down_revision: Union[str, Sequence[str], None] = "a7d2e5f8c931"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "exams",
        sa.Column("auto_mark_short_answers", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("exams", "auto_mark_short_answers")
