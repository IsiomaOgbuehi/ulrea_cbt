"""
Prices. Each product has a price in the base currency (NGN); exam access can
also have a price per exam body. Other currencies use a price set in that
currency when there is one, otherwise the base price converted at the rate
Juwazi set. Only enabled currencies with a way to price them are offered.
"""
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlmodel import Session, col, or_, select

from auth.database.schema.billing.billing_db import Currency, Price, Product
from auth.services.billing.platform_settings import get_settings


@dataclass(frozen=True)
class Quote:
    product: Product
    currency: str
    unit_amount: Decimal
    quantity: int
    total: Decimal
    duration_days: int | None


def _round_up(amount: Decimal, whole: bool) -> Decimal:
    step = Decimal("1") if whole else Decimal("0.01")
    return amount.quantize(step, rounding=ROUND_CEILING)


def _prices_for(session: Session, product: Product, exam_body_org_id: UUID | None) -> list[Price]:
    """The body's own prices first, then the default ones."""
    stmt = select(Price).where(Price.product == product)
    if exam_body_org_id is None:
        stmt = stmt.where(col(Price.exam_body_org_id).is_(None))
    else:
        stmt = stmt.where(or_(col(Price.exam_body_org_id).is_(None), Price.exam_body_org_id == exam_body_org_id))
    rows = list(session.exec(stmt).all())
    return sorted(rows, key=lambda p: p.exam_body_org_id is None)   # specific before default


def unit_price(
    session: Session, product: Product, currency: str, exam_body_org_id: UUID | None = None,
) -> tuple[Decimal, int | None]:
    """(price for one, its duration in days or None) in `currency`. 400 if it can't be priced."""
    settings = get_settings(session)
    base = settings.base_currency
    currency = currency.upper()
    prices = _prices_for(session, product, exam_body_org_id)
    # Duration comes from the most specific price that sets one.
    duration = next((p.duration_days for p in prices if p.duration_days), None)
    # The most specific price level (this exam body, else the default) decides.
    level = prices[0].exam_body_org_id if prices else None
    at_level = [p for p in prices if p.exam_body_org_id == level]
    cur: Currency | None = session.get(Currency, currency)
    if currency != base and (cur is None or not cur.enabled):
        raise HTTPException(status_code=400, detail=f"Payments in {currency} aren't available.")
    direct = next((p for p in at_level if p.currency == currency), None)
    if direct is not None:
        return Decimal(direct.amount), duration
    base_price = next((p for p in at_level if p.currency == base), None)
    if base_price is None:
        raise HTTPException(status_code=400, detail="This hasn't been priced yet.")
    if currency == base:
        return Decimal(base_price.amount), duration
    if cur is None or not cur.base_per_unit:
        raise HTTPException(status_code=400, detail=f"Payments in {currency} aren't available.")
    converted = Decimal(base_price.amount) / Decimal(cur.base_per_unit)
    return _round_up(converted, cur.whole_units), duration


def quote(
    session: Session, product: Product, currency: str, quantity: int, exam_body_org_id: UUID | None = None,
) -> Quote:
    unit, duration = unit_price(session, product, currency, exam_body_org_id)
    return Quote(
        product=product, currency=currency.upper(), unit_amount=unit, quantity=quantity,
        total=unit * quantity, duration_days=duration,
    )


def checkout_currencies(session: Session) -> list[Currency]:
    """Currencies offered at checkout: the base, plus enabled ones with a rate."""
    base = get_settings(session).base_currency
    rows = session.exec(select(Currency).order_by(col(Currency.code))).all()
    return [c for c in rows if c.code == base or (c.enabled and c.base_per_unit)]
