"""Shared question banks

question_banks.shared: an exam body offers the bank's questions to other
organisations (question library).

Revision ID: d4f7b2a9e3c1
Revises: c3e8a1f6d2b9
Create Date: 2026-11-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4f7b2a9e3c1"
down_revision: Union[str, Sequence[str], None] = "c3e8a1f6d2b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("question_banks", sa.Column("shared", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_index("ix_question_banks_shared", "question_banks", ["shared"])


def downgrade() -> None:
    op.drop_index("ix_question_banks_shared", table_name="question_banks")
    op.drop_column("question_banks", "shared")
