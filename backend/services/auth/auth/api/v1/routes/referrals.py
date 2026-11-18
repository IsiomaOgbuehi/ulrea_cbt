"""Refer and earn: a student's link, commission account and withdrawals; and
Juwazi paying the withdrawals out."""
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, col, func, select

from auth.api_models.referrals import (
    AdminWithdrawalPage, AdminWithdrawalRead, CommissionEntryPage, CommissionEntryRead, MarkPaid, ReferralInvite,
    ReferralSummary, RejectWithdrawal, WithdrawalRead, WithdrawalRequest,
)
from auth.core.settings import settings
from auth.database.database import SessionDep
from auth.database.schema.referral.referral_db import CommissionEntry, Withdrawal, WithdrawalStatus
from auth.database.schema.user.user_db import UserModel
from auth.dependencies.auth_dependencies import get_user_context, require_platform_admin
from auth.services import referrals
from auth.services.billing.platform_settings import get_settings
from auth.services.user.user_context import UserContext

router = APIRouter(tags=["referrals"])


def _student(ctx: UserContext = Depends(get_user_context)) -> UserContext:
    if not ctx.is_candidate:
        raise HTTPException(status_code=403, detail="Refer and earn is for students.")
    return ctx


Student = Annotated[UserContext, Depends(_student)]
Admin = Annotated[UserContext, Depends(require_platform_admin)]


