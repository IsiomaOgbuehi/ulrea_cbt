"""dynamic difficulty levels per organisation

- new table difficulty_levels (filled with the built-in levels per
  organisation the first time it's read)
- items.difficulty: Postgres enum of NAMES ("MEDIUM") -> text holding the
  level key ("medium"), so organisations can add their own levels

Revision ID: e7f1c2d9a4b6
Revises: d5e8a1b2c3f4
Create Date: 2026-10-26
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e7f1c2d9a4b6"
down_revision: Union[str, Sequence[str], None] = "d5e8a1b2c3f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_NAMES: tuple[str, ...] = ("EASY", "MEDIUM", "HARD", "TEST", "CAT1", "CAT2", "CAT3", "EXAM")


def upgrade() -> None:
    op.create_table(
        "difficulty_levels",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("description", sa.String(length=300), nullable=True),
        sa.Column("colour", sa.String(length=20), server_default="slate", nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_built_in", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(length=20), server_default="active", nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "key", name="uq_difficulty_levels_org_key"),
    )
    op.create_index(op.f("ix_difficulty_levels_org_id"), "difficulty_levels", ["org_id"], unique=False)

    op.alter_column(
        "items", "difficulty",
        existing_type=postgresql.ENUM(*OLD_NAMES, name="itemdifficulty"),
        type_=sa.String(length=40),
        postgresql_using="lower(difficulty::text)",
        existing_nullable=False,
        server_default="medium",
    )
    op.execute("DROP TYPE IF EXISTS itemdifficulty")


def downgrade() -> None:
    itemdifficulty = postgresql.ENUM(*OLD_NAMES, name="itemdifficulty")
    itemdifficulty.create(op.get_bind(), checkfirst=True)
    op.alter_column("items", "difficulty", server_default=None)
    # Custom levels have no place in the old enum: they become MEDIUM.
    op.execute(
        "UPDATE items SET difficulty = 'medium' WHERE difficulty NOT IN "
        "('easy','medium','hard','test','cat1','cat2','cat3','exam')"
    )
    op.alter_column(
        "items", "difficulty",
        existing_type=sa.String(length=40),
        type_=itemdifficulty,
        postgresql_using="upper(difficulty)::itemdifficulty",
        existing_nullable=False,
    )
    op.drop_index(op.f("ix_difficulty_levels_org_id"), table_name="difficulty_levels")
    op.drop_table("difficulty_levels")
