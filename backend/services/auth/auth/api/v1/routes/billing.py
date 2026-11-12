"""An organisation's billing (student places), and confirming payments."""
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool
from sqlmodel import Session, col, func, select

from auth.api_models.billing import (
    BillingStatusRead, CheckoutRead, CouponPrice, CurrencyRead, ExamAccessBodyRead, ExamAccessQuoteRead, ExamAccessQuoteRequest,
    PaymentPage, PaymentRead, SeatCheckoutRequest, SeatQuoteRead,
)
from auth.services.billing.platform_settings import get_settings
from auth.core.permissions import P
from auth.core.settings import settings
from auth.database.database import SessionDep
from auth.database.schema.billing.billing_db import Payment, PaymentPurpose, Product
from auth.database.schema.organization.organization_db import OrganizationModel
from auth.dependencies.auth_dependencies import get_user_context, require_permission
from auth.services.billing import coupons, exam_access, org_billing, payments, pricing  # noqa: F401  (registers fulfilment)
from auth.services.billing.coupons import Discounted
from auth.services.user.user_context import UserContext
from auth.utility.payment.providers.base import InvalidWebhook, PaymentProviderError
from auth.utility.payment.providers.registry import active_provider, provider_named

router = APIRouter(tags=["billing"])


def _org(session: Session, ctx: UserContext) -> OrganizationModel:
    org = session.get(OrganizationModel, ctx.membership.org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organisation not found.")
    return org


def status_read(st: org_billing.BillingStatus) -> BillingStatusRead:
    return BillingStatusRead(
        state=st.state, seats=st.seats, students=st.students, ends_at=st.ends_at, product=st.product,
        period_days=st.period_days, enforced=st.enforced, can_add_students=st.can_add_students,
        exempt=st.exempt,
    )


def payment_read(p: Payment) -> PaymentRead:
    return PaymentRead.model_validate(p, from_attributes=True)


@router.get("/billing/status", response_model=BillingStatusRead)
def billing_status(
    session: SessionDep, ctx: UserContext = Depends(require_permission(P.BILLING_VIEW)),
) -> BillingStatusRead:
    return status_read(org_billing.status(session, _org(session, ctx)))


@router.get("/billing/currencies", response_model=list[CurrencyRead])
def checkout_currencies(
    session: SessionDep, ctx: UserContext = Depends(get_user_context),
) -> list[CurrencyRead]:
    return [CurrencyRead.model_validate(c, from_attributes=True) for c in pricing.checkout_currencies(session)]


def coupon_price(price: Discounted) -> CouponPrice:
    c = price.coupon
    return CouponPrice(
        coupon_code=c.code if c else None, coupon_description=(c.description or None) if c else None,
        discount=price.discount, amount_due=price.due,
    )


def _seat_quote(
    session: Session, org: OrganizationModel, seats: int, currency: str,
    coupon_code: str | None = None, user_id: UUID | None = None,
) -> tuple[SeatQuoteRead, Discounted]:
    if org_billing.is_exempt(org):
        raise HTTPException(status_code=400, detail="Exam bodies don't pay for student places.")
    product, starts, ends, adding = org_billing.purchase_plan(session, org, seats)
    if not adding:
        # A new period replaces the trial or the one that ended: it must cover
        # the students already there.
        students = org_billing.students_using_places(session, org.id)
        if seats < students:
            raise HTTPException(
                status_code=400,
                detail=f"You have {students} students, so buy at least {students} places.",
            )
    q = pricing.quote(session, product, currency, seats)
    price = coupons.price_after(
        session, coupon_code, purpose=PaymentPurpose.ORG_SEATS, org_id=org.id, user_id=user_id or org.id,
        unit_amount=q.unit_amount, quantity=seats, currency=q.currency,
    )
    return SeatQuoteRead(
        product=product, currency=q.currency, unit_amount=q.unit_amount, seats=seats, total=q.total,
        starts_at=starts, ends_at=ends, adding_to_current=adding, **coupon_price(price).model_dump(),
    ), price


@router.get("/billing/quote", response_model=SeatQuoteRead)
def seat_quote(
    session: SessionDep,
    seats: Annotated[int, Query(ge=1, le=100_000)],
    currency: Annotated[str, Query(min_length=3, max_length=3)],
    coupon: Annotated[str | None, Query(max_length=40)] = None,
    ctx: UserContext = Depends(require_permission(P.BILLING_VIEW)),
) -> SeatQuoteRead:
    return _seat_quote(session, _org(session, ctx), seats, currency, coupon, ctx.user.id)[0]


@router.post("/billing/checkout", response_model=CheckoutRead)
async def seat_checkout(
    payload: SeatCheckoutRequest,
    session: SessionDep,
    ctx: UserContext = Depends(require_permission(P.BILLING_MANAGE)),
) -> CheckoutRead:
    """Pay for student places. Returns the provider's checkout page to send the payer to."""
    org = await run_in_threadpool(_org, session, ctx)
    q, price = await run_in_threadpool(
        _seat_quote, session, org, payload.seats, payload.currency, payload.coupon_code, ctx.user.id,
    )
    email = ctx.user.email or org.email
    payment, url = await payments.start_checkout(
        session, active_provider(),
        purpose=PaymentPurpose.ORG_SEATS, user_id=ctx.user.id, org_id=org.id,
        email=str(email), name=f"{ctx.user.firstname} {ctx.user.lastname}".strip(),
        amount=price.due, currency=q.currency, price=price,
        description=(
            f"{'Extra ' if q.adding_to_current else ''}{q.seats} student{'s' if q.seats != 1 else ''} "
            f"for {org.name}, until {q.ends_at:%d %b %Y}"
        ),
        details={
            "org_id": str(org.id), "seats": q.seats, "product": q.product.value,
            "starts_at": q.starts_at.isoformat(), "ends_at": q.ends_at.isoformat(),
        },
        redirect_url=f"{settings.FRONTEND_URL}/app/subscription",
    )
    return CheckoutRead(reference=payment.reference, checkout_url=url, amount=payment.amount, currency=payment.currency)


# ---- Paying for students' External Exams ----------------------------------------

@router.get("/billing/exam-access/bodies", response_model=list[ExamAccessBodyRead])
def exam_access_bodies(
    session: SessionDep, ctx: UserContext = Depends(require_permission(P.BILLING_PAY_EXAM_ACCESS)),
) -> list[ExamAccessBodyRead]:
    """Exam bodies a school can pay for, the price per student, and how many of
    its students have access already."""
    org = _org(session, ctx)
    base = get_settings(session).base_currency
    bodies = [b for b in exam_access.on_sale_bodies(session) if b.id != org.id]
    counts = exam_access.with_access(session, exam_access.school_students(session, org.id), [b.id for b in bodies])
    out: list[ExamAccessBodyRead] = []
    for b in bodies:
        try:
            unit, days = pricing.unit_price(session, Product.EXAM_BODY_ACCESS, base, b.id)
        except HTTPException:
            unit, days = None, None
        out.append(ExamAccessBodyRead(
            id=b.id, name=b.name, unit_amount=unit, currency=base,
            days=days or get_settings(session).exam_access_days, students_with_access=counts.get(b.id, 0),
        ))
    return out


def _bulk_quote(
    session: Session, org: OrganizationModel, payload: ExamAccessQuoteRequest, user_id: UUID,
) -> tuple[ExamAccessQuoteRead, list[UUID], Discounted]:
    if org_billing.is_exempt(org):
        raise HTTPException(status_code=400, detail="Exam bodies don't pay for External Exams.")
    body = session.get(OrganizationModel, payload.exam_body_org_id)
    if body is None or body.id == org.id or not exam_access.is_on_sale(body):
        raise HTTPException(status_code=404, detail="Exam body not found.")
    students = exam_access.school_students(session, org.id, payload.cohort_id, payload.student_ids)
    if not students:
        raise HTTPException(status_code=400, detail="There are no students to pay for.")
    if len(students) > exam_access.MAX_BULK_STUDENTS:
        raise HTTPException(status_code=400, detail=f"Pay for at most {exam_access.MAX_BULK_STUDENTS} students at a time.")
    try:
        unit, days = pricing.unit_price(session, Product.EXAM_BODY_ACCESS, payload.currency, body.id)
    except HTTPException:
        raise HTTPException(status_code=400, detail=f"This isn't available in {payload.currency.upper()} yet.")
    days = days or get_settings(session).exam_access_days
    already = exam_access.with_access(session, students, [body.id]).get(body.id, 0)
    price = coupons.price_after(
        session, payload.coupon_code, purpose=PaymentPurpose.EXAM_BODY_BULK, org_id=org.id, user_id=user_id,
        unit_amount=unit, quantity=len(students), currency=payload.currency,
    )
    return ExamAccessQuoteRead(
        exam_body_name=body.name, students=len(students), already_have_access=already, unit_amount=unit,
        total=unit * len(students), currency=payload.currency.upper(), days=days,
        **coupon_price(price).model_dump(),
    ), students, price


@router.post("/billing/exam-access/quote", response_model=ExamAccessQuoteRead)
def exam_access_quote(
    payload: ExamAccessQuoteRequest,
    session: SessionDep,
    ctx: UserContext = Depends(require_permission(P.BILLING_PAY_EXAM_ACCESS)),
) -> ExamAccessQuoteRead:
    return _bulk_quote(session, _org(session, ctx), payload, ctx.user.id)[0]


@router.post("/billing/exam-access/checkout", response_model=CheckoutRead)
async def exam_access_checkout(
    payload: ExamAccessQuoteRequest,
    session: SessionDep,
    ctx: UserContext = Depends(require_permission(P.BILLING_PAY_EXAM_ACCESS)),
) -> CheckoutRead:
    """Pay for students' access to an exam body's practice exams, in one payment."""
    org = await run_in_threadpool(_org, session, ctx)
    q, students, price = await run_in_threadpool(_bulk_quote, session, org, payload, ctx.user.id)
    email = ctx.user.email or org.email
    payment, url = await payments.start_checkout(
        session, active_provider(),
        purpose=PaymentPurpose.EXAM_BODY_BULK, user_id=ctx.user.id, org_id=org.id,
        email=str(email), name=f"{ctx.user.firstname} {ctx.user.lastname}".strip(),
        amount=price.due, currency=q.currency, price=price,
        description=f"{q.exam_body_name} practice exams for {q.students} student{'s' if q.students != 1 else ''}, {q.days} days each",
        details={
            "exam_body_org_id": str(payload.exam_body_org_id), "days": q.days,
            "unit_amount": str(q.unit_amount), "student_ids": [str(x) for x in students],
        },
        redirect_url=f"{settings.FRONTEND_URL}/app/subscription",
    )
    return CheckoutRead(reference=payment.reference, checkout_url=url, amount=payment.amount, currency=payment.currency)


@router.post("/payments/{reference}/confirm", response_model=PaymentRead)
async def confirm_payment(
    reference: str, session: SessionDep, ctx: UserContext = Depends(get_user_context),
) -> PaymentRead:
    """After the payer returns from the checkout page: check with the provider
    and, if paid, give what was bought. Safe to repeat."""
    owner = await run_in_threadpool(lambda: session.exec(select(Payment).where(Payment.reference == reference)).first())
    if owner is None or (owner.user_id != ctx.user.id and owner.org_id != ctx.membership.org_id):
        raise HTTPException(status_code=404, detail="Payment not found.")
    return payment_read(await payments.confirm(session, reference))


@router.post("/payments/webhook/{provider}")
async def payment_webhook(provider: str, request: Request, session: SessionDep) -> dict[str, str]:
    """Called by the payment provider. Signature-checked, then the payment is
    re-verified with the provider before anything is given."""
    try:
        handler = provider_named(provider)
    except PaymentProviderError:
        raise HTTPException(status_code=404, detail="Unknown provider.")
    body = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    try:
        notice = handler.parse_webhook(headers, body)
    except InvalidWebhook:
        raise HTTPException(status_code=401, detail="Invalid signature.")
    if notice is not None and notice.reference and notice.reference.startswith("jw_"):
        try:
            await payments.confirm(session, notice.reference)
        except HTTPException:
            pass          # not ours / unknown: acknowledge so the provider stops retrying
    return {"status": "ok"}


@router.get("/billing/payments", response_model=PaymentPage)
def org_payments(
    session: SessionDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    ctx: UserContext = Depends(require_permission(P.BILLING_VIEW)),
) -> PaymentPage:
    base = select(Payment).where(Payment.org_id == ctx.membership.org_id)
    total = int(session.exec(select(func.count()).select_from(base.subquery())).one())
    rows = session.exec(base.order_by(col(Payment.created_at).desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return PaymentPage(
        items=[payment_read(p) for p in rows], total=total, page=page, page_size=page_size,
        total_pages=max(1, -(-total // page_size)),
    )
