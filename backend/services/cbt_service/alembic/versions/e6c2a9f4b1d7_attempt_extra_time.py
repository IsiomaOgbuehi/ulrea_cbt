"""Extra time for one attempt (after a power cut or similar)

attempts.extra_seconds: added to the exam's duration for this attempt only.
attempts.resume_seconds: set when an attempt that ran out of time is reopened;
the student gets this many seconds counted from when they come back.

Revision ID: e6c2a9f4b1d7
Revises: d3a8f1c6e2b9
Create Date: 2026-11-05
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e6c2a9f4b1d7"
down_revision: Union[str, Sequence[str], None] = "d3a8f1c6e2b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("attempts", sa.Column("extra_seconds", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("attempts", sa.Column("resume_seconds", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("attempts", "resume_seconds")
    op.drop_column("attempts", "extra_seconds")
