"""What an organisation calls a group of candidates ("Class", "Cohort"...)

org_settings.group_label / group_label_plural, empty until an admin chooses.
The app then uses "Class" for schools and "Cohort" otherwise.

Revision ID: d3a8f1c6e2b9
Revises: c7e1a4d9f2b6
Create Date: 2026-11-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d3a8f1c6e2b9"
down_revision: Union[str, Sequence[str], None] = "c7e1a4d9f2b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("org_settings", sa.Column("group_label", sa.String(length=40), nullable=True))
    op.add_column("org_settings", sa.Column("group_label_plural", sa.String(length=40), nullable=True))


def downgrade() -> None:
    op.drop_column("org_settings", "group_label_plural")
    op.drop_column("org_settings", "group_label")
