"""exam_items marks from their questions; exams.total_marks recomputed; responses.exam_item_id fixed

Adding questions to an exam never copied the question's marks, so every
exam_items row has the default (1 mark, no negative marks) and exams'
total_marks is just the question count. Scoring used the question's own
marks, so copy those in (what results were based on) and recompute totals.

The exam page also sent the question's id as responses.exam_item_id; point
each answer at its exam's entry for that question.

Revision ID: a7d2e5f8c931
Revises: f3b9c1d7e2a5
Create Date: 2026-10-29
"""
from typing import Sequence, Union

from alembic import op

revision: str = "a7d2e5f8c931"
down_revision: Union[str, Sequence[str], None] = "f3b9c1d7e2a5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE exam_items AS ei
        SET marks = i.marks, negative_marks = i.negative_marks
        FROM items AS i
        WHERE i.id = ei.item_id
          AND (ei.marks IS DISTINCT FROM i.marks OR ei.negative_marks IS DISTINCT FROM i.negative_marks)
        """
    )
    op.execute(
        """
        UPDATE exams AS e
        SET total_marks = COALESCE(t.total, 0)
        FROM (
            SELECT e2.id, SUM(ei.marks) AS total
            FROM exams e2 LEFT JOIN exam_items ei ON ei.exam_id = e2.id
            GROUP BY e2.id
        ) AS t
        WHERE t.id = e.id AND e.total_marks IS DISTINCT FROM COALESCE(t.total, 0)
        """
    )
    op.execute(
        """
        UPDATE responses AS r
        SET exam_item_id = ei.id
        FROM attempts AS a, exam_items AS ei
        WHERE a.id = r.attempt_id
          AND ei.exam_id = a.exam_id
          AND ei.item_id = r.item_id
          AND r.exam_item_id IS DISTINCT FROM ei.id
        """
    )


def downgrade() -> None:
    # Data fix only; the old values were wrong defaults.
    pass

