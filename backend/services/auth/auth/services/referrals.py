"""
Referrals: students share a link; friends who sign up through it are theirs,
and the student earns a share of what those friends pay.

Money is kept as a ledger (`CommissionEntry`) in the base currency: a
commission for each paid payment, minus withdrawals, plus withdrawals turned
down. The balance is the sum; nothing is ever edited.
"""
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_DOWN, Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, func, select

from auth.database.schema.billing.billing_db import Currency, Payment, PaymentPurpose
from auth.database.schema.referral.referral_db import (
    AccountHolder, CommissionEntry, EntryKind, Referral, ReferralCode, Withdrawal, WithdrawalStatus,
)
from auth.database.schema.user.user_db import UserModel
from auth.services.billing import payments
from auth.services.billing.platform_settings import PlatformSettings, get_settings

logger: logging.Logger = logging.getLogger(__name__)

_ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ23456789"     # no 0/O, 1/I/L, U
_LENGTH = 8
CENT = Decimal("0.01")

# Payments a friend makes for themselves (what earns a commission).
EARNING_PURPOSES: frozenset[PaymentPurpose] = frozenset({PaymentPurpose.EXAM_BODY_ACCESS})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(when: datetime) -> datetime:
    return when if when.tzinfo is not None else when.replace(tzinfo=timezone.utc)


def normalise(code: str) -> str:
    return code.strip().upper()


# ---- Codes and sign-ups -------------------------------------------------------------

def code_for(session: Session, user_id: UUID) -> str:
    """The student's referral code, made the first time it's needed."""
    row = session.get(ReferralCode, user_id)
    if row is not None:
        return row.code
    for _ in range(20):
        candidate = "".join(secrets.choice(_ALPHABET) for _ in range(_LENGTH))
        if session.exec(select(ReferralCode).where(ReferralCode.code == candidate)).first() is None:
            row = ReferralCode(user_id=user_id, code=candidate)
            session.add(row)
            try:
                session.commit()
            except IntegrityError:          # made at the same moment elsewhere
                session.rollback()
                existing = session.get(ReferralCode, user_id)
                if existing is not None:
                    return existing.code
                continue
            return candidate
    raise HTTPException(status_code=500, detail="Couldn't make a referral code. Please try again.")


def referrer_by_code(session: Session, code: str) -> UserModel | None:
    row = session.exec(select(ReferralCode).where(ReferralCode.code == normalise(code))).first()
    return session.get(UserModel, row.user_id) if row is not None else None


def attach(session: Session, referred: UserModel, code: str | None) -> Referral | None:
    """Someone signed up with a referral code: they're the code owner's friend.
    An unknown code or one's own is ignored, so it never stops a sign-up."""
    if not code or not code.strip():
        return None
    referrer = referrer_by_code(session, code)
    if referrer is None or referrer.id == referred.id:
        return None
    if session.exec(select(Referral).where(Referral.referred_id == referred.id)).first() is not None:
        return None
    referral = Referral(referrer_id=referrer.id, referred_id=referred.id)
    session.add(referral)
    session.commit()
    return referral


# ---- Earning -----------------------------------------------------------------------

def _to_base(session: Session, amount: Decimal, currency: str, base: str) -> Decimal | None:
    if currency.upper() == base.upper():
        return amount
    row = session.get(Currency, currency.upper())
    if row is None or not row.base_per_unit:
        return None
    return amount * Decimal(row.base_per_unit)


def commission_for(session: Session, payment: Payment, settings: PlatformSettings) -> tuple[Referral, Decimal] | None:
    """The referral this payment earns for, and how much (in the base currency)."""
    if payment.purpose not in EARNING_PURPOSES or Decimal(payment.amount) <= 0 or settings.referral_percent <= 0:
        return None
    referral = session.exec(select(Referral).where(Referral.referred_id == payment.user_id)).first()
    if referral is None:
        return None
    if settings.referral_months > 0:
        # Months as 30 days: simple and predictable.
        if _now() > _aware(referral.created_at) + timedelta(days=30 * settings.referral_months):
            return None
    base_amount = _to_base(session, Decimal(payment.amount), payment.currency, settings.base_currency)
    if base_amount is None:
        logger.warning("No rate to convert %s for referral commission on %s", payment.currency, payment.reference)
        return None
    share = (base_amount * Decimal(str(settings.referral_percent)) / Decimal(100)).quantize(CENT, rounding=ROUND_DOWN)
    return (referral, share) if share > 0 else None


def _credit(session: Session, payment: Payment) -> None:
    """A payment went through: if it's a referred friend's, its commission goes
    in the referrer's account (once; same transaction as the payment)."""
    settings = get_settings(session)
    found = commission_for(session, payment, settings)
    if found is None:
        return
    referral, share = found
    if session.exec(select(CommissionEntry.id).where(CommissionEntry.payment_id == payment.id)).first() is not None:
        return
    friend = session.get(UserModel, payment.user_id)
    session.add(CommissionEntry(
        user_id=referral.referrer_id, kind=EntryKind.COMMISSION, amount=share, currency=settings.base_currency,
        payment_id=payment.id, referred_id=payment.user_id,
        description=f"{settings.referral_percent:g}% of {friend.firstname if friend else 'a friend'}'s payment: {payment.description}"[:300],
    ))


payments.on_paid(_credit)


# ---- The account -------------------------------------------------------------------

def balance(session: Session, user_id: UUID) -> Decimal:
    total = session.exec(select(func.coalesce(func.sum(CommissionEntry.amount), 0)).where(CommissionEntry.user_id == user_id)).one()
    return Decimal(total).quantize(CENT)


def total_earned(session: Session, user_id: UUID) -> Decimal:
    total = session.exec(
        select(func.coalesce(func.sum(CommissionEntry.amount), 0))
        .where(CommissionEntry.user_id == user_id, CommissionEntry.kind == EntryKind.COMMISSION)
    ).one()
    return Decimal(total).quantize(CENT)


def friends_count(session: Session, user_id: UUID) -> int:
    return int(session.exec(select(func.count()).select_from(Referral).where(Referral.referrer_id == user_id)).one())


def open_withdrawal(session: Session, user_id: UUID) -> Withdrawal | None:
    return session.exec(
        select(Withdrawal).where(Withdrawal.user_id == user_id, Withdrawal.status == WithdrawalStatus.REQUESTED)
    ).first()


@dataclass(frozen=True)
class PayoutAccount:
    holder: AccountHolder
    adult_confirmed: bool
    guardian_relationship: str | None
    account_name: str
    account_number: str
    bank_name: str


def request_withdrawal(session: Session, user_id: UUID, amount: Decimal, account: PayoutAccount) -> Withdrawal:
    """Take the amount out of the balance now (so it can't be asked for twice)
    and leave it for Juwazi to pay. Row-locks the student while checking."""
    settings = get_settings(session)
    if account.holder == AccountHolder.SELF and not account.adult_confirmed:
        raise HTTPException(
            status_code=400,
            detail="Confirm you're 18 or older, or give a parent or guardian's account instead.",
        )
    if account.holder == AccountHolder.GUARDIAN and not (account.guardian_relationship or "").strip():
        raise HTTPException(status_code=400, detail="Say how the account holder is related to you (e.g. Mother).")
    if not account.account_number.isdigit() or not 6 <= len(account.account_number) <= 20:
        raise HTTPException(status_code=400, detail="Enter the account number using digits only.")
    amount = amount.quantize(CENT)
    if amount < settings.referral_min_withdrawal:
        raise HTTPException(
            status_code=400,
            detail=f"The least you can withdraw is {settings.referral_min_withdrawal:,} {settings.base_currency}.",
        )
    session.exec(select(UserModel).where(UserModel.id == user_id).with_for_update()).one()
    if open_withdrawal(session, user_id) is not None:
        session.rollback()
        raise HTTPException(status_code=409, detail="You already have a withdrawal waiting to be paid.")
    available = balance(session, user_id)
    if amount > available:
        session.rollback()
        raise HTTPException(status_code=400, detail=f"You have {available:,} {settings.base_currency} to withdraw.")
    guardian = account.holder == AccountHolder.GUARDIAN
    withdrawal = Withdrawal(
        user_id=user_id, amount=amount, currency=settings.base_currency, holder=account.holder,
        adult_confirmed=account.adult_confirmed and not guardian,
        guardian_relationship=((account.guardian_relationship or "").strip() or None) if guardian else None,
        account_name=account.account_name.strip(), account_number=account.account_number,
        bank_name=account.bank_name.strip(),
    )
    session.add(withdrawal)
    session.flush()
    session.add(CommissionEntry(
        user_id=user_id, kind=EntryKind.WITHDRAWAL, amount=-amount, currency=settings.base_currency,
        withdrawal_id=withdrawal.id, description=f"Withdrawal to {withdrawal.bank_name} ••••{withdrawal.account_number[-4:]}",
    ))
    session.commit()
    session.refresh(withdrawal)
    return withdrawal


def _waiting(session: Session, withdrawal_id: UUID) -> Withdrawal:
    w = session.exec(select(Withdrawal).where(Withdrawal.id == withdrawal_id).with_for_update()).first()
    if w is None:
        raise HTTPException(status_code=404, detail="Withdrawal not found.")
    if w.status != WithdrawalStatus.REQUESTED:
        session.rollback()
        raise HTTPException(status_code=409, detail=f"This withdrawal has already been {w.status.value}.")
    return w


def mark_paid(session: Session, withdrawal_id: UUID, reference: str, admin_id: UUID) -> Withdrawal:
    """Juwazi sent the transfer."""
    w = _waiting(session, withdrawal_id)
    w.status = WithdrawalStatus.PAID
    w.payout_reference = reference.strip()[:120]
    w.decided_by, w.decided_at = admin_id, _now()
    session.add(w)
    session.commit()
    session.refresh(w)
    return w


def reject(session: Session, withdrawal_id: UUID, reason: str, admin_id: UUID) -> Withdrawal:
    """Turned down: the amount goes back into the student's balance."""
    w = _waiting(session, withdrawal_id)
    w.status = WithdrawalStatus.REJECTED
    w.reject_reason = reason.strip()[:300]
    w.decided_by, w.decided_at = admin_id, _now()
    session.add(w)
    session.add(CommissionEntry(
        user_id=w.user_id, kind=EntryKind.WITHDRAWAL_RETURNED, amount=Decimal(w.amount), currency=w.currency,
        withdrawal_id=w.id, description=f"Withdrawal returned: {w.reject_reason}"[:300],
    ))
    session.commit()
    session.refresh(w)
    return w
