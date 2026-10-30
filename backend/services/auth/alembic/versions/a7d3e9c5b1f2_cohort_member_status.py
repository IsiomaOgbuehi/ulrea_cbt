"""Cohort members get a status: active, graduated or left

Students can now be graduated, moved or removed one by one while the cohort
carries on. Rows are kept as history instead of being deleted, so the old
"one row per student per cohort" constraint only applies to active rows.
Every existing member becomes active; nothing else changes.

Revision ID: a7d3e9c5b1f2
Revises: f2c8d5a1b9e4
Create Date: 2026-10-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7d3e9c5b1f2"
down_revision: Union[str, Sequence[str], None] = "f2c8d5a1b9e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "cohort_members",
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
    )
    op.add_column("cohort_members", sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("cohort_members", sa.Column("ended_by", sa.Uuid(), nullable=True))
    op.add_column("cohort_members", sa.Column("end_note", sa.String(), nullable=True))
    op.create_index("ix_cohort_members_status", "cohort_members", ["status"])
    op.drop_constraint("uq_cohort_member", "cohort_members", type_="unique")
    op.create_index(
        "uq_cohort_member_active", "cohort_members", ["cohort_id", "student_id", "org_id"],
        unique=True, postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    # Only safe while each student has at most one row per cohort.
    op.drop_index("uq_cohort_member_active", table_name="cohort_members")
    op.execute("DELETE FROM cohort_members WHERE status <> 'active'")
    op.create_unique_constraint("uq_cohort_member", "cohort_members", ["cohort_id", "student_id", "org_id"])
    op.drop_index("ix_cohort_members_status", table_name="cohort_members")
    op.drop_column("cohort_members", "end_note")
    op.drop_column("cohort_members", "ended_by")
    op.drop_column("cohort_members", "ended_at")
    op.drop_column("cohort_members", "status")