def _pages(total: int, size: int) -> int:
    return max(1, -(-total // size))


def withdrawal_read(w: Withdrawal) -> WithdrawalRead:
    return WithdrawalRead(
        id=w.id, amount=w.amount, currency=w.currency, status=w.status, holder=w.holder,
        guardian_relationship=w.guardian_relationship, account_name=w.account_name,
        account_number_last4=w.account_number[-4:], bank_name=w.bank_name, payout_reference=w.payout_reference,
        reject_reason=w.reject_reason, created_at=w.created_at, decided_at=w.decided_at,
    )


def referral_link(code: str) -> str:
    return f"{settings.FRONTEND_URL}/join?ref={code}"


# ------------------------------------------------------------ public

@router.get("/referrals/invite/{code}", response_model=ReferralInvite)
def invite(code: str, session: SessionDep) -> ReferralInvite:
    """Who invited this person (for the sign-up page). First name only."""
    referrer = referrals.referrer_by_code(session, code)
    if referrer is None:
        raise HTTPException(status_code=404, detail="This invite link isn't valid.")
    return ReferralInvite(referrer_first_name=referrer.firstname)


# ------------------------------------------------------------ the student

@router.get("/referrals/me", response_model=ReferralSummary)
def my_referrals(session: SessionDep, ctx: Student) -> ReferralSummary:
    s = get_settings(session)
    code = referrals.code_for(session, ctx.user.id)
    balance = referrals.balance(session, ctx.user.id)
    waiting = referrals.open_withdrawal(session, ctx.user.id)
    minimum = Decimal(s.referral_min_withdrawal)
    return ReferralSummary(
        code=code, link=referral_link(code), percent=s.referral_percent, months=s.referral_months,
        friends=referrals.friends_count(session, ctx.user.id), balance=balance,
        total_earned=referrals.total_earned(session, ctx.user.id), currency=s.base_currency,
        min_withdrawal=minimum, can_withdraw=waiting is None and balance >= minimum and balance > 0,
        waiting_withdrawal=withdrawal_read(waiting) if waiting else None,
    )


@router.get("/referrals/me/entries", response_model=CommissionEntryPage)
def my_entries(
    session: SessionDep, ctx: Student,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> CommissionEntryPage:
    """The commission account, newest first."""
    where = CommissionEntry.user_id == ctx.user.id
    total = int(session.exec(select(func.count()).select_from(CommissionEntry).where(where)).one())
    rows = session.exec(
        select(CommissionEntry).where(where)
        .order_by(col(CommissionEntry.created_at).desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return CommissionEntryPage(
        items=[CommissionEntryRead.model_validate(r, from_attributes=True) for r in rows],
        total=total, page=page, page_size=page_size, total_pages=_pages(total, page_size),
    )


@router.get("/referrals/me/withdrawals", response_model=list[WithdrawalRead])
def my_withdrawals(session: SessionDep, ctx: Student) -> list[WithdrawalRead]:
    rows = session.exec(
        select(Withdrawal).where(Withdrawal.user_id == ctx.user.id).order_by(col(Withdrawal.created_at).desc()).limit(50)
    ).all()
    return [withdrawal_read(w) for w in rows]


@router.post("/referrals/me/withdrawals", response_model=WithdrawalRead, status_code=201)
def withdraw(payload: WithdrawalRequest, session: SessionDep, ctx: Student) -> WithdrawalRead:
    """Ask for commissions to be paid to a bank account. The amount leaves the
    balance now; it comes back if Juwazi turns the request down."""
    w = referrals.request_withdrawal(session, ctx.user.id, payload.amount, referrals.PayoutAccount(
        holder=payload.holder, adult_confirmed=payload.adult_confirmed,
        guardian_relationship=payload.guardian_relationship, account_name=payload.account_name,
        account_number=payload.account_number.strip(), bank_name=payload.bank_name,
    ))
    return withdrawal_read(w)


# ------------------------------------------------------------ Juwazi paying out

def _admin_read(session: Session, w: Withdrawal, students: dict[UUID, UserModel]) -> AdminWithdrawalRead:
    student = students.get(w.user_id)
    return AdminWithdrawalRead(
        **withdrawal_read(w).model_dump(), account_number=w.account_number, adult_confirmed=w.adult_confirmed,
        student_id=w.user_id,
        student_name=f"{student.firstname} {student.lastname}".strip() if student else "Deleted student",
        student_email=student.email if student else None, balance_after=referrals.balance(session, w.user_id),
    )


@router.get("/admin/withdrawals", response_model=AdminWithdrawalPage)
def admin_withdrawals(
    session: SessionDep, ctx: Admin,
    status: WithdrawalStatus | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> AdminWithdrawalPage:
    base = select(Withdrawal)
    if status is not None:
        base = base.where(Withdrawal.status == status)
    total = int(session.exec(select(func.count()).select_from(base.subquery())).one())
    # Oldest waiting first; otherwise newest first.
    order = col(Withdrawal.created_at).asc() if status == WithdrawalStatus.REQUESTED else col(Withdrawal.created_at).desc()
    rows = list(session.exec(base.order_by(order).offset((page - 1) * page_size).limit(page_size)).all())
    ids = list({w.user_id for w in rows})
    students = {u.id: u for u in session.exec(select(UserModel).where(col(UserModel.id).in_(ids))).all()} if ids else {}
    return AdminWithdrawalPage(
        items=[_admin_read(session, w, students) for w in rows], total=total, page=page, page_size=page_size,
        total_pages=_pages(total, page_size),
    )


def _one(session: Session, w: Withdrawal) -> AdminWithdrawalRead:
    student = session.get(UserModel, w.user_id)
    return _admin_read(session, w, {student.id: student} if student else {})


@router.post("/admin/withdrawals/{withdrawal_id}/paid", response_model=AdminWithdrawalRead)
def withdrawal_paid(withdrawal_id: UUID, payload: MarkPaid, session: SessionDep, ctx: Admin) -> AdminWithdrawalRead:
    """After sending the bank transfer: record its reference."""
    return _one(session, referrals.mark_paid(session, withdrawal_id, payload.reference, ctx.user.id))


@router.post("/admin/withdrawals/{withdrawal_id}/reject", response_model=AdminWithdrawalRead)
def withdrawal_rejected(
    withdrawal_id: UUID, payload: RejectWithdrawal, session: SessionDep, ctx: Admin,
) -> AdminWithdrawalRead:
    """Turn it down; the amount goes back into the student's balance and they see why."""
    return _one(session, referrals.reject(session, withdrawal_id, payload.reason, ctx.user.id))
