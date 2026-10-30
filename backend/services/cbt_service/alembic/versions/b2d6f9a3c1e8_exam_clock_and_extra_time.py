"""Exam clock starts when questions are sent; extra time per exam

- attempts.clock_started_at: when the questions were first sent to the
  student. Deadlines count from here, so slow page loading doesn't eat into
  their time. Existing attempts keep counting from when they started.
- exams.extra_time_seconds: added to every candidate's time (default 30 s).

Revision ID: b2d6f9a3c1e8
Revises: a1c4e7f2b8d5
Create Date: 2026-10-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b2d6f9a3c1e8"
down_revision: Union[str, Sequence[str], None] = "a1c4e7f2b8d5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("attempts", sa.Column("clock_started_at", sa.DateTime(timezone=True), nullable=True))
    # Attempts that already exist behave exactly as before.
    op.execute("UPDATE attempts SET clock_started_at = started_at WHERE status <> 'RESET'")
    op.add_column(
        "exams",
        sa.Column("extra_time_seconds", sa.Integer(), nullable=False, server_default="30"),
    )


def downgrade() -> None:
    op.drop_column("exams", "extra_time_seconds")
    op.drop_column("attempts", "clock_started_at")
