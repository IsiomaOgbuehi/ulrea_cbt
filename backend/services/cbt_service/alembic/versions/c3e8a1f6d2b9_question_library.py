"""Question library

items.copied_from_item_id: a question an organisation copied from an exam
body's shared questions (External Exams), pointing at the original.

Revision ID: c3e8a1f6d2b9
Revises: a9c4e7b2d5f8
Create Date: 2026-11-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c3e8a1f6d2b9"
down_revision: Union[str, Sequence[str], None] = "a9c4e7b2d5f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("items", sa.Column("copied_from_item_id", sa.Uuid(), nullable=True))
    op.create_index("ix_items_copied_from_item_id", "items", ["copied_from_item_id"])


def downgrade() -> None:
    op.drop_index("ix_items_copied_from_item_id", table_name="items")
    op.drop_column("items", "copied_from_item_id")
