# auth/api_models/platform_subscription.py
from pydantic import BaseModel
from uuid import UUID
from datetime import datetime
from typing import Any


class PlatformPlanRead(BaseModel):
    id: UUID
    name: str
    description: str | None
    price: float
    currency: str
    interval: str
    max_students: int | None
    max_staff: int | None
    max_exams: int | None
    trial_days: int
    features: dict
    status: str


class SubscribeToPlatformRequest(BaseModel):
    plan_id: UUID
    callback_url: str
    start_trial: bool = True            # whether to start trial first


class OrgSubscriptionRead(BaseModel):
    id: UUID
    org_id: UUID
    plan: PlatformPlanRead
    status: str
    is_trial: bool
    trial_ends_at: datetime | None
    current_period_start: datetime | None
    current_period_end: datetime | None
    max_students: int | None
    max_staff: int | None
    amount_paid: float | None
    currency: str
    created_at: datetime


class OrgLimitsCheck(BaseModel):
    """Used to check if org can add more students/staff."""
    can_add_students: bool
    can_add_staff: bool
    current_students: int
    max_students: int | None
    students_remaining: int | None      # None = unlimited
    current_staff: int
    max_staff: int | None
    staff_remaining: int | None
    plan_name: str
    subscription_status: str


class ExamBodyPricingRead(BaseModel):
    """What students see when browsing paid exam bodies."""
    org_id: UUID
    name: str
    price: float
    currency: str
    duration_days: int | None
    trial_days: int
    is_free: bool