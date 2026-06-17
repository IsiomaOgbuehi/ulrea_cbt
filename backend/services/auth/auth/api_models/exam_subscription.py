# auth/api_models/exam_subscription.py
from pydantic import BaseModel
from uuid import UUID
from datetime import datetime
from auth.database.schema.exam_subscription.exam_subscription_db import (
    SubscriptionStatus, SubscribedBy
)


class InitiateSubscriptionRequest(BaseModel):
    """Student initiates payment for a paid exam body."""
    exam_body_org_id: UUID
    callback_url: str           # frontend URL Paystack redirects to after payment


class BulkSubscribeStudentsRequest(BaseModel):
    """School subscribes multiple students to an exam body."""
    exam_body_org_id: UUID
    student_ids: list[UUID]
    callback_url: str


class PaystackWebhookPayload(BaseModel):
    event: str
    data: dict


class SubscriptionRead(BaseModel):
    id: UUID
    student_id: UUID
    exam_body_org_id: UUID
    plan: str
    status: str
    subscribed_by: str
    amount_paid: float | None
    currency: str
    starts_at: datetime | None
    expires_at: datetime | None
    created_at: datetime


class ExamAccessCheck(BaseModel):
    """Response when checking if student can access an exam."""
    can_access: bool
    reason: str | None = None   # why they can't access if False
    subscription_id: UUID | None = None