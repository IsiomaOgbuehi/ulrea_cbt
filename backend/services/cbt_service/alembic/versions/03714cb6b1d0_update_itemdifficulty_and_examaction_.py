"""update itemDifficulty and ExamAction Enums

Revision ID: 03714cb6b1d0
Revises: c1cc4897b572
Create Date: 2026-10-08 13:57:54.928324

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "03714cb6b1d0"
down_revision: Union[str, Sequence[str], None] = "c1cc4897b572"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_LABELS: dict[str, list[str]] = {
    "itemdifficulty": ["TEST", "CAT1", "CAT2", "CAT3", "EXAM"],
    "examaction": ["ATTEMPT_RESET", "EXAM_ARCHIVED"],
}


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for type_name, labels in NEW_LABELS.items():
            for label in labels:
                op.execute(
                    f"ALTER TYPE {type_name} ADD VALUE IF NOT EXISTS '{label}'"
                )


def downgrade() -> None:
    pass