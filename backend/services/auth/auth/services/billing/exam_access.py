"""
Students' access to an exam body's practice exams (External Exams): one
subscription per exam body, bought by the student (or, later, their school).
Paying again while subscribed adds to the end of the current subscription.

An exam body that is hidden (no longer public) stops selling: no new trials or
subscriptions. Students who have already paid keep seeing it and practising
until their subscription ends; free trials of a hidden body stop.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlmodel import Session, col, func, select

from auth.database.schema.cohort.cohort_db import CohortMember
from auth.database.schema.cohort.enum import MemberStatus
from auth.database.schema.membership.membership_db import OrgMembership
from auth.database.schema.user.enums import MembershipStatus, UserRole

from auth.database.schema.billing.billing_db import Payment, PaymentPurpose
from auth.database.schema.exam_subscription.enum import SubscribedBy, SubscriptionPlan, SubscriptionStatus
from auth.database.schema.exam_subscription.exam_subscription_db import ExamBodySubscription
from auth.database.schema.organization.enums import OrganizationVisibility
from auth.database.schema.organization.organization_db import OrganizationModel
from auth.services.billing.org_billing import not_former_student
from auth.services.billing.payments import register_fulfiller


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class Access:
    public: bool                       # the student can see this exam body (on sale, or already paid for)
    active: bool                       # ...and may practise its exams now
    expires_at: datetime | None
    on_sale: bool = False              # taking new trials and subscriptions


def is_on_sale(body: OrganizationModel) -> bool:
    return body.visibility == OrganizationVisibility.PUBLIC


def on_sale_bodies(session: Session) -> list[OrganizationModel]:
    return list(session.exec(
        select(OrganizationModel).where(OrganizationModel.visibility == OrganizationVisibility.PUBLIC)
        .order_by(col(OrganizationModel.name))
    ).all())


def visible_bodies(session: Session, student_id: UUID) -> list[OrganizationModel]:
    """Exam bodies on sale, plus hidden ones this student has paid access to."""
    now = _now()
    paid_hidden = (
        select(ExamBodySubscription.exam_body_org_id).where(
            ExamBodySubscription.student_id == student_id,
            ExamBodySubscription.status == SubscriptionStatus.ACTIVE,
            col(ExamBodySubscription.is_trial).is_(False),
            (col(ExamBodySubscription.expires_at).is_(None)) | (col(ExamBodySubscription.expires_at) > now),
        )
    )
    return list(session.exec(
        select(OrganizationModel).where(
            (OrganizationModel.visibility == OrganizationVisibility.PUBLIC)
            | col(OrganizationModel.id).in_(paid_hidden)
        ).order_by(col(OrganizationModel.name))
    ).all())


def access_for(
    session: Session, student_id: UUID, body_ids: list[UUID], paid_only: set[UUID] | None = None,
) -> dict[UUID, datetime | None]:
    """Exam bodies the student can use now -> when that ends (None = no end).
    For bodies in `paid_only` (hidden ones), free trials don't count."""
    if not body_ids:
        return {}
    paid_only = paid_only or set()
    now = _now()
    out: dict[UUID, datetime | None] = {}
    for sub in session.exec(
        select(ExamBodySubscription).where(
            ExamBodySubscription.student_id == student_id,
            col(ExamBodySubscription.exam_body_org_id).in_(body_ids),
            ExamBodySubscription.status == SubscriptionStatus.ACTIVE,
        )
    ).all():
        ends = _aware(sub.expires_at) if sub.expires_at else None
        if ends is not None and ends <= now:
            continue
        if sub.is_trial and sub.exam_body_org_id in paid_only:
            continue
        current = out.get(sub.exam_body_org_id, now)
        if sub.exam_body_org_id not in out or ends is None or (current is not None and ends > current):
            out[sub.exam_body_org_id] = ends
    return out


