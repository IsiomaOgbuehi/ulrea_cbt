"""Give edited built-in admin roles the new "give staff banks" permission

Built-in roles left as they are follow the code defaults, which already
include it. Admin roles an organisation has edited keep a stored list, so add
the new permission there too (it didn't exist when they were edited).

Revision ID: f2c8d5a1b9e4
Revises: d4b1f7a2c8e3
Create Date: 2026-10-30
"""
import json
from typing import Any, Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f2c8d5a1b9e4"
down_revision: Union[str, Sequence[str], None] = "d4b1f7a2c8e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PERMISSION: str = "banks.assign_staff"


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT id, permissions FROM org_roles WHERE is_system AND key = 'admin' AND permissions IS NOT NULL"
    )).all()
    for role_id, permissions in rows:
        grants: dict[str, Any] = permissions if isinstance(permissions, dict) else json.loads(permissions)
        if PERMISSION in grants:
            continue
        grants[PERMISSION] = "all"
        conn.execute(
            sa.text("UPDATE org_roles SET permissions = CAST(:p AS JSON) WHERE id = :id"),
            {"p": json.dumps(grants), "id": role_id},
        )


def downgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT id, permissions FROM org_roles WHERE is_system AND key = 'admin' AND permissions IS NOT NULL"
    )).all()
    for role_id, permissions in rows:
        grants: dict[str, Any] = permissions if isinstance(permissions, dict) else json.loads(permissions)
        if grants.pop(PERMISSION, None) is not None:
            conn.execute(
                sa.text("UPDATE org_roles SET permissions = CAST(:p AS JSON) WHERE id = :id"),
                {"p": json.dumps(grants), "id": role_id},
            )
