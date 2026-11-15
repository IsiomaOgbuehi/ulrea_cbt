from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field

from auth.database.schema.referral.referral_db import AccountHolder, EntryKind, WithdrawalStatus


class ReferralInvite(BaseModel):
    """Shown on the sign-up page a link leads to."""
    referrer_first_name: str


class ReferralSummary(BaseModel):
    code: str
    link: str
    percent: float                 # of each friend's payment
    months: int                    # 0: every payment, forever
    friends: int
    balance: Decimal
    total_earned: Decimal
    currency: str
    min_withdrawal: Decimal
    can_withdraw: bool
    waiting_withdrawal: "WithdrawalRead | None" = None


class CommissionEntryRead(BaseModel):
    id: UUID
    kind: EntryKind
    amount: Decimal
    currency: str
    description: str
    created_at: datetime


class CommissionEntryPage(BaseModel):
    items: list[CommissionEntryRead]
    total: int
    page: int
    page_size: int
    total_pages: int


class WithdrawalRequest(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    holder: AccountHolder
    adult_confirmed: bool = False
    guardian_relationship: str | None = Field(default=None, max_length=40)
    account_name: str = Field(min_length=2, max_length=120)
    account_number: str = Field(min_length=6, max_length=20)
    bank_name: str = Field(min_length=2, max_length=120)


class WithdrawalRead(BaseModel):
    id: UUID
    amount: Decimal
    currency: str
    status: WithdrawalStatus
    holder: AccountHolder
    guardian_relationship: str | None
    account_name: str
    account_number_last4: str
    bank_name: str
    payout_reference: str | None
    reject_reason: str | None
    created_at: datetime
    decided_at: datetime | None


class AdminWithdrawalRead(WithdrawalRead):
    """What Juwazi needs to pay it."""
    account_number: str
    adult_confirmed: bool
    student_id: UUID
    student_name: str
    student_email: str | None
    balance_after: Decimal          # the student's balance now


class AdminWithdrawalPage(BaseModel):
    items: list[AdminWithdrawalRead]
    total: int
    page: int
    page_size: int
    total_pages: int


class MarkPaid(BaseModel):
    reference: str = Field(min_length=2, max_length=120)


class RejectWithdrawal(BaseModel):
    reason: str = Field(min_length=3, max_length=300)


ReferralSummary.model_rebuild()
