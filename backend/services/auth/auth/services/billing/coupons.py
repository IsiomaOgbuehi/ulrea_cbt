"""
Coupons: discount codes Juwazi gives out to subsidise what schools and
students pay.

A percent coupon comes off the whole payment; an amount coupon comes off each
student or place, in its one currency. A coupon counts against its limits
from the moment a checkout using it starts (so two people can't both take
the last use) until that payment fails or the checkout is left for
`RESERVE_FOR`; once paid it counts for good. When a coupon covers the whole
price nothing goes to the payment provider: the purchase is given straight
away.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlmodel import Session, col, func, or_, select

from auth.database.schema.billing.billing_db import (
    Coupon, CouponKind, CouponRedemption, Currency, Payment, PaymentPurpose, RedemptionStatus,
)

# An open checkout holds its coupon this long before the use is freed again.
RESERVE_FOR = timedelta(hours=2)

PURPOSE_NAMES: dict[PaymentPurpose, str] = {
    PaymentPurpose.ORG_SEATS: "student places",
    PaymentPurpose.EXAM_BODY_ACCESS: "a student's External Exams access",
    PaymentPurpose.EXAM_BODY_BULK: "students' External Exams access paid by the school",
}


@dataclass(frozen=True)
class Discounted:
    """A price after a coupon (or without one)."""
    total: Decimal               # before the coupon
    discount: Decimal
    due: Decimal                 # what's paid
    coupon: Coupon | None


def normalise(code: str) -> str:
    return code.strip().upper()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(when: datetime) -> datetime:
    # SQLite hands back naive datetimes; they were stored in UTC.
    return when if when.tzinfo is not None else when.replace(tzinfo=timezone.utc)


def payer_key(purpose: PaymentPurpose, org_id: UUID | None, user_id: UUID) -> str:
    """Who "once each" means: the student paying for themselves, otherwise the school."""
    if purpose == PaymentPurpose.EXAM_BODY_ACCESS or org_id is None:
        return f"user:{user_id}"
    return f"org:{org_id}"


def uses(session: Session, coupon_id: UUID, payer: str | None = None) -> int:
    """Redemptions that count: paid, or a checkout still open."""
    stmt = select(func.count()).select_from(CouponRedemption).where(
        CouponRedemption.coupon_id == coupon_id,
        or_(
            col(CouponRedemption.status) == RedemptionStatus.USED,
            (col(CouponRedemption.status) == RedemptionStatus.RESERVED)
            & (col(CouponRedemption.created_at) >= _now() - RESERVE_FOR),
        ),
    )
    if payer is not None:
        stmt = stmt.where(CouponRedemption.payer == payer)
    return int(session.exec(stmt).one())


def uses_by_coupon(session: Session, coupon_ids: list[UUID]) -> dict[UUID, int]:
    """Paid uses per coupon, in one query (for the admin list)."""
    if not coupon_ids:
        return {}
    rows = session.exec(
        select(CouponRedemption.coupon_id, func.count())
        .where(col(CouponRedemption.coupon_id).in_(coupon_ids), CouponRedemption.status == RedemptionStatus.USED)
        .group_by(col(CouponRedemption.coupon_id))
    ).all()
    return {cid: int(n) for cid, n in rows}


def _refuse(message: str) -> HTTPException:
    return HTTPException(status_code=400, detail=message)


def check_usable(
    session: Session, coupon: Coupon | None, *,
    purpose: PaymentPurpose, org_id: UUID | None, user_id: UUID, currency: str,
) -> Coupon:
    """The coupon, if it can be used on this payment; a 400 saying why not otherwise."""
    if coupon is None or not coupon.active:
        raise _refuse("This coupon code isn't valid.")
    if coupon.expires_at is not None and _aware(coupon.expires_at) <= _now():
        raise _refuse("This coupon has expired.")
    if purpose.value not in coupon.purposes:
        raise _refuse(f"This coupon can't be used to pay for {PURPOSE_NAMES[purpose]}.")
    if coupon.org_ids and (org_id is None or str(org_id) not in coupon.org_ids):
        raise _refuse("This coupon isn't for your school.")
    if coupon.kind == CouponKind.AMOUNT and coupon.currency != currency.upper():
        raise _refuse(f"This coupon is for payments in {coupon.currency}.")
    if coupon.max_redemptions is not None and uses(session, coupon.id) >= coupon.max_redemptions:
        raise _refuse("This coupon has been used up.")
    payer = payer_key(purpose, org_id, user_id)
    if coupon.once_per_payer and uses(session, coupon.id, payer) > 0:
        raise _refuse(
            "You've already used this coupon." if payer.startswith("user:")
            else "Your school has already used this coupon."
        )
    return coupon


def _whole_units(session: Session, currency: str) -> bool:
    row = session.get(Currency, currency.upper())
    return bool(row and row.whole_units)


def price_after(
    session: Session, code: str | None, *,
    purpose: PaymentPurpose, org_id: UUID | None, user_id: UUID,
    unit_amount: Decimal, quantity: int, currency: str,
) -> Discounted:
    """The price of `quantity` at `unit_amount`, less the coupon (if a code was given)."""
    total = unit_amount * quantity
    if not code or not code.strip():
        return Discounted(total=total, discount=Decimal(0), due=total, coupon=None)
    coupon = session.exec(select(Coupon).where(Coupon.code == normalise(code))).first()
    coupon = check_usable(session, coupon, purpose=purpose, org_id=org_id, user_id=user_id, currency=currency)
    if coupon.kind == CouponKind.PERCENT:
        off = (total * Decimal(coupon.percent_off or 0) / Decimal(100)).quantize(Decimal("0.01"), ROUND_HALF_UP)
    else:
        off = min(Decimal(coupon.amount_off or 0), unit_amount) * quantity
    off = min(off, total)
    step = Decimal("1") if _whole_units(session, currency) else Decimal("0.01")
    due = (total - off).quantize(step, rounding=ROUND_CEILING)
    return Discounted(total=total, discount=total - due, due=due, coupon=coupon)


def reserve(session: Session, price: Discounted, payment: Payment) -> None:
    """Hold a use of the coupon for this payment (same transaction as the payment).
    Locks the coupon row and checks its limits again, so two checkouts can't
    both take the last use."""
    if price.coupon is None:
        return
    coupon = session.exec(select(Coupon).where(Coupon.id == price.coupon.id).with_for_update()).first()
    check_usable(
        session, coupon, purpose=payment.purpose, org_id=payment.org_id,
        user_id=payment.user_id, currency=payment.currency,
    )
    session.add(CouponRedemption(
        coupon_id=price.coupon.id, payment_id=payment.id,
        payer=payer_key(payment.purpose, payment.org_id, payment.user_id),
        org_id=payment.org_id, user_id=payment.user_id, discount=price.discount, currency=payment.currency,
        status=RedemptionStatus.USED if price.due == 0 else RedemptionStatus.RESERVED,
    ))


def settle(session: Session, payment_id: UUID, paid: bool) -> None:
    """The payment went through (the use counts for good) or failed (it's freed)."""
    redemption = session.exec(select(CouponRedemption).where(CouponRedemption.payment_id == payment_id)).first()
    if redemption is None or redemption.status != RedemptionStatus.RESERVED:
        return
    redemption.status = RedemptionStatus.USED if paid else RedemptionStatus.RELEASED
    session.add(redemption)
