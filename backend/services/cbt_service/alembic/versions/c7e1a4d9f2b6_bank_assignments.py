"""Bank assignments: which staff may use each question bank

Staff limited to their subjects ("assigned" reach) now see only the banks
they're given, plus the subject's questions that aren't in any bank.
Organisation-wide staff (admins) see every bank. Nobody is given a bank by
this migration, so existing teachers keep unbanked questions and lose banked
ones until an admin assigns them.

Revision ID: c7e1a4d9f2b6
Revises: b2d6f9a3c1e8
Create Date: 2026-10-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c7e1a4d9f2b6"
down_revision: Union[str, Sequence[str], None] = "b2d6f9a3c1e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bank_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "bank_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("question_banks.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assigned_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("bank_id", "user_id", name="uq_bank_assignments_bank_user"),
    )
    op.create_index("ix_bank_assignments_org_id", "bank_assignments", ["org_id"])
    op.create_index("ix_bank_assignments_bank_id", "bank_assignments", ["bank_id"])
    op.create_index("ix_bank_assignments_user_id", "bank_assignments", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_bank_assignments_user_id", table_name="bank_assignments")
    op.drop_index("ix_bank_assignments_bank_id", table_name="bank_assignments")
    op.drop_index("ix_bank_assignments_org_id", table_name="bank_assignments")
    op.drop_table("bank_assignments")
