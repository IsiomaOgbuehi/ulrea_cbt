from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from auth.database.schema.billing.billing_db import CouponKind, PaymentPurpose, PaymentStatus, PeriodKind, Product


class CurrencyRead(BaseModel):
    code: str
    name: str
    enabled: bool
    base_per_unit: Decimal | None
    whole_units: bool


class BillingStatusRead(BaseModel):
    state: Literal["trial", "active", "expired", "none"]
    seats: int
    students: int
    ends_at: datetime | None
    product: Product
    period_days: int
    enforced: bool
    can_add_students: bool
    exempt: bool = False           # an exam body (public): billing doesn't apply


class CouponPrice(BaseModel):
    """The price after a coupon. Without one, discount is 0 and amount_due is the total."""
    coupon_code: str | None = None
    coupon_description: str | None = None
    discount: Decimal = Decimal(0)
    amount_due: Decimal


class SeatQuoteRead(CouponPrice):
    product: Product
    currency: str
    unit_amount: Decimal
    seats: int
    total: Decimal
    starts_at: datetime
    ends_at: datetime
    adding_to_current: bool        # places added to the period already running


class SeatCheckoutRequest(BaseModel):
    seats: int = Field(ge=1, le=100_000)
    currency: str = Field(min_length=3, max_length=3)
    coupon_code: str | None = Field(default=None, max_length=40)


class CheckoutRead(BaseModel):
    reference: str
    checkout_url: str
    amount: Decimal
    currency: str


class PaymentRead(BaseModel):
    id: UUID
    reference: str
    provider: str
    purpose: PaymentPurpose
    status: PaymentStatus
    org_id: UUID | None
    user_id: UUID
    amount: Decimal
    currency: str
    description: str
    channel: str | None
    failure_reason: str | None
    created_at: datetime
    paid_at: datetime | None


class PaymentPage(BaseModel):
    items: list[PaymentRead]
    total: int
    page: int
    page_size: int
    total_pages: int


# ------------------------------------------------------------ admin

class PriceRead(BaseModel):
    id: UUID
    product: Product
    exam_body_org_id: UUID | None
    currency: str
    amount: Decimal
    duration_days: int | None
    updated_at: datetime


class PriceUpsert(BaseModel):
    product: Product
    exam_body_org_id: UUID | None = None
    currency: str = Field(min_length=3, max_length=3)
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    duration_days: int | None = Field(default=None, ge=1, le=3650)


class CurrencyUpdate(BaseModel):
    enabled: bool | None = None
    base_per_unit: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=6)
    whole_units: bool | None = None


class AdminOrgRead(BaseModel):
    id: UUID
    name: str
    organization_type: str
    visibility: str
    created_at: datetime | None
    billing: BillingStatusRead


class AdminOrgPage(BaseModel):
    items: list[AdminOrgRead]
    total: int
    page: int
    page_size: int
    total_pages: int


class GrantPeriodRequest(BaseModel):
    seats: int = Field(ge=1, le=1_000_000)
    days: int = Field(ge=1, le=3650)
    kind: PeriodKind = PeriodKind.MANUAL
    note: str | None = Field(default=None, max_length=500)


class PeriodRead(BaseModel):
    id: UUID
    kind: PeriodKind
    seats: int
    starts_at: datetime
    ends_at: datetime
    payment_id: UUID | None
    note: str | None
    created_at: datetime


class ExamBodyRead(BaseModel):
    id: UUID
    name: str
    slug: str
    prices: list[PriceRead]


# ---- Schools paying for students' External Exams --------------------------------

class ExamAccessBodyRead(BaseModel):
    id: UUID
    name: str
    unit_amount: Decimal | None      # per student, in the platform's base currency (None: no price yet)
    currency: str
    days: int
    students_with_access: int        # of this organisation's students, paid access running now


class ExamAccessQuoteRequest(BaseModel):
    exam_body_org_id: UUID
    cohort_id: UUID | None = None                  # one class; leave out for every student
    student_ids: list[UUID] | None = Field(default=None, max_length=5000)
    currency: str = Field(default="NGN", min_length=3, max_length=3)
    coupon_code: str | None = Field(default=None, max_length=40)


class ExamAccessQuoteRead(CouponPrice):
    exam_body_name: str
    students: int
    already_have_access: int         # their days are added to the end of what they have
    unit_amount: Decimal
    total: Decimal
    currency: str
    days: int


# ---- Coupons (Juwazi admin) ---------------------------------------------------------

class CouponCreate(BaseModel):
    code: str = Field(min_length=3, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    description: str = Field(default="", max_length=300)
    kind: CouponKind
    percent_off: Decimal | None = Field(default=None, gt=0, le=100)
    amount_off: Decimal | None = Field(default=None, gt=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    purposes: list[PaymentPurpose] = Field(min_length=1)
    org_ids: list[UUID] = Field(default_factory=list, max_length=500)    # empty: every school
    once_per_payer: bool = False
    max_redemptions: int | None = Field(default=None, ge=1)
    expires_at: datetime | None = None


class CouponUpdate(BaseModel):
    """What can change after a coupon is made (not its discount)."""
    description: str | None = Field(default=None, max_length=300)
    org_ids: list[UUID] | None = Field(default=None, max_length=500)
    once_per_payer: bool | None = None
    max_redemptions: int | None = Field(default=None, ge=1)
    expires_at: datetime | None = None
    active: bool | None = None


class CouponSchool(BaseModel):
    id: UUID
    name: str


class CouponRead(BaseModel):
    id: UUID
    code: str
    description: str
    kind: CouponKind
    percent_off: Decimal | None
    amount_off: Decimal | None
    currency: str | None
    purposes: list[PaymentPurpose]
    org_ids: list[UUID]
    once_per_payer: bool
    max_redemptions: int | None
    expires_at: datetime | None
    active: bool
    created_at: datetime
    uses: int = 0                   # paid uses
    schools: list[CouponSchool] = []   # the org_ids, with names
