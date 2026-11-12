"""External Exams for students: exam bodies, their price, and subscribing."""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session
from starlette.concurrency import run_in_threadpool

from auth.api_models.billing import CheckoutRead
from auth.core.settings import settings
from auth.database.database import SessionDep
from auth.database.schema.billing.billing_db import PaymentPurpose, Product
from auth.database.schema.organization.organization_db import OrganizationModel
from auth.dependencies.auth_dependencies import get_user_context
from auth.services.billing import exam_access, payments, pricing
from auth.services.billing.platform_settings import get_settings
from auth.services.user.user_context import UserContext
from auth.utility.payment.providers.registry import active_provider

router = APIRouter(prefix="/external", tags=["external exams"])


def _candidate(ctx: UserContext = Depends(get_user_context)) -> UserContext:
    if not ctx.is_candidate:
        raise HTTPException(status_code=403, detail="External Exams are for students.")
    return ctx


Candidate = Annotated[UserContext, Depends(_candidate)]


class BodyPrice(BaseModel):
    amount: Decimal
    currency: str
    days: int


class ExamBodyCard(BaseModel):
    id: UUID
    name: str
    slug: str
    logo_url: str | None
    description: str | None
    price: BodyPrice | None          # None: not on sale yet
    active: bool                     # subscribed (or on a free trial) now
    expires_at: datetime | None
    on_trial: bool = False
    trial_days: int = 0              # free trial on offer (0: none, or already used)
    on_sale: bool = True             # False: hidden; shown only to students who've paid, until it ends


class SubscribeRequest(BaseModel):
    currency: str = Field(default="NGN", min_length=3, max_length=3)


class SeenRead(BaseModel):
    seen: bool


def _price(session: Session, body_id: UUID, currency: str) -> BodyPrice | None:
    try:
        unit, days = pricing.unit_price(session, Product.EXAM_BODY_ACCESS, currency, body_id)
    except HTTPException:
        return None
    return BodyPrice(amount=unit, currency=currency.upper(), days=days or get_settings(session).exam_access_days)


@router.get("/bodies", response_model=list[ExamBodyCard])
def exam_bodies(session: SessionDep, ctx: Candidate) -> list[ExamBodyCard]:
    bodies = exam_access.visible_bodies(session, ctx.user.id)
    ids = [b.id for b in bodies]
    hidden = {b.id for b in bodies if not exam_access.is_on_sale(b)}
    access = exam_access.access_for(session, ctx.user.id, ids, paid_only=hidden)
    used = exam_access.has_used(session, ctx.user.id, ids)
    trials = exam_access.on_trial(session, ctx.user.id, ids) - hidden
    settings_ = get_settings(session)
    return [
        ExamBodyCard(
            id=b.id, name=b.name, slug=b.slug, logo_url=b.logo_url, description=b.description,
            price=None if b.id in hidden else _price(session, b.id, settings_.base_currency),
            active=b.id in access, expires_at=access.get(b.id), on_trial=b.id in trials,
            trial_days=0 if b.id in used or b.id in hidden else settings_.external_trial_days,
            on_sale=b.id not in hidden,
        )
        for b in bodies
    ]


@router.post("/bodies/{body_id}/trial", response_model=ExamBodyCard)
def start_trial(body_id: UUID, session: SessionDep, ctx: Candidate) -> ExamBodyCard:
    """The student's one free trial of this exam body's practice exams."""
    days = get_settings(session).external_trial_days
    trial = exam_access.start_trial(session, ctx.user.id, body_id, days, ctx.membership.org_id)
    body = session.get(OrganizationModel, body_id)
    assert body is not None
    return ExamBodyCard(
        id=body.id, name=body.name, slug=body.slug, logo_url=body.logo_url, description=body.description,
        price=_price(session, body.id, get_settings(session).base_currency), active=True,
        expires_at=trial.expires_at, on_trial=True, trial_days=0,
    )


@router.post("/bodies/{body_id}/subscribe", response_model=CheckoutRead)
async def subscribe(body_id: UUID, payload: SubscribeRequest, session: SessionDep, ctx: Candidate) -> CheckoutRead:
    """Pay for access to one exam body's practice exams."""
    def _prepare() -> tuple[OrganizationModel, BodyPrice]:
        body = session.get(OrganizationModel, body_id)
        exam_access.assert_on_sale(session, ctx.user.id, body_id)
        if body is None:
            raise HTTPException(status_code=404, detail="Exam body not found.")
        price = _price(session, body_id, payload.currency)
        if price is None:
            raise HTTPException(status_code=400, detail=f"This isn't available in {payload.currency.upper()} yet.")
        return body, price

    body, price = await run_in_threadpool(_prepare)
    email = ctx.user.email or f"{ctx.user.id}@students.juwazi.com"     # some students have no email
    payment, url = await payments.start_checkout(
        session, active_provider(),
        purpose=PaymentPurpose.EXAM_BODY_ACCESS, user_id=ctx.user.id, org_id=ctx.membership.org_id,
        email=email, name=f"{ctx.user.firstname} {ctx.user.lastname}".strip(),
        amount=price.amount, currency=price.currency,
        description=f"{body.name} practice exams for {price.days} days",
        details={"student_id": str(ctx.user.id), "exam_body_org_id": str(body_id), "days": price.days},
        redirect_url=f"{settings.FRONTEND_URL}/app/external",
    )
    return CheckoutRead(reference=payment.reference, checkout_url=url, amount=payment.amount, currency=payment.currency)


@router.get("/seen", response_model=SeenRead)
def seen(ctx: UserContext = Depends(get_user_context)) -> SeenRead:
    return SeenRead(seen=ctx.user.external_exams_seen_at is not None)


@router.post("/seen", response_model=SeenRead)
def mark_seen(session: SessionDep, ctx: UserContext = Depends(get_user_context)) -> SeenRead:
    """The student opened External Exams: the "New" badge goes away (on every device)."""
    if ctx.user.external_exams_seen_at is None:
        user = ctx.user
        user.external_exams_seen_at = datetime.now(timezone.utc)
        session.add(user)
        session.commit()
    return SeenRead(seen=True)
