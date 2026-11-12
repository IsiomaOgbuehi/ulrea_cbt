"""External Exams practice

exams.external_listed: an exam body's exam offered to subscribers.
exam_assignments.practice / attempts.practice: practice from External Exams,
kept apart from real exams. A student may have one real and one practice
assignment for the same exam. practice_summaries: progress per student and
exam (each retake replaces the previous practice attempt).

Revision ID: a9c4e7b2d5f8
Revises: f1d4b8e2c6a3
Create Date: 2026-11-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a9c4e7b2d5f8"
down_revision: Union[str, Sequence[str], None] = "f1d4b8e2c6a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("exams", sa.Column("external_listed", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_index("ix_exams_external_listed", "exams", ["external_listed"])
    op.add_column("exam_assignments", sa.Column("practice", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_index("ix_exam_assignments_practice", "exam_assignments", ["practice"])
    op.drop_constraint("uq_exam_assignments_exam_student", "exam_assignments", type_="unique")
    op.create_unique_constraint("uq_exam_assignments_exam_student", "exam_assignments", ["exam_id", "student_id", "practice"])
    op.add_column("attempts", sa.Column("practice", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_index("ix_attempts_practice", "attempts", ["practice"])
    op.create_table(
        "practice_summaries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("exam_id", sa.Uuid(), sa.ForeignKey("exams.id"), nullable=False),
        sa.Column("student_id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("times_taken", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("best_percentage", sa.Float(), nullable=True),
        sa.Column("last_percentage", sa.Float(), nullable=True),
        sa.Column("last_taken_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("exam_id", "student_id", name="uq_practice_summaries_exam_student"),
    )
    for c in ("exam_id", "student_id", "org_id"):
        op.create_index(f"ix_practice_summaries_{c}", "practice_summaries", [c])


def downgrade() -> None:
    op.drop_table("practice_summaries")
    op.drop_index("ix_attempts_practice", table_name="attempts")
    op.drop_column("attempts", "practice")
    op.drop_constraint("uq_exam_assignments_exam_student", "exam_assignments", type_="unique")
    op.execute("DELETE FROM exam_assignments WHERE practice")
    op.create_unique_constraint("uq_exam_assignments_exam_student", "exam_assignments", ["exam_id", "student_id"])
    op.drop_index("ix_exam_assignments_practice", table_name="exam_assignments")
    op.drop_column("exam_assignments", "practice")
    op.drop_index("ix_exams_external_listed", table_name="exams")
    op.drop_column("exams", "external_listed")
