"""Referrals: students' share links, who signed up through them, commission
accounts (a ledger: the balance is the sum of its entries) and withdrawals.

Revision ID: b8d5f1c3e7a9
Revises: a7c4e9b2d5f8
Create Date: 2026-11-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b8d5f1c3e7a9"
down_revision: Union[str, Sequence[str], None] = "a7c4e9b2d5f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TZ = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "referral_codes",
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("code", sa.String(length=20), nullable=False),
        sa.Column("created_at", TZ, nullable=False),
    )
    op.create_index("ix_referral_codes_code", "referral_codes", ["code"], unique=True)

    op.create_table(
        "referrals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("referrer_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("referred_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", TZ, nullable=False),
        sa.UniqueConstraint("referred_id", name="uq_referrals_referred_id"),
    )
    op.create_index("ix_referrals_referrer_id", "referrals", ["referrer_id"])
    op.create_index("ix_referrals_created_at", "referrals", ["created_at"])

    op.create_table(
        "withdrawals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("holder", sa.String(), nullable=False),
        sa.Column("adult_confirmed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("guardian_relationship", sa.String(length=40), nullable=True),
        sa.Column("account_name", sa.String(length=120), nullable=False),
        sa.Column("account_number", sa.String(length=20), nullable=False),
        sa.Column("bank_name", sa.String(length=120), nullable=False),
        sa.Column("payout_reference", sa.String(length=120), nullable=True),
        sa.Column("reject_reason", sa.String(length=300), nullable=True),
        sa.Column("decided_by", sa.Uuid(), nullable=True),
        sa.Column("decided_at", TZ, nullable=True),
        sa.Column("created_at", TZ, nullable=False),
    )
    for c in ("user_id", "status", "created_at"):
        op.create_index(f"ix_withdrawals_{c}", "withdrawals", [c])

    op.create_table(
        "commission_entries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("payment_id", sa.Uuid(), sa.ForeignKey("payments.id"), nullable=True),
        sa.Column("referred_id", sa.Uuid(), nullable=True),
        sa.Column("withdrawal_id", sa.Uuid(), nullable=True),
        sa.Column("description", sa.String(), nullable=False, server_default=""),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", TZ, nullable=False),
        sa.UniqueConstraint("payment_id", name="uq_commission_entries_payment_id"),
    )
    for c in ("user_id", "kind", "referred_id", "withdrawal_id", "created_at"):
        op.create_index(f"ix_commission_entries_{c}", "commission_entries", [c])


def downgrade() -> None:
    op.drop_table("commission_entries")
    op.drop_table("withdrawals")
    op.drop_table("referrals")
    op.drop_table("referral_codes")
