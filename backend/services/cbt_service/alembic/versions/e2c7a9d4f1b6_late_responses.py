"""Late answers: answers that reach the server after an attempt's time ran out

Time is strict: they never change a score by themselves. Staff see how late
each one was and accept or reject it.

Revision ID: e2c7a9d4f1b6
Revises: b8f3d1e6a274
Create Date: 2026-10-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e2c7a9d4f1b6"
down_revision: Union[str, Sequence[str], None] = "b8f3d1e6a274"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "late_responses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("attempt_id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("exam_item_id", sa.Uuid(), nullable=False),
        sa.Column("answer", sa.JSON(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("seconds_late", sa.Float(), nullable=False, server_default="0"),
        sa.Column("last_on_time_save_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("decided_by", sa.Uuid(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["attempt_id"], ["attempts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_late_responses_attempt_id", "late_responses", ["attempt_id"])
    op.create_index("ix_late_responses_org_id", "late_responses", ["org_id"])
    op.create_index("ix_late_responses_status", "late_responses", ["status"])


def downgrade() -> None:
    op.drop_index("ix_late_responses_status", table_name="late_responses")
    op.drop_index("ix_late_responses_org_id", table_name="late_responses")
    op.drop_index("ix_late_responses_attempt_id", table_name="late_responses")
    op.drop_table("late_responses")
