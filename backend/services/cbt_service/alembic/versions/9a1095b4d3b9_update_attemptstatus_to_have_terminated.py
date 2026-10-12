"""update attemptstatus to have TERMINATED

Revision ID: 9a1095b4d3b9
Revises: 03714cb6b1d0
Create Date: 2026-10-08 21:50:18.520634

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '9a1095b4d3b9'
down_revision: Union[str, Sequence[str], None] = '03714cb6b1d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add TERMINATED to the attemptstatus enum."""
    # Run outside Alembic's transaction: on Postgres < 12, ADD VALUE can't run
    # inside a transaction block, and on any version the new value can't be
    # used until the transaction that added it commits.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE attemptstatus ADD VALUE IF NOT EXISTS 'TERMINATED'")


def downgrade() -> None:
    """No-op: Postgres can't drop a single value from an enum type.

    Leaving the extra label in place is harmless. Removing it would mean
    recreating the type and rewriting the column, which isn't worth it here.
    """
    pass