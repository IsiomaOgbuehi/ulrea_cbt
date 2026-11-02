"""Students choose a new security question after an admin reset

users.security_reset_required is set when an admin gives a student a
temporary answer, and cleared when the student picks their own question and
answer at their next sign-in. Existing users start as false.

Revision ID: c9f2b6e4a8d1
Revises: a7d3e9c5b1f2
Create Date: 2026-10-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c9f2b6e4a8d1"
down_revision: Union[str, Sequence[str], None] = "a7d3e9c5b1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("security_reset_required", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("users", "security_reset_required")
