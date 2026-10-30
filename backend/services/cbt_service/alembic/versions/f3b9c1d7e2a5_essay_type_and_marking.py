"""essay question type, marking guide, word limit, review time

- itemtype enum: + ESSAY (the enum stores names)
- items.marking_guide (text, markers only), items.word_limit
- responses.reviewed_at

Revision ID: f3b9c1d7e2a5
Revises: e7f1c2d9a4b6
Create Date: 2026-10-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f3b9c1d7e2a5"
down_revision: Union[str, Sequence[str], None] = "e7f1c2d9a4b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ADD VALUE can't run inside a transaction block on older Postgres.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE itemtype ADD VALUE IF NOT EXISTS 'ESSAY'")
    op.add_column("items", sa.Column("marking_guide", sa.Text(), nullable=True))
    op.add_column("items", sa.Column("word_limit", sa.Integer(), nullable=True))
    op.add_column("responses", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("responses", "reviewed_at")
    op.drop_column("items", "word_limit")
    op.drop_column("items", "marking_guide")
    # Postgres can't drop an enum value; essays become short answers instead.
    op.execute("UPDATE items SET item_type = 'SHORT_ANSWER' WHERE item_type = 'ESSAY'")
