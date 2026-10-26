"""add periods and org settings

Revision ID: a4d2f8c3e1b7
Revises: 7c1e5a9d2b40
Create Date: 2026-10-26 04:00:00.000000

Organisation-wide periods (session > term, year > cycle...) and an
org_settings table. Purely additive: nullable period_id columns on items and
exams, so existing questions and exams are untouched (NULL = no period).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a4d2f8c3e1b7'
down_revision: Union[str, Sequence[str], None] = '7c1e5a9d2b40'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'periods',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('org_id', sa.Uuid(), nullable=False),
        sa.Column('parent_id', sa.Uuid(), nullable=True),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('status', sa.String(length=20), server_default='active', nullable=False),
        sa.Column('starts_on', sa.Date(), nullable=True),
        sa.Column('ends_on', sa.Date(), nullable=True),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('created_by', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['parent_id'], ['periods.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_periods_org_id'), 'periods', ['org_id'], unique=False)
    op.create_index(op.f('ix_periods_parent_id'), 'periods', ['parent_id'], unique=False)

    op.create_table(
        'org_settings',
        sa.Column('org_id', sa.Uuid(), nullable=False),
        sa.Column('period_parent_label', sa.String(length=40), nullable=False),
        sa.Column('period_child_label', sa.String(length=40), nullable=False),
        sa.Column('current_period_id', sa.Uuid(), nullable=True),
        sa.Column('updated_by', sa.Uuid(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['current_period_id'], ['periods.id']),
        sa.PrimaryKeyConstraint('org_id'),
    )

    op.add_column('items', sa.Column('period_id', sa.Uuid(), nullable=True))
    op.create_index(op.f('ix_items_period_id'), 'items', ['period_id'], unique=False)
    op.create_foreign_key('fk_items_period_id_periods', 'items', 'periods', ['period_id'], ['id'])

    op.add_column('exams', sa.Column('period_id', sa.Uuid(), nullable=True))
    op.create_index(op.f('ix_exams_period_id'), 'exams', ['period_id'], unique=False)
    op.create_foreign_key('fk_exams_period_id_periods', 'exams', 'periods', ['period_id'], ['id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_exams_period_id_periods', 'exams', type_='foreignkey')
    op.drop_index(op.f('ix_exams_period_id'), table_name='exams')
    op.drop_column('exams', 'period_id')

    op.drop_constraint('fk_items_period_id_periods', 'items', type_='foreignkey')
    op.drop_index(op.f('ix_items_period_id'), table_name='items')
    op.drop_column('items', 'period_id')

    op.drop_table('org_settings')
    op.drop_index(op.f('ix_periods_parent_id'), table_name='periods')
    op.drop_index(op.f('ix_periods_org_id'), table_name='periods')
    op.drop_table('periods')
