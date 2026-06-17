from enum import Enum


class PlatformPlanInterval(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"
    LIFETIME = "lifetime"


class PlatformPlanStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class OrgSubscriptionStatus(str, Enum):
    TRIAL = "trial"
    ACTIVE = "active"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    PAST_DUE = "past_due"