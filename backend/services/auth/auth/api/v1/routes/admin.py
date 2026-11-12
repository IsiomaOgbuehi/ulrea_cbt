"""Juwazi's own admin: platform settings, prices, currencies, organisations'
billing and payments. Platform admins only."""
from datetime import datetime, timezone
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, ValidationError
from sqlmodel import Session, col, func, or_, select

from auth.api.v1.routes.billing import payment_read, status_read
from auth.api_models.billing import (
    AdminOrgPage, AdminOrgRead, CouponCreate, CouponRead, CouponSchool, CouponUpdate, CurrencyRead, CurrencyUpdate, ExamBodyRead,
    GrantPeriodRequest, PaymentPage, PeriodRead, PriceRead, PriceUpsert,
)
from auth.database.database import SessionDep
from auth.database.schema.billing.billing_db import (
    Coupon, CouponKind, CouponRedemption, Currency, OrgBillingPeriod, Payment, PaymentPurpose, PaymentStatus, Price,
)
from auth.database.schema.organization.enums import OrganizationVisibility
from auth.database.schema.organization.organization_db import OrganizationModel
from auth.dependencies.auth_dependencies import get_user_context, require_platform_admin
from auth.services.billing import coupons, org_billing
from auth.services.billing.platform_settings import PlatformSettings, get_settings, update_settings
from auth.services.user.user_context import UserContext

router = APIRouter(prefix="/admin", tags=["juwazi admin"])
Admin = Annotated[UserContext, Depends(require_platform_admin)]


