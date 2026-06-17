from datetime import datetime, timezone
from uuid import UUID, uuid4
from sqlmodel import SQLModel, Field, Column
from sqlalchemy import JSON
from .enum import OrgSubscriptionStatus, PlatformPlanStatus

class PlatformPlan(SQLModel, table=True):
    """
    Defines available platform subscription plans.
    Created and managed by platform admin (you).
    """
    __tablename__ = "platform_plans"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field(index=True)           # "Basic Monthly", "Standard Yearly"
    description: str | None = None
    status: PlatformPlanStatus = Field(default=PlatformPlanStatus.ACTIVE, index=True)

    # Pricing
    price: float = Field(default=0.0)
    currency: str = Field(default="USD")
    interval: str = Field(index=True)       # PlatformPlanInterval

    # Limits
    max_students: int | None = None         # None = unlimited
    max_staff: int | None = None            # None = unlimited
    max_exams: int | None = None            # None = unlimited
    max_cohorts: int | None = None          # None = unlimited

    # Trial
    trial_days: int = Field(default=0)      # 0 = no trial

    # Feature flags — control what each plan can do
    features: dict = Field(
        default_factory=dict,
        sa_column=Column(JSON)
    )
    # e.g. {
    #   "bulk_upload": true,
    #   "cohort_management": true,
    #   "advanced_analytics": false,
    #   "api_access": false,
    # }

    # Paystack
    paystack_plan_code: str | None = None   # for recurring billing

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class OrgPlatformSubscription(SQLModel, table=True):
    """
    Tracks an organization's platform subscription.
    One active subscription per org at a time.
    """
    __tablename__ = "org_platform_subscriptions"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    org_id: UUID = Field(
        foreign_key="organizations.id", index=True, nullable=False
    )
    plan_id: UUID = Field(
        foreign_key="platform_plans.id", nullable=False
    )
    status: OrgSubscriptionStatus = Field(
        default=OrgSubscriptionStatus.TRIAL, index=True
    )

    # Trial tracking
    is_trial: bool = Field(default=True)
    trial_ends_at: datetime | None = None

    # Billing period
    current_period_start: datetime | None = None
    current_period_end: datetime | None = None
    cancelled_at: datetime | None = None
    cancel_reason: str | None = None

    # Limits snapshot (from plan at time of subscription)
    max_students: int | None = None
    max_staff: int | None = None

    # Paystack
    paystack_reference: str | None = Field(default=None, index=True)
    paystack_subscription_code: str | None = None   # recurring
    paystack_transaction_id: str | None = None
    amount_paid: float | None = None
    currency: str = Field(default="USD")

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )