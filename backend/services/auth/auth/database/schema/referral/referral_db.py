"""
Referrals: a student's share link, who signed up through it, the
commissions they've earned (as a ledger) and their withdrawals.

The balance is never stored: it's the sum of the student's ledger entries.
Entries are only ever added, so every naira can be traced to a payment or a
withdrawal.
"""
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, Numeric, UniqueConstraint
from sqlmodel import Field, SQLModel


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ReferralCode(SQLModel, table=True):
    """A student's code for their share link (made the first time they ask)."""
    __tablename__ = "referral_codes"

    user_id: UUID = Field(foreign_key="users.id", primary_key=True)
    code: str = Field(index=True, unique=True, max_length=20)
    created_at: datetime = Field(default_factory=_now, sa_column=Column(DateTime(timezone=True), nullable=False))


class Referral(SQLModel, table=True):
    """Someone who signed up through a student's link. One referrer each."""
    __tablename__ = "referrals"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    referrer_id: UUID = Field(foreign_key="users.id", index=True)
    referred_id: UUID = Field(foreign_key="users.id", unique=True)
    created_at: datetime = Field(default_factory=_now, sa_column=Column(DateTime(timezone=True), nullable=False, index=True))


class EntryKind(str, Enum):
    COMMISSION = "commission"                    # a share of a referred friend's payment
    WITHDRAWAL = "withdrawal"                    # taken out when a withdrawal is asked for
    WITHDRAWAL_RETURNED = "withdrawal_returned"  # put back when a withdrawal is turned down
    ADJUSTMENT = "adjustment"                    # a correction by Juwazi


class CommissionEntry(SQLModel, table=True):
    """One line of a student's commission account; the balance is their sum."""
    __tablename__ = "commission_entries"
    __table_args__ = (UniqueConstraint("payment_id", name="uq_commission_entries_payment_id"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="users.id", index=True)      # whose account
    kind: EntryKind = Field(index=True)
    amount: Decimal = Field(sa_column=Column(Numeric(14, 2), nullable=False))   # + in, - out
    currency: str = Field(max_length=3)                              # always the base currency
    payment_id: UUID | None = Field(default=None, foreign_key="payments.id")    # commissions: once per payment
    referred_id: UUID | None = Field(default=None, index=True)      # the friend who paid
    withdrawal_id: UUID | None = Field(default=None, index=True)
    description: str = ""
    created_by: UUID | None = None                                   # adjustments
    created_at: datetime = Field(default_factory=_now, sa_column=Column(DateTime(timezone=True), nullable=False, index=True))


class WithdrawalStatus(str, Enum):
    REQUESTED = "requested"
    PAID = "paid"
    REJECTED = "rejected"


class AccountHolder(str, Enum):
    SELF = "self"            # the student, who confirmed they're 18 or older
    GUARDIAN = "guardian"    # a parent or guardian's account


class Withdrawal(SQLModel, table=True):
    """A student asking for their commissions; Juwazi pays by bank transfer."""
    __tablename__ = "withdrawals"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="users.id", index=True)
    amount: Decimal = Field(sa_column=Column(Numeric(14, 2), nullable=False))
    currency: str = Field(max_length=3)
    status: WithdrawalStatus = Field(default=WithdrawalStatus.REQUESTED, index=True)

    holder: AccountHolder
    adult_confirmed: bool = False                  # SELF: "I'm 18 or older"
    guardian_relationship: str | None = Field(default=None, max_length=40)   # GUARDIAN: "Mother", "Uncle"...
    account_name: str = Field(max_length=120)
    account_number: str = Field(max_length=20)
    bank_name: str = Field(max_length=120)

    payout_reference: str | None = Field(default=None, max_length=120)   # the transfer's reference, once paid
    reject_reason: str | None = Field(default=None, max_length=300)
    decided_by: UUID | None = None
    decided_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    created_at: datetime = Field(default_factory=_now, sa_column=Column(DateTime(timezone=True), nullable=False, index=True))
