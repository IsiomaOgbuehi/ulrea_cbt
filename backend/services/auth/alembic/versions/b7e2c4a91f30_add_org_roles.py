"""add org roles (editable permissions)

Revision ID: b7e2c4a91f30
Revises: e83dafde3811
Create Date: 2026-10-26 11:30:00.000000

Purely additive: a new org_roles table and a nullable org_memberships.role_id.
No rows are created: built-in roles keep using the code defaults until an
organisation edits one, so existing staff behave exactly as before.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b7e2c4a91f30'
down_revision: Union[str, Sequence[str], None] = 'e83dafde3811'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'org_roles',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('org_id', sa.Uuid(), nullable=False),
        sa.Column('key', sa.String(length=60), nullable=False),
        sa.Column('name', sa.String(length=80), nullable=False),
        sa.Column('description', sa.String(), nullable=True),
        sa.Column('base_role', sa.String(length=20), nullable=False),
        sa.Column('is_system', sa.Boolean(), nullable=False),
        sa.Column('permissions', sa.JSON(), nullable=True),
        sa.Column('created_by', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('org_id', 'key', name='uq_org_roles_org_key'),
    )
    op.create_index(op.f('ix_org_roles_org_id'), 'org_roles', ['org_id'], unique=False)

    op.add_column('org_memberships', sa.Column('role_id', sa.Uuid(), nullable=True))
    op.create_index(op.f('ix_org_memberships_role_id'), 'org_memberships', ['role_id'], unique=False)
    op.create_foreign_key(
        'fk_org_memberships_role_id_org_roles', 'org_memberships', 'org_roles', ['role_id'], ['id']
    )


def downgrade() -> None:
    op.drop_constraint('fk_org_memberships_role_id_org_roles', 'org_memberships', type_='foreignkey')
    op.drop_index(op.f('ix_org_memberships_role_id'), table_name='org_memberships')
    op.drop_column('org_memberships', 'role_id')
    op.drop_index(op.f('ix_org_roles_org_id'), table_name='org_roles')
    op.drop_table('org_roles')
