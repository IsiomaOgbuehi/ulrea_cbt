from enum import Enum


class SubscriptionPlan(str, Enum):
    FREE = "free"
    PAID = "paid"


class SubscriptionStatus(str, Enum):
    PENDING = "pending"         # payment initiated, not confirmed
    ACTIVE = "active"           # payment confirmed
    EXPIRED = "expired"         # past expiry date
    CANCELLED = "cancelled"     # manually cancelled
    REFUNDED = "refunded"


class SubscribedBy(str, Enum):
    SELF = "self"               # student paid for themselves
    ORGANIZATION = "org"        # school subscribed on behalf of student