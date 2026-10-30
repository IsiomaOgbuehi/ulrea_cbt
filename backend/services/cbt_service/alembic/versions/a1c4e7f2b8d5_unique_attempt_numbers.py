"""One attempt row per attempt number per student per exam

Two "start exam" requests arriving at the same moment could each create an
attempt, leaving a student with two rows for the same attempt. The service now
locks the assignment while starting; this constraint makes the database refuse
a duplicate as well.

If duplicates already exist the migration stops and lists them, without
changing any data, so you can decide which row to keep.

Revision ID: a1c4e7f2b8d5
Revises: f5a8c2e7b9d3
Create Date: 2026-10-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1c4e7f2b8d5"
down_revision: Union[str, Sequence[str], None] = "f5a8c2e7b9d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DUPLICATES_SQL: str = """
    SELECT exam_id, student_id, attempt_number, count(*) AS rows,
           string_agg(id::text || ' (' || status || ', started ' || started_at::text || ')', '; '
                      ORDER BY started_at) AS attempts
    FROM attempts
    GROUP BY exam_id, student_id, attempt_number
    HAVING count(*) > 1
"""


def upgrade() -> None:
    dupes = op.get_bind().execute(sa.text(DUPLICATES_SQL)).all()
    if dupes:
        lines = "\n".join(
            f"  exam {d.exam_id} · student {d.student_id} · attempt {d.attempt_number}: {d.attempts}"
            for d in dupes
        )
        raise RuntimeError(
            "Some students have duplicate attempt rows. Nothing was changed. Decide which row to "
            "keep for each (usually the one with answers), delete the others together with their "
            "responses and violations, then run the migration again:\n" + lines
        )
    op.create_unique_constraint(
        "uq_attempts_exam_student_number", "attempts", ["exam_id", "student_id", "attempt_number"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_attempts_exam_student_number", "attempts", type_="unique")
