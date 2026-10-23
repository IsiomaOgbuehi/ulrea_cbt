"""add composite index for item listing and counts

Revision ID: 2fabab7d6959
Revises: 3d328734fff0
Create Date: 2026-10-22 19:50:00.000000

Covers every hot item query: they all filter on subject_id + org_id + status,
and the item list sorts by created_at DESC
(a btree is scanned backwards for DESC, so no DESC needed in the index) (list_items, its count, the
subject list's has_items filter and per-subject item counts).

Built CONCURRENTLY so writes to `items` aren't blocked while it builds.
CONCURRENTLY can't run inside a transaction, hence the autocommit block.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '2fabab7d6959'
down_revision: Union[str, Sequence[str], None] = '3d328734fff0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEX_NAME: str = 'ix_items_subject_org_status_created'


def upgrade() -> None:
    """Upgrade schema."""
    with op.get_context().autocommit_block():
        op.create_index(
            INDEX_NAME,
            'items',
            ['subject_id', 'org_id', 'status', 'created_at'],
            unique=False,
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.get_context().autocommit_block():
        op.drop_index(
            INDEX_NAME,
            table_name='items',
            postgresql_concurrently=True,
            if_exists=True,
        )
