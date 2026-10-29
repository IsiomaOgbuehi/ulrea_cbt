"""Sync exam assignment status with the student's attempts

Assignments stayed "assigned" after students started or submitted, so the
student's exam list kept offering "Start exam" and exam counts showed nobody
had submitted. From now on the status follows the attempt; this brings
existing rows in line.

Revision ID: b8f3d1e6a274
Revises: c4e8b2a6d913
Create Date: 2026-10-29
"""
from typing import Sequence, Union

from alembic import op

revision: str = "b8f3d1e6a274"
down_revision: Union[str, Sequence[str], None] = "c4e8b2a6d913"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Finished an attempt -> submitted.
    op.execute("""
        UPDATE exam_assignments ea SET status = 'SUBMITTED'
        WHERE ea.status <> 'SUBMITTED' AND EXISTS (
            SELECT 1 FROM attempts a
            WHERE a.assignment_id = ea.id
              AND a.status IN ('SUBMITTED', 'SCORED', 'TERMINATED')
        )
    """)
    # Otherwise an attempt under way -> started.
    op.execute("""
        UPDATE exam_assignments ea SET status = 'STARTED'
        WHERE ea.status = 'ASSIGNED' AND EXISTS (
            SELECT 1 FROM attempts a
            WHERE a.assignment_id = ea.id AND a.status = 'STARTED'
        )
    """)


def downgrade() -> None:
    # Statuses are derived data; nothing to undo.
    pass
