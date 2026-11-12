"""
An organisation's student places: a free trial on sign-up, then places paid
for per term (schools) or per 30 days (training and certification), plus any
Juwazi grants by hand (enterprise, invoices).
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement
from sqlmodel import Session, col, func, select

from auth.database.schema.billing.billing_db import (
    OrgBillingPeriod, Payment, PaymentPurpose, PeriodKind, Product,
)
from auth.database.schema.cohort.cohort_db import CohortMember
from auth.database.schema.cohort.enum import MemberStatus
from auth.database.schema.membership.membership_db import OrgMembership
from auth.database.schema.organization.enums import OrganizationType, OrganizationVisibility
from auth.database.schema.organization.organization_db import OrganizationModel
from auth.database.schema.user.enums import MembershipStatus, UserRole
from auth.services.billing.payments import register_fulfiller
from auth.services.billing.platform_settings import PlatformSettings, get_settings

SCHOOL_TYPES: frozenset[OrganizationType] = frozenset({OrganizationType.SCHOOL, OrganizationType.UNIVERSITY})

State = Literal["trial", "active", "expired", "none"]


@dataclass(frozen=True)
class BillingStatus:
    state: State
    seats: int                     # student places available now
    students: int                  # students using a place
    ends_at: datetime | None       # when the current trial or paid period ends
    product: Product               # what this organisation buys
    period_days: int               # how long one purchase lasts
    enforced: bool                 # whether limits are applied (never for exam bodies)
    exempt: bool = False           # public organisations (exam bodies) don't pay

    @property
    def can_add_students(self) -> bool:
        if self.exempt or not self.enforced:
            return True
        return self.state in ("trial", "active") and self.students < self.seats


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def product_for(org: OrganizationModel) -> Product:
    return Product.SCHOOL_SEAT if org.organization_type in SCHOOL_TYPES else Product.TRAINING_SEAT


def period_days(settings: PlatformSettings, product: Product) -> int:
    return settings.term_days if product == Product.SCHOOL_SEAT else settings.training_period_days


def not_former_student(org_id: UUID) -> ColumnElement[bool]:
    """For a query on OrgMembership: leaves out graduates who aren't in a group
    any more (former students; their accounts stay open for external exams)."""
    current = aliased(CohortMember)
    ended = aliased(CohortMember)
    in_a_group = (
        select(current.id).where(
            current.student_id == OrgMembership.user_id, current.org_id == org_id,
            current.status == MemberStatus.ACTIVE.value,
        ).correlate(OrgMembership).exists()
    )
    finished = (
        select(ended.id).where(
            ended.student_id == OrgMembership.user_id, ended.org_id == org_id,
            ended.status == MemberStatus.GRADUATED.value,
        ).correlate(OrgMembership).exists()
    )
    return ~(finished & ~in_a_group)


def students_using_places(session: Session, org_id: UUID) -> int:
    """Active students, leaving out graduates who aren't in a group any more
    (their accounts stay open for external exams, but they don't use a place)."""
    stmt = select(func.count()).select_from(OrgMembership).where(
        OrgMembership.org_id == org_id,
        OrgMembership.role == UserRole.STUDENT,
        # Added but not signed in yet still holds a place.
        col(OrgMembership.status).in_([MembershipStatus.ACTIVE, MembershipStatus.PENDING]),
        not_former_student(org_id),
    )
    return int(session.exec(stmt).one())


def _current_periods(session: Session, org_id: UUID, now: datetime) -> list[OrgBillingPeriod]:
    return list(session.exec(
        select(OrgBillingPeriod).where(
            OrgBillingPeriod.org_id == org_id,
            col(OrgBillingPeriod.starts_at) <= now,
            col(OrgBillingPeriod.ends_at) > now,
        )
    ).all())


def status(session: Session, org: OrganizationModel) -> BillingStatus:
    settings = get_settings(session)
    product = product_for(org)
    now = _now()
    periods = _current_periods(session, org.id, now)
    paid = [p for p in periods if p.kind in (PeriodKind.PAID, PeriodKind.MANUAL)]
    trial = [p for p in periods if p.kind == PeriodKind.TRIAL]
    if paid:
        state: State = "active"
        seats = sum(p.seats for p in paid)
        ends_at: datetime | None = max(_aware(p.ends_at) for p in paid)
    elif trial:
        state, seats, ends_at = "trial", max(p.seats for p in trial), max(_aware(p.ends_at) for p in trial)
    else:
        had_any = session.exec(select(OrgBillingPeriod.id).where(OrgBillingPeriod.org_id == org.id)).first()
        state, seats, ends_at = ("expired" if had_any else "none"), 0, None
    # Billing only applies to private organisations: exam bodies (public) never
    # pay and are never limited.
    exempt = is_exempt(org)
    return BillingStatus(
        state=state, seats=seats, students=students_using_places(session, org.id), ends_at=ends_at,
        product=product, period_days=period_days(settings, product),
        enforced=settings.enforce_limits and not exempt, exempt=exempt,
    )


def is_exempt(org: OrganizationModel) -> bool:
    """Public organisations (exam bodies) are free: billing is for private ones."""
    return org.visibility == OrganizationVisibility.PUBLIC


def start_trial(session: Session, org_id: UUID, *, seats: int | None = None, created_by: UUID | None = None) -> OrgBillingPeriod | None:
    """The free trial a new organisation gets (once). Not committed."""
    settings = get_settings(session)
    if settings.trial_days <= 0:
        return None
    if session.exec(select(OrgBillingPeriod.id).where(OrgBillingPeriod.org_id == org_id)).first():
        return None
    now = _now()
    period = OrgBillingPeriod(
        org_id=org_id, kind=PeriodKind.TRIAL, seats=seats if seats is not None else settings.trial_student_limit,
        starts_at=now, ends_at=now + timedelta(days=settings.trial_days), created_by=created_by,
    )
    session.add(period)
    return period


def assert_can_add_students(session: Session, org_id: UUID, adding: int = 1) -> None:
    org = session.get(OrganizationModel, org_id)
    if org is None:
        return
    st = status(session, org)
    if not st.enforced:
        return
    if st.state in ("expired", "none"):
        raise HTTPException(
            status_code=403,
            detail="Your organisation's plan has ended. Renew it in Billing to add students.",
        )
    if st.students + adding > st.seats:
        left = max(0, st.seats - st.students)
        raise HTTPException(
            status_code=403,
            detail=(
                f"Not enough student places: {st.students} of {st.seats} used"
                + (f", {left} left" if left else "")
                + ". Add places in Billing."
            ),
        )


def purchase_plan(session: Session, org: OrganizationModel, seats: int) -> tuple[Product, datetime, datetime, bool]:
    """(product, starts, ends, adding_to_current) for buying `seats` now.
    With a paid period running, the places are added to it until it ends;
    otherwise a new period starts today."""
    settings = get_settings(session)
    product = product_for(org)
    now = _now()
    paid = [p for p in _current_periods(session, org.id, now) if p.kind in (PeriodKind.PAID, PeriodKind.MANUAL)]
    if paid:
        return product, now, max(_aware(p.ends_at) for p in paid), True
    return product, now, now + timedelta(days=period_days(settings, product)), False


def _fulfil_seats(session: Session, payment: Payment) -> None:
    d = payment.details
    session.add(OrgBillingPeriod(
        org_id=UUID(d["org_id"]), kind=PeriodKind.PAID, seats=int(d["seats"]),
        starts_at=datetime.fromisoformat(d["starts_at"]),
        ends_at=datetime.fromisoformat(d["ends_at"]),
        payment_id=payment.id, created_by=payment.user_id,
    ))


register_fulfiller(PaymentPurpose.ORG_SEATS, _fulfil_seats)


def grant(
    session: Session, org_id: UUID, *, seats: int, days: int, kind: PeriodKind, note: str | None, actor_id: UUID,
) -> OrgBillingPeriod:
    """Juwazi gives places by hand (enterprise, invoice, extending a trial)."""
    now = _now()
    period = OrgBillingPeriod(
        org_id=org_id, kind=kind, seats=seats, starts_at=now, ends_at=now + timedelta(days=days),
        note=note, created_by=actor_id,
    )
    session.add(period)
    session.commit()
    session.refresh(period)
    return period


def seat_total(unit: Decimal, seats: int) -> Decimal:
    return unit * seats
