"""
Billing: payments (any provider), prices, currencies, platform settings and
the periods an organisation has paid for (or is trialling).
"""
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, Column, DateTime, Numeric, UniqueConstraint
from sqlmodel import Field, SQLModel


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Product(str, Enum):
    SCHOOL_SEAT = "school_seat"          # one student for one term
    TRAINING_SEAT = "training_seat"      # one candidate for 30 days
    EXAM_BODY_ACCESS = "exam_body_access"   # one student, one exam body, for its access period


class PaymentPurpose(str, Enum):
    ORG_SEATS = "org_seats"
    EXAM_BODY_ACCESS = "exam_body_access"
    EXAM_BODY_BULK = "exam_body_bulk"


class PaymentStatus(str, Enum):
    PENDING = "pending"          # checkout started
    SUCCESSFUL = "successful"    # verified with the provider and fulfilled
    FAILED = "failed"
    MISMATCH = "mismatch"        # provider says paid, but not the amount/currency we asked for


class Payment(SQLModel, table=True):
    __tablename__ = "payments"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    reference: str = Field(index=True, unique=True)      # ours, sent to the provider
    provider: str = Field(index=True)
    provider_txn_id: str | None = Field(default=None, index=True)
    purpose: PaymentPurpose = Field(index=True)
    status: PaymentStatus = Field(default=PaymentStatus.PENDING, index=True)

    org_id: UUID | None = Field(default=None, index=True)
    user_id: UUID = Field(index=True)                    # who paid
    amount: Decimal = Field(sa_column=Column(Numeric(14, 2), nullable=False))
    currency: str = Field(max_length=3)
    description: str = ""
    # What to give once paid (seats and period, exam body, students...).
    details: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    channel: str | None = None                           # card, bank transfer, ussd, mobile money...
    failure_reason: str | None = None

    created_at: datetime = Field(default_factory=_now, sa_column=Column(DateTime(timezone=True), nullable=False, index=True))
    paid_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))


class Price(SQLModel, table=True):
    """A price set by Juwazi. Per exam body for exam access (empty = the default
    for every body). A price in a currency other than the base overrides converting."""
    __tablename__ = "prices"
    __table_args__ = (UniqueConstraint("product", "exam_body_org_id", "currency", name="uq_price"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    product: Product = Field(index=True)
    exam_body_org_id: UUID | None = Field(default=None, index=True)
    currency: str = Field(max_length=3)
    amount: Decimal = Field(sa_column=Column(Numeric(14, 2), nullable=False))
    # How long one purchase lasts (exam access per body); None = the platform setting.
    duration_days: int | None = None
    updated_by: UUID | None = None
    updated_at: datetime = Field(default_factory=_now, sa_column=Column(DateTime(timezone=True), nullable=False))


class Currency(SQLModel, table=True):
    """A currency offered at checkout. Prices are converted from the base
    currency (NGN) at `base_per_unit` unless a price is set in it directly."""
    __tablename__ = "currencies"

    code: str = Field(primary_key=True, max_length=3)
    name: str
    enabled: bool = Field(default=False, index=True)
    # How many base-currency units one unit of this currency is worth (e.g. 1 USD = 1600 NGN).
    base_per_unit: Decimal | None = Field(default=None, sa_column=Column(Numeric(18, 6), nullable=True))
    # Currencies without everyday subunits (UGX, RWF, XOF...) are charged in whole units.
    whole_units: bool = Field(default=False)
    updated_by: UUID | None = None
    updated_at: datetime = Field(default_factory=_now, sa_column=Column(DateTime(timezone=True), nullable=False))


class PlatformSetting(SQLModel, table=True):
    """Settings Juwazi changes from its admin area (trial length, term length...)."""
    __tablename__ = "platform_settings"

    key: str = Field(primary_key=True, max_length=64)
    value: Any = Field(sa_column=Column(JSON, nullable=False))
    updated_by: UUID | None = None
    updated_at: datetime = Field(default_factory=_now, sa_column=Column(DateTime(timezone=True), nullable=False))


class PeriodKind(str, Enum):
    TRIAL = "trial"
    PAID = "paid"
    MANUAL = "manual"        # granted by Juwazi (enterprise, invoice, goodwill)


class OrgBillingPeriod(SQLModel, table=True):
    """Student places an organisation has for a stretch of time. Buying more
    places mid-period adds another row ending on the same day."""
    __tablename__ = "org_billing_periods"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    org_id: UUID = Field(index=True)
    kind: PeriodKind = Field(index=True)
    seats: int
    starts_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False, index=True))
    ends_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False, index=True))
    payment_id: UUID | None = Field(default=None, index=True)
    note: str | None = None
    created_by: UUID | None = None
    created_at: datetime = Field(default_factory=_now, sa_column=Column(DateTime(timezone=True), nullable=False))
