"""cascade user fks on cohort tables

Revision ID: e83dafde3811
Revises: 6268442b1c55
Create Date: 2026-10-06 10:08:30.343644

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e83dafde3811'
down_revision: Union[str, Sequence[str], None] = '6268442b1c55'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

FK_STUDENT = "cohort_members_student_id_fkey"
FK_TEACHER = "teacher_cohort_assignments_teacher_id_fkey"
FK_ASSIGNED_BY = "teacher_cohort_assignments_assigned_by_fkey"


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint(FK_STUDENT, "cohort_members", type_="foreignkey")
    op.create_foreign_key(
        FK_STUDENT, "cohort_members", "users",
        ["student_id"], ["id"], ondelete="CASCADE",
    )

    op.alter_column(
        "teacher_cohort_assignments", "assigned_by",
        existing_type=sa.UUID(), nullable=True,
    )
    op.drop_constraint(FK_TEACHER, "teacher_cohort_assignments", type_="foreignkey")
    op.drop_constraint(FK_ASSIGNED_BY, "teacher_cohort_assignments", type_="foreignkey")
    op.create_foreign_key(
        FK_TEACHER, "teacher_cohort_assignments", "users",
        ["teacher_id"], ["id"], ondelete="CASCADE",
    )
    op.create_foreign_key(
        FK_ASSIGNED_BY, "teacher_cohort_assignments", "users",
        ["assigned_by"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(FK_ASSIGNED_BY, "teacher_cohort_assignments", type_="foreignkey")
    op.drop_constraint(FK_TEACHER, "teacher_cohort_assignments", type_="foreignkey")
    op.create_foreign_key(
        FK_TEACHER, "teacher_cohort_assignments", "users",
        ["teacher_id"], ["id"],
    )
    # Rows orphaned by SET NULL would block NOT NULL; backfill with a placeholder.
    op.execute(
        "UPDATE teacher_cohort_assignments "
        "SET assigned_by = teacher_id WHERE assigned_by IS NULL"
    )
    op.alter_column(
        "teacher_cohort_assignments", "assigned_by",
        existing_type=sa.UUID(), nullable=False,
    )
    op.create_foreign_key(
        FK_ASSIGNED_BY, "teacher_cohort_assignments", "users",
        ["assigned_by"], ["id"],
    )

    op.drop_constraint(FK_STUDENT, "cohort_members", type_="foreignkey")
    op.create_foreign_key(
        FK_STUDENT, "cohort_members", "users",
        ["student_id"], ["id"],
    )