"""Minutes of extra time staff gave an attempt, for showing them

attempts.extra_seconds also covers the time a reopened attempt was waiting
for the candidate, so it can't be shown as "time given".

Revision ID: f1d4b8e2c6a3
Revises: e6c2a9f4b1d7
Create Date: 2026-11-05
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f1d4b8e2c6a3"
down_revision: Union[str, Sequence[str], None] = "e6c2a9f4b1d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("attempts", sa.Column("extra_given_seconds", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("attempts", "extra_given_seconds")
