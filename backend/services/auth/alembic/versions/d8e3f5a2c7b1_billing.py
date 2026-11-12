"""Billing: payments (any provider), prices, currencies, platform settings,
organisations' trial/paid periods, and the platform admin flag

Every existing organisation gets a 30-day trial with places for at least its
current students, so nobody is blocked when limits are switched on.

Revision ID: d8e3f5a2c7b1
Revises: b4e7a1c9d3f6
Create Date: 2026-11-10
"""
from datetime import datetime, timedelta, timezone
from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "d8e3f5a2c7b1"
down_revision: Union[str, Sequence[str], None] = "b4e7a1c9d3f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TZ = sa.DateTime(timezone=True)

# Offered at checkout once Juwazi sets a rate and switches them on in admin.
CURRENCIES: list[tuple[str, str, bool]] = [
    ("NGN", "Nigerian naira", False), ("USD", "US dollar", False), ("GBP", "British pound", False),
    ("EUR", "Euro", False), ("GHS", "Ghanaian cedi", False), ("KES", "Kenyan shilling", False),
    ("ZAR", "South African rand", False), ("UGX", "Ugandan shilling", True), ("TZS", "Tanzanian shilling", True),
    ("RWF", "Rwandan franc", True), ("XOF", "West African CFA franc", True), ("XAF", "Central African CFA franc", True),
    ("ZMW", "Zambian kwacha", False), ("EGP", "Egyptian pound", False), ("MWK", "Malawian kwacha", True),
    ("SLL", "Sierra Leonean leone", True), ("MAD", "Moroccan dirham", False), ("ETB", "Ethiopian birr", False),
]

# Prices from juwazi.com/pricing, in naira. (Enum columns hold the member
# names, as SQLModel writes them.)
PRICES: list[tuple[str, str]] = [
    ("SCHOOL_SEAT", "3000.00"), ("TRAINING_SEAT", "1000.00"), ("EXAM_BODY_ACCESS", "1000.00"),
]


def upgrade() -> None:
    op.add_column("users", sa.Column("is_platform_admin", sa.Boolean(), nullable=False, server_default=sa.false()))

    op.create_table(
        "payments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("reference", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("provider_txn_id", sa.String(), nullable=True),
        sa.Column("purpose", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("description", sa.String(), nullable=False, server_default=""),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("channel", sa.String(), nullable=True),
        sa.Column("failure_reason", sa.String(), nullable=True),
        sa.Column("created_at", TZ, nullable=False),
        sa.Column("paid_at", TZ, nullable=True),
    )
    op.create_index("ix_payments_reference", "payments", ["reference"], unique=True)
    for c in ("provider", "provider_txn_id", "purpose", "status", "org_id", "user_id", "created_at"):
        op.create_index(f"ix_payments_{c}", "payments", [c])

    op.create_table(
        "prices",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("product", sa.String(), nullable=False),
        sa.Column("exam_body_org_id", sa.Uuid(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("duration_days", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("updated_at", TZ, nullable=False),
        sa.UniqueConstraint("product", "exam_body_org_id", "currency", name="uq_price"),
    )
    op.create_index("ix_prices_product", "prices", ["product"])
    op.create_index("ix_prices_exam_body_org_id", "prices", ["exam_body_org_id"])
    # The unique constraint treats NULLs as distinct: keep one default price per product and currency.
    op.create_index(
        "uq_price_default", "prices", ["product", "currency"], unique=True,
        postgresql_where=sa.text("exam_body_org_id IS NULL"),
    )

    op.create_table(
        "currencies",
        sa.Column("code", sa.String(length=3), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("base_per_unit", sa.Numeric(18, 6), nullable=True),
        sa.Column("whole_units", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("updated_at", TZ, nullable=False),
    )
    op.create_index("ix_currencies_enabled", "currencies", ["enabled"])

    op.create_table(
        "platform_settings",
        sa.Column("key", sa.String(length=64), primary_key=True),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("updated_at", TZ, nullable=False),
    )

    op.create_table(
        "org_billing_periods",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("seats", sa.Integer(), nullable=False),
        sa.Column("starts_at", TZ, nullable=False),
        sa.Column("ends_at", TZ, nullable=False),
        sa.Column("payment_id", sa.Uuid(), nullable=True),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", TZ, nullable=False),
    )
    for c in ("org_id", "kind", "starts_at", "ends_at", "payment_id"):
        op.create_index(f"ix_org_billing_periods_{c}", "org_billing_periods", [c])

    now = datetime.now(timezone.utc)
    conn = op.get_bind()
    currencies = sa.table(
        "currencies", sa.column("code", sa.String), sa.column("name", sa.String), sa.column("enabled", sa.Boolean),
        sa.column("whole_units", sa.Boolean), sa.column("updated_at", TZ),
    )
    op.bulk_insert(currencies, [
        {"code": code, "name": name, "enabled": code == "NGN", "whole_units": whole, "updated_at": now}
        for code, name, whole in CURRENCIES
    ])
    prices = sa.table(
        "prices", sa.column("id", sa.Uuid), sa.column("product", sa.String), sa.column("currency", sa.String),
        sa.column("amount", sa.Numeric), sa.column("updated_at", TZ),
    )
    op.bulk_insert(prices, [
        {"id": uuid4(), "product": product, "currency": "NGN", "amount": amount, "updated_at": now}
        for product, amount in PRICES
    ])

    # Existing organisations: a 30-day trial sized for their students (at least 10).
    rows = conn.execute(sa.text(
        "SELECT o.id, COUNT(m.id) FROM organizations o "
        "LEFT JOIN org_memberships m ON m.org_id = o.id AND m.role = 'STUDENT' "
        "AND m.status IN ('ACTIVE', 'PENDING') GROUP BY o.id"
    )).all()
    periods = sa.table(
        "org_billing_periods", sa.column("id", sa.Uuid), sa.column("org_id", sa.Uuid), sa.column("kind", sa.String),
        sa.column("seats", sa.Integer), sa.column("starts_at", TZ), sa.column("ends_at", TZ),
        sa.column("note", sa.String), sa.column("created_at", TZ),
    )
    if rows:
        op.bulk_insert(periods, [
            {"id": uuid4(), "org_id": org_id, "kind": "TRIAL", "seats": max(10, int(n)), "starts_at": now,
             "ends_at": now + timedelta(days=30), "note": "Trial for organisations that joined before billing", "created_at": now}
            for org_id, n in rows
        ])


def downgrade() -> None:
    op.drop_table("org_billing_periods")
    op.drop_table("platform_settings")
    op.drop_table("currencies")
    op.drop_index("uq_price_default", table_name="prices")
    op.drop_table("prices")
    op.drop_table("payments")
    op.drop_column("users", "is_platform_admin")
