from datetime import datetime, timezone
from uuid import UUID, uuid4
from sqlmodel import SQLModel, Field
from .enum import SubscriptionPlan, SubscriptionStatus, SubscribedBy

class ExamBodySubscription(SQLModel, table=True):
    """
    Tracks access to a paid exam body.
    Can be purchased by student directly or by their school on their behalf.

    For FREE exam bodies — no record needed, access is open.
    For PAID exam bodies — this record must exist and be ACTIVE.
    """
    __tablename__ = "exam_body_subscriptions"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    student_id: UUID = Field(foreign_key="users.id", index=True, nullable=False)
    exam_body_org_id: UUID = Field(
        foreign_key="organizations.id", index=True, nullable=False
    )
    org_id: UUID | None = Field(
        default=None, index=True
    )   # set if school paid on student's behalf

    plan: SubscriptionPlan = Field(default=SubscriptionPlan.FREE)
    status: SubscriptionStatus = Field(default=SubscriptionStatus.PENDING, index=True)
    subscribed_by: SubscribedBy = Field(default=SubscribedBy.SELF)

    # Paystack
    paystack_reference: str | None = Field(default=None, index=True, unique=True)
    paystack_transaction_id: str | None = Field(default=None)
    amount_paid: float | None = None
    currency: str = Field(default="NGN")

    # Validity
    starts_at: datetime | None = None
    expires_at: datetime | None = None     # None = lifetime access

    is_trial: bool = Field(default=False)
    trial_ends_at: datetime | None = None

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class BulkOrgSubscription(SQLModel, table=True):
    """
    When a school subscribes multiple students to an exam body at once.
    Groups individual ExamBodySubscription records under one payment.
    """
    __tablename__ = "bulk_org_subscriptions"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    org_id: UUID = Field(foreign_key="organizations.id", index=True, nullable=False)
    exam_body_org_id: UUID = Field(
        foreign_key="organizations.id", index=True, nullable=False
    )
    created_by: UUID = Field(nullable=False)

    student_count: int = Field(default=0)
    total_amount: float = Field(default=0.0)
    currency: str = Field(default="NGN")

    # Paystack — school pays one invoice for all students
    paystack_reference: str | None = Field(default=None, index=True, unique=True)
    paystack_transaction_id: str | None = Field(default=None)
    status: SubscriptionStatus = Field(default=SubscriptionStatus.PENDING, index=True)

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))