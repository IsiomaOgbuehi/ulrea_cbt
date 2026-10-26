"""normalise exam_audit_logs.action to enum values

Rows written while the column was a Postgres enum hold the enum NAME
("CREATED"); everything since holds the value ("created"). Make them all
values so the audit log reads cleanly.

Revision ID: d5e8a1b2c3f4
Revises: a4d2f8c3e1b7
Create Date: 2026-10-26
"""
from typing import Sequence, Union

from alembic import op

revision: str = "d5e8a1b2c3f4"
down_revision: Union[str, Sequence[str], None] = "a4d2f8c3e1b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE exam_audit_logs SET action = 'exam_archived' WHERE action = 'ARCHIVED'")
    op.execute("UPDATE exam_audit_logs SET action = lower(action) WHERE action <> lower(action)")


def downgrade() -> None:
    # Data-only clean-up; the old upper-case names aren't needed back.
    pass
