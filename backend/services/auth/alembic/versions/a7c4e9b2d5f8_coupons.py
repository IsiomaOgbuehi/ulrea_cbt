"""Coupons: discount codes Juwazi gives out to subsidise payments

coupons: the code, its discount (a percent off the payment, or an amount off
each student / place in one currency), which payments and schools it's for,
and its limits. coupon_redemptions: each payment a coupon was used on; one
counts once its payment goes through (or while its checkout is open).

Revision ID: a7c4e9b2d5f8
Revises: f3a9c2d7b8e1
Create Date: 2026-11-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7c4e9b2d5f8"
down_revision: Union[str, Sequence[str], None] = "f3a9c2d7b8e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TZ = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "coupons",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("description", sa.String(), nullable=False, server_default=""),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("percent_off", sa.Numeric(5, 2), nullable=True),
        sa.Column("amount_off", sa.Numeric(14, 2), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("purposes", sa.JSON(), nullable=False),
        sa.Column("org_ids", sa.JSON(), nullable=False),
        sa.Column("once_per_payer", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("max_redemptions", sa.Integer(), nullable=True),
        sa.Column("expires_at", TZ, nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", TZ, nullable=False),
    )
    op.create_index("ix_coupons_code", "coupons", ["code"], unique=True)
    op.create_index("ix_coupons_active", "coupons", ["active"])

    op.create_table(
        "coupon_redemptions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("coupon_id", sa.Uuid(), sa.ForeignKey("coupons.id"), nullable=False),
        sa.Column("payment_id", sa.Uuid(), sa.ForeignKey("payments.id"), nullable=False),
        sa.Column("payer", sa.String(length=60), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("discount", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", TZ, nullable=False),
        sa.UniqueConstraint("payment_id", name="uq_coupon_redemptions_payment_id"),
    )
    for c in ("coupon_id", "payer", "org_id", "user_id", "status", "created_at"):
        op.create_index(f"ix_coupon_redemptions_{c}", "coupon_redemptions", [c])


def downgrade() -> None:
    op.drop_table("coupon_redemptions")
    op.drop_table("coupons")