def check(session: Session, student_id: UUID, body_id: UUID) -> Access:
    body = session.get(OrganizationModel, body_id)
    if body is None:
        return Access(public=False, active=False, expires_at=None)
    on_sale = is_on_sale(body)
    access = access_for(session, student_id, [body_id], paid_only=None if on_sale else {body_id})
    active = body_id in access
    return Access(public=on_sale or active, active=active, expires_at=access.get(body_id), on_sale=on_sale)


def assert_on_sale(session: Session, student_id: UUID, body_id: UUID) -> Access:
    """New trials and subscriptions: only while the exam body is public."""
    from fastapi import HTTPException
    a = check(session, student_id, body_id)
    if not a.public:
        raise HTTPException(status_code=404, detail="Exam body not found.")
    if not a.on_sale:
        raise HTTPException(status_code=409, detail="This exam body isn't taking new subscriptions.")
    return a


def has_used(session: Session, student_id: UUID, body_ids: list[UUID]) -> set[UUID]:
    """Exam bodies the student has had any subscription or trial with."""
    if not body_ids:
        return set()
    return set(session.exec(
        select(ExamBodySubscription.exam_body_org_id).where(
            ExamBodySubscription.student_id == student_id,
            col(ExamBodySubscription.exam_body_org_id).in_(body_ids),
            col(ExamBodySubscription.status).in_([SubscriptionStatus.ACTIVE, SubscriptionStatus.EXPIRED, SubscriptionStatus.CANCELLED]),
        )
    ).all())


def on_trial(session: Session, student_id: UUID, body_ids: list[UUID]) -> set[UUID]:
    if not body_ids:
        return set()
    now = _now()
    return {
        s.exam_body_org_id for s in session.exec(
            select(ExamBodySubscription).where(
                ExamBodySubscription.student_id == student_id,
                col(ExamBodySubscription.exam_body_org_id).in_(body_ids),
                ExamBodySubscription.status == SubscriptionStatus.ACTIVE,
                col(ExamBodySubscription.is_trial).is_(True),
            )
        ).all()
        if s.expires_at is None or _aware(s.expires_at) > now
    }


def start_trial(session: Session, student_id: UUID, body_id: UUID, days: int, org_id: UUID | None) -> ExamBodySubscription:
    """The student's one free trial of this exam body."""
    from fastapi import HTTPException
    if days <= 0:
        raise HTTPException(status_code=400, detail="Free trials aren't available right now.")
    assert_on_sale(session, student_id, body_id)
    if body_id in has_used(session, student_id, [body_id]):
        raise HTTPException(status_code=409, detail="You've already had a free trial or subscription for this exam body.")
    now = _now()
    trial = ExamBodySubscription(
        student_id=student_id, exam_body_org_id=body_id, org_id=org_id,
        plan=SubscriptionPlan.FREE, status=SubscriptionStatus.ACTIVE, subscribed_by=SubscribedBy.SELF,
        amount_paid=0.0, starts_at=now, expires_at=now + timedelta(days=days),
        is_trial=True, trial_ends_at=now + timedelta(days=days), created_at=now, updated_at=now,
    )
    session.add(trial)
    session.commit()
    session.refresh(trial)
    return trial


def grant(
    session: Session, body_id: UUID, student_ids: list[UUID], days: int, *,
    amount_each: float, currency: str, org_id: UUID | None, by: SubscribedBy,
) -> None:
    """Give students `days` of access to an exam body (caller commits). Anyone
    with access running gets the days added to the end; a trial being paid for
    becomes a subscription. One read for all the students."""
    if not student_ids:
        return
    now = _now()
    current: dict[UUID, ExamBodySubscription] = {}
    for sub in session.exec(
        select(ExamBodySubscription).where(
            col(ExamBodySubscription.student_id).in_(student_ids),
            ExamBodySubscription.exam_body_org_id == body_id,
            ExamBodySubscription.status == SubscriptionStatus.ACTIVE,
        )
    ).all():
        if sub.expires_at is None or _aware(sub.expires_at) <= now:
            continue
        best = current.get(sub.student_id)
        if best is None or (best.expires_at is not None and _aware(sub.expires_at) > _aware(best.expires_at)):
            current[sub.student_id] = sub
    for student_id in dict.fromkeys(student_ids):
        sub = current.get(student_id)
        if sub is not None and sub.expires_at is not None:
            sub.expires_at = _aware(sub.expires_at) + timedelta(days=days)     # paid early: add to the end
            sub.amount_paid = amount_each
            sub.is_trial = False
            sub.plan = SubscriptionPlan.PAID
            sub.updated_at = now
            session.add(sub)
            continue
        session.add(ExamBodySubscription(
            student_id=student_id, exam_body_org_id=body_id, org_id=org_id,
            plan=SubscriptionPlan.PAID, status=SubscriptionStatus.ACTIVE, subscribed_by=by,
            amount_paid=amount_each, currency=currency,
            starts_at=now, expires_at=now + timedelta(days=days), created_at=now, updated_at=now,
        ))


