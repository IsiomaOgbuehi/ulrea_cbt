from datetime import datetime, timezone
from sqlmodel import SQLModel, Field
from uuid import UUID, uuid4
from pydantic import EmailStr

from auth.database.schema.exam_subscription.enum import SubscriptionPlan
from .enums import OrganizationType, OrganizationVisibility

class OrganizationBase(SQLModel):
    name: str = Field(index=True)
    address: str | None = Field(default=None)
    email: EmailStr = Field(index=True, unique=True)
    phone: str | None = Field(index=True)
    organization_type: OrganizationType
    slug: str = Field(index=True, unique=True)   # e.g. "waec-ng" for search/URL
    logo_url: str | None = None
    website: str | None = None
    description: str | None = None
    verified: bool | None = False
    visibility: OrganizationVisibility = Field(
        default=OrganizationVisibility.PRIVATE,
        index=True,
    )
    owner_user_id: UUID | None = Field(
        default=None,
        foreign_key="users.id",
        index=True,
        nullable=False,
    )
    # Subscription settings (for public exam bodies)
    allow_self_subscription: bool = Field(default=False)
    require_subscription_approval: bool = Field(default=False)
    exam_body_trial_days: int = Field(default=0)

    # Exam body pricing
    subscription_plan: SubscriptionPlan = Field(default=SubscriptionPlan.FREE)   # free | paid
    subscription_price: float | None = None     # price per student
    subscription_currency: str = Field(default="NGN")
    subscription_duration_days: int | None = None   # None = lifetime
    paystack_plan_code: str | None = None           # Paystack plan if recurring

class OrganizationModel(OrganizationBase, table=True):
    __tablename__ = 'organizations'
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    created_at: datetime | None = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        index=True,
        nullable=False,
    )
    updated_at: datetime | None = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        index=True,
        nullable=False,
    )