"""Institution IDs Juwazi generates for students who have none

org_memberships.institution_id_generated: the institution_id was made by
Juwazi (JW-XXXXXXXX). A unique index over generated ones guarantees no two
students anywhere share one; schools' own IDs stay unique per organisation.

Revision ID: f3a9c2d7b8e1
Revises: e2b7d4f9a1c6
Create Date: 2026-11-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f3a9c2d7b8e1"
down_revision: Union[str, Sequence[str], None] = "e2b7d4f9a1c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "org_memberships",
        sa.Column("institution_id_generated", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index(
        "uq_org_memberships_generated_institution_id", "org_memberships", ["institution_id"],
        unique=True, postgresql_where=sa.text("institution_id_generated"),
    )


def downgrade() -> None:
    op.drop_index("uq_org_memberships_generated_institution_id", table_name="org_memberships")
    op.drop_column("org_memberships", "institution_id_generated")