def _pages(total: int, size: int) -> int:
    return max(1, -(-total // size))


@router.get("/me")
def am_i_admin(ctx: UserContext = Depends(get_user_context)) -> dict[str, bool]:
    """Whether the signed-in person is a Juwazi admin (the app shows the admin
    menu on it). Answers for anyone signed in, so it's never an error; every
    other admin endpoint still refuses non-admins."""
    return {"is_platform_admin": bool(ctx.user.is_platform_admin)}


# ------------------------------------------------------------ settings

@router.get("/settings", response_model=PlatformSettings)
def read_settings(session: SessionDep, ctx: Admin) -> PlatformSettings:
    return get_settings(session)


@router.patch("/settings", response_model=PlatformSettings)
def change_settings(session: SessionDep, ctx: Admin, changes: dict[str, Any] = Body(...)) -> PlatformSettings:
    unknown = set(changes) - set(PlatformSettings.model_fields)
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown settings: {', '.join(sorted(unknown))}")
    try:
        return update_settings(session, changes, ctx.user.id)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors(include_url=False, include_context=False))


# ------------------------------------------------------------ prices

@router.get("/prices", response_model=list[PriceRead])
def list_prices(session: SessionDep, ctx: Admin) -> list[PriceRead]:
    rows = session.exec(select(Price).order_by(col(Price.product), col(Price.currency))).all()
    return [PriceRead.model_validate(p, from_attributes=True) for p in rows]


@router.put("/prices", response_model=PriceRead)
def set_price(payload: PriceUpsert, session: SessionDep, ctx: Admin) -> PriceRead:
    if payload.exam_body_org_id is not None:
        body = session.get(OrganizationModel, payload.exam_body_org_id)
        if body is None or body.visibility != OrganizationVisibility.PUBLIC:
            raise HTTPException(status_code=404, detail="Exam body not found.")
    currency = payload.currency.upper()
    if session.get(Currency, currency) is None and currency != get_settings(session).base_currency:
        raise HTTPException(status_code=422, detail=f"Unknown currency {currency}.")
    stmt = select(Price).where(Price.product == payload.product, Price.currency == currency)
    stmt = stmt.where(
        col(Price.exam_body_org_id).is_(None) if payload.exam_body_org_id is None
        else Price.exam_body_org_id == payload.exam_body_org_id
    )
    price = session.exec(stmt).first() or Price(
        product=payload.product, exam_body_org_id=payload.exam_body_org_id, currency=currency, amount=payload.amount,
    )
    price.amount = payload.amount
    price.duration_days = payload.duration_days
    price.updated_by = ctx.user.id
    price.updated_at = datetime.now(timezone.utc)
    session.add(price)
    session.commit()
    session.refresh(price)
    return PriceRead.model_validate(price, from_attributes=True)


@router.delete("/prices/{price_id}", status_code=204)
def delete_price(price_id: UUID, session: SessionDep, ctx: Admin) -> None:
    price = session.get(Price, price_id)
    if price is None:
        raise HTTPException(status_code=404, detail="Price not found.")
    session.delete(price)
    session.commit()


# ------------------------------------------------------------ currencies

@router.get("/currencies", response_model=list[CurrencyRead])
def list_currencies(session: SessionDep, ctx: Admin) -> list[CurrencyRead]:
    rows = session.exec(select(Currency).order_by(col(Currency.code))).all()
    return [CurrencyRead.model_validate(c, from_attributes=True) for c in rows]


@router.patch("/currencies/{code}", response_model=CurrencyRead)
def change_currency(code: str, payload: CurrencyUpdate, session: SessionDep, ctx: Admin) -> CurrencyRead:
    cur = session.get(Currency, code.upper())
    if cur is None:
        raise HTTPException(status_code=404, detail="Currency not found.")
    changes = payload.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(cur, key, value)
    if cur.enabled and not cur.base_per_unit and cur.code != get_settings(session).base_currency:
        raise HTTPException(status_code=422, detail="Set its exchange rate before switching it on.")
    cur.updated_by = ctx.user.id
    cur.updated_at = datetime.now(timezone.utc)
    session.add(cur)
    session.commit()
    session.refresh(cur)
    return CurrencyRead.model_validate(cur, from_attributes=True)


# ------------------------------------------------------------ exam bodies

@router.get("/exam-bodies", response_model=list[ExamBodyRead])
def exam_bodies(session: SessionDep, ctx: Admin) -> list[ExamBodyRead]:
    """Public organisations (the external exams), each with its own prices."""
    bodies = session.exec(
        select(OrganizationModel).where(OrganizationModel.visibility == OrganizationVisibility.PUBLIC)
        .order_by(col(OrganizationModel.name))
    ).all()
    prices = session.exec(select(Price).where(col(Price.exam_body_org_id).in_([b.id for b in bodies]))).all() if bodies else []
    by_body: dict[UUID, list[PriceRead]] = {}
    for p in prices:
        if p.exam_body_org_id is not None:
            by_body.setdefault(p.exam_body_org_id, []).append(PriceRead.model_validate(p, from_attributes=True))
    return [ExamBodyRead(id=b.id, name=b.name, slug=b.slug, prices=by_body.get(b.id, [])) for b in bodies]


# ------------------------------------------------------------ organisations

@router.get("/organizations", response_model=AdminOrgPage)
def organizations(
    session: SessionDep, ctx: Admin,
    search: Annotated[str | None, Query(max_length=100)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> AdminOrgPage:
    base = select(OrganizationModel)
    if search:
        like = f"%{search.strip()}%"
        base = base.where(or_(col(OrganizationModel.name).ilike(like), col(OrganizationModel.email).ilike(like)))
    total = int(session.exec(select(func.count()).select_from(base.subquery())).one())
    orgs = session.exec(
        base.order_by(col(OrganizationModel.created_at).desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    items = [
        AdminOrgRead(
            id=o.id, name=o.name, organization_type=str(getattr(o.organization_type, "value", o.organization_type)),
            visibility=str(getattr(o.visibility, "value", o.visibility)), created_at=o.created_at,
            billing=status_read(org_billing.status(session, o)),
        )
        for o in orgs
    ]
    return AdminOrgPage(items=items, total=total, page=page, page_size=page_size, total_pages=_pages(total, page_size))


class OrgVisibilityUpdate(BaseModel):
    visibility: OrganizationVisibility


@router.patch("/organizations/{org_id}/visibility", response_model=AdminOrgRead)
def set_visibility(org_id: UUID, payload: OrgVisibilityUpdate, session: SessionDep, ctx: Admin) -> AdminOrgRead:
    """Make an organisation an exam body (public: its ticked exams appear in
    students' External Exams) or back to a private organisation."""
    org = session.get(OrganizationModel, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organisation not found.")
    org.visibility = payload.visibility
    session.add(org)
    session.commit()
    session.refresh(org)
    return AdminOrgRead(
        id=org.id, name=org.name, organization_type=str(getattr(org.organization_type, "value", org.organization_type)),
        visibility=str(getattr(org.visibility, "value", org.visibility)), created_at=org.created_at,
        billing=status_read(org_billing.status(session, org)),
    )


@router.get("/organizations/{org_id}/periods", response_model=list[PeriodRead])
def org_periods(org_id: UUID, session: SessionDep, ctx: Admin) -> list[PeriodRead]:
    rows = session.exec(
        select(OrgBillingPeriod).where(OrgBillingPeriod.org_id == org_id).order_by(col(OrgBillingPeriod.starts_at).desc())
    ).all()
    return [PeriodRead.model_validate(r, from_attributes=True) for r in rows]


@router.post("/organizations/{org_id}/periods", response_model=PeriodRead)
def grant_period(org_id: UUID, payload: GrantPeriodRequest, session: SessionDep, ctx: Admin) -> PeriodRead:
    """Give an organisation places by hand: enterprise, an invoice paid offline,
    or a longer trial."""
    if session.get(OrganizationModel, org_id) is None:
        raise HTTPException(status_code=404, detail="Organisation not found.")
    period = org_billing.grant(
        session, org_id, seats=payload.seats, days=payload.days, kind=payload.kind, note=payload.note, actor_id=ctx.user.id,
    )
    return PeriodRead.model_validate(period, from_attributes=True)


# ------------------------------------------------------------ payments

@router.get("/payments", response_model=PaymentPage)
def all_payments(
    session: SessionDep, ctx: Admin,
    status: PaymentStatus | None = None,
    org_id: UUID | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PaymentPage:
    base = select(Payment)
    if status is not None:
        base = base.where(Payment.status == status)
    if org_id is not None:
        base = base.where(Payment.org_id == org_id)
    total = int(session.exec(select(func.count()).select_from(base.subquery())).one())
    rows = session.exec(base.order_by(col(Payment.created_at).desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return PaymentPage(
        items=[payment_read(p) for p in rows], total=total, page=page, page_size=page_size,
        total_pages=_pages(total, page_size),
    )


# ------------------------------------------------------------ coupons

def _school_names(session: Session, rows: list[Coupon]) -> dict[str, str]:
    """Names of every school the coupons name, in one query."""
    ids = {UUID(o) for c in rows for o in c.org_ids}
    if not ids:
        return {}
    found = session.exec(select(OrganizationModel.id, OrganizationModel.name).where(col(OrganizationModel.id).in_(ids))).all()
    return {str(i): n for i, n in found}


def _coupon_read(c: Coupon, used: int, names: dict[str, str]) -> CouponRead:
    return CouponRead(
        id=c.id, code=c.code, description=c.description, kind=c.kind, percent_off=c.percent_off,
        amount_off=c.amount_off, currency=c.currency, purposes=[PaymentPurpose(p) for p in c.purposes],
        org_ids=[UUID(o) for o in c.org_ids], once_per_payer=c.once_per_payer, max_redemptions=c.max_redemptions,
        expires_at=c.expires_at, active=c.active, created_at=c.created_at, uses=used,
        schools=[CouponSchool(id=UUID(o), name=names.get(o, "Deleted organisation")) for o in c.org_ids],
    )


def _check_schools(session: Session, org_ids: list[UUID]) -> list[str]:
    """The ids, if they're all real schools (organisations that pay; not exam bodies)."""
    if not org_ids:
        return []
    found = session.exec(select(OrganizationModel).where(col(OrganizationModel.id).in_(org_ids))).all()
    schools = {o.id for o in found if not org_billing.is_exempt(o)}
    missing = [str(i) for i in org_ids if i not in schools]
    if missing:
        raise HTTPException(status_code=400, detail=f"Not schools that pay: {', '.join(missing)}")
    return [str(i) for i in dict.fromkeys(org_ids)]


@router.get("/coupons", response_model=list[CouponRead])
def list_coupons(session: SessionDep, ctx: Admin) -> list[CouponRead]:
    rows = list(session.exec(select(Coupon).order_by(col(Coupon.created_at).desc())).all())
    used = coupons.uses_by_coupon(session, [c.id for c in rows])
    names = _school_names(session, rows)
    return [_coupon_read(c, used.get(c.id, 0), names) for c in rows]


@router.post("/coupons", response_model=CouponRead, status_code=201)
def create_coupon(payload: CouponCreate, session: SessionDep, ctx: Admin) -> CouponRead:
    code = coupons.normalise(payload.code)
    if payload.kind == CouponKind.PERCENT:
        if payload.percent_off is None:
            raise HTTPException(status_code=400, detail="Give the percent off.")
        amount_off, currency, percent_off = None, None, payload.percent_off
    else:
        if payload.amount_off is None or payload.currency is None:
            raise HTTPException(status_code=400, detail="Give the amount off and its currency.")
        if session.get(Currency, payload.currency.upper()) is None:
            raise HTTPException(status_code=400, detail=f"Unknown currency {payload.currency.upper()}.")
        amount_off, currency, percent_off = payload.amount_off, payload.currency.upper(), None
    if payload.expires_at is not None and payload.expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="The expiry date has already passed.")
    if session.exec(select(Coupon).where(Coupon.code == code)).first() is not None:
        raise HTTPException(status_code=409, detail=f"There's already a coupon {code}.")
    coupon = Coupon(
        code=code, description=payload.description.strip(), kind=payload.kind, percent_off=percent_off,
        amount_off=amount_off, currency=currency, purposes=[p.value for p in dict.fromkeys(payload.purposes)],
        org_ids=_check_schools(session, payload.org_ids), once_per_payer=payload.once_per_payer,
        max_redemptions=payload.max_redemptions, expires_at=payload.expires_at, created_by=ctx.user.id,
    )
    session.add(coupon)
    session.commit()
    session.refresh(coupon)
    return _coupon_read(coupon, 0, _school_names(session, [coupon]))


@router.patch("/coupons/{coupon_id}", response_model=CouponRead)
def update_coupon(coupon_id: UUID, payload: CouponUpdate, session: SessionDep, ctx: Admin) -> CouponRead:
    """Change a coupon's limits or switch it off. Its discount stays as it was
    made, so payments already made with it still add up."""
    coupon = session.get(Coupon, coupon_id)
    if coupon is None:
        raise HTTPException(status_code=404, detail="Coupon not found.")
    changes = payload.model_dump(exclude_unset=True)
    if "description" in changes and payload.description is not None:
        coupon.description = payload.description.strip()
    if "org_ids" in changes:
        coupon.org_ids = _check_schools(session, payload.org_ids or [])
    if "once_per_payer" in changes and payload.once_per_payer is not None:
        coupon.once_per_payer = payload.once_per_payer
    if "max_redemptions" in changes:
        coupon.max_redemptions = payload.max_redemptions          # None: no limit
    if "expires_at" in changes:
        coupon.expires_at = payload.expires_at                    # None: never
    if "active" in changes and payload.active is not None:
        coupon.active = payload.active
    session.add(coupon)
    session.commit()
    session.refresh(coupon)
    return _coupon_read(
        coupon, coupons.uses_by_coupon(session, [coupon.id]).get(coupon.id, 0), _school_names(session, [coupon]),
    )


@router.delete("/coupons/{coupon_id}", status_code=204)
def delete_coupon(coupon_id: UUID, session: SessionDep, ctx: Admin) -> None:
    """Only a coupon nobody has used; switch a used one off instead."""
    coupon = session.get(Coupon, coupon_id)
    if coupon is None:
        raise HTTPException(status_code=404, detail="Coupon not found.")
    if session.exec(select(CouponRedemption.id).where(CouponRedemption.coupon_id == coupon_id)).first() is not None:
        raise HTTPException(status_code=409, detail="This coupon has been used, so switch it off instead.")
    session.delete(coupon)
    session.commit()