def _fulfil_access(session: Session, payment: Payment) -> None:
    d = payment.details
    grant(
        session, UUID(d["exam_body_org_id"]), [UUID(d["student_id"])], int(d["days"]),
        amount_each=float(payment.amount), currency=payment.currency, org_id=payment.org_id, by=SubscribedBy.SELF,
    )


def _fulfil_bulk(session: Session, payment: Payment) -> None:
    """A school paying for its students."""
    d = payment.details
    ids = [UUID(x) for x in d["student_ids"]]
    grant(
        session, UUID(d["exam_body_org_id"]), ids, int(d["days"]),
        amount_each=float(d["unit_amount"]), currency=payment.currency, org_id=payment.org_id,
        by=SubscribedBy.ORGANIZATION,
    )


# ---- Schools paying for their students ------------------------------------------

MAX_BULK_STUDENTS: int = 5000


def school_students(
    session: Session, org_id: UUID, cohort_id: UUID | None = None, student_ids: list[UUID] | None = None,
) -> list[UUID]:
    """The organisation's students to pay for: every current student (not
    graduates who've left their class), one class, or chosen ones (only ones
    really in the organisation). Includes students who haven't signed in yet."""
    stmt = select(OrgMembership.user_id).where(
        OrgMembership.org_id == org_id, OrgMembership.role == UserRole.STUDENT,
        col(OrgMembership.status).in_([MembershipStatus.ACTIVE, MembershipStatus.PENDING]),
    )
    if cohort_id is not None:
        stmt = stmt.where(col(OrgMembership.user_id).in_(
            select(CohortMember.student_id).where(
                CohortMember.cohort_id == cohort_id, CohortMember.org_id == org_id,
                CohortMember.status == MemberStatus.ACTIVE.value,
            )
        ))
    if student_ids is not None:
        stmt = stmt.where(col(OrgMembership.user_id).in_(student_ids))
    elif cohort_id is None:
        # "All students": current ones, not graduates who've left their class.
        stmt = stmt.where(not_former_student(org_id))
    return list(dict.fromkeys(session.exec(stmt.limit(MAX_BULK_STUDENTS + 1)).all()))


def with_access(session: Session, student_ids: list[UUID], body_ids: list[UUID]) -> dict[UUID, int]:
    """How many of these students have paid access to each exam body now."""
    if not student_ids or not body_ids:
        return {}
    now = _now()
    rows = session.exec(
        select(ExamBodySubscription.exam_body_org_id, func.count(func.distinct(ExamBodySubscription.student_id))).where(
            col(ExamBodySubscription.student_id).in_(student_ids),
            col(ExamBodySubscription.exam_body_org_id).in_(body_ids),
            ExamBodySubscription.status == SubscriptionStatus.ACTIVE,
            col(ExamBodySubscription.is_trial).is_(False),
            (col(ExamBodySubscription.expires_at).is_(None)) | (col(ExamBodySubscription.expires_at) > now),
        ).group_by(col(ExamBodySubscription.exam_body_org_id))
    ).all()
    return {b: int(n) for b, n in rows}


register_fulfiller(PaymentPurpose.EXAM_BODY_ACCESS, _fulfil_access)
register_fulfiller(PaymentPurpose.EXAM_BODY_BULK, _fulfil_bulk)
