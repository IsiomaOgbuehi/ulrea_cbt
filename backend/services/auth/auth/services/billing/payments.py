"""
Taking payments, whatever the provider.

A payment is only ever counted after *we* ask the provider about it and the
reference, amount and currency all match what we asked for. The return page
and the webhook both go through `confirm()`, which locks the payment row, so
a payment is fulfilled exactly once however many times it's confirmed.
"""
import logging
import secrets
from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool
from sqlmodel import Session, select

from auth.database.schema.billing.billing_db import Payment, PaymentPurpose, PaymentStatus
from auth.utility.payment.providers.registry import provider_named
from auth.utility.payment.providers.base import (
    CheckoutRequest, PaymentProvider, PaymentProviderError, VerifiedPayment,
)

logger: logging.Logger = logging.getLogger(__name__)

# purpose -> what to do once a payment of that purpose is confirmed.
Fulfiller = Callable[[Session, Payment], None]
_fulfillers: dict[PaymentPurpose, Fulfiller] = {}


def register_fulfiller(purpose: PaymentPurpose, fn: Fulfiller) -> None:
    _fulfillers[purpose] = fn


def new_reference(prefix: str) -> str:
    return f"jw_{prefix}_{secrets.token_hex(10)}"


def _record(session: Session, payment: Payment) -> Payment:
    session.add(payment)
    session.commit()          # also ends the transaction: no connection held while the provider is called
    session.refresh(payment)
    return payment


def _mark_failed(session: Session, payment_id: UUID, reason: str) -> None:
    payment = session.get(Payment, payment_id)
    if payment is not None and payment.status == PaymentStatus.PENDING:
        payment.status = PaymentStatus.FAILED
        payment.failure_reason = reason[:500]
        session.add(payment)
    session.commit()


async def start_checkout(
    session: Session,
    provider: PaymentProvider,
    *,
    purpose: PaymentPurpose,
    user_id: UUID,
    org_id: UUID | None,
    email: str,
    name: str,
    amount: Decimal,
    currency: str,
    description: str,
    details: dict[str, Any],
    redirect_url: str,
) -> tuple[Payment, str]:
    """Record the payment, then get the provider's hosted checkout page.
    Database work runs in the thread pool; no connection is held while waiting
    on the provider."""
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Nothing to pay.")
    payment = await run_in_threadpool(_record, session, Payment(
        reference=new_reference(purpose.value.split("_")[0]),
        provider=provider.name, purpose=purpose, user_id=user_id, org_id=org_id,
        amount=amount, currency=currency.upper(), description=description, details=details,
    ))
    try:
        checkout = await provider.create_checkout(CheckoutRequest(
            reference=payment.reference, amount=amount, currency=payment.currency,
            email=email, name=name, redirect_url=redirect_url, description=description,
            meta={"purpose": purpose.value, "payment_id": str(payment.id)},
        ))
    except PaymentProviderError as e:
        await run_in_threadpool(_mark_failed, session, payment.id, str(e))
        logger.warning("Checkout failed for %s: %s", payment.reference, e)
        raise HTTPException(status_code=502, detail="The payment service is unavailable. Please try again shortly.")
    return payment, checkout.url


def _matches(payment: Payment, verified: VerifiedPayment) -> bool:
    return (
        verified.reference == payment.reference
        and verified.currency == payment.currency
        and verified.amount >= Decimal(payment.amount)
    )


def _find(session: Session, reference: str) -> Payment | None:
    payment = session.exec(select(Payment).where(Payment.reference == reference)).first()
    session.commit()          # end the read transaction before calling the provider
    return payment


def _settle(session: Session, payment_id: UUID, verified: VerifiedPayment) -> Payment:
    """Record what the provider said; fulfil once. Row-locked."""
    locked = session.exec(select(Payment).where(Payment.id == payment_id).with_for_update()).one()
    if locked.status != PaymentStatus.PENDING or verified.state == "pending":
        session.commit()
        return locked
    locked.provider_txn_id = verified.provider_txn_id
    locked.channel = verified.channel
    if verified.state == "failed":
        locked.status = PaymentStatus.FAILED
        locked.failure_reason = "Declined or cancelled."
    elif not _matches(locked, verified):
        locked.status = PaymentStatus.MISMATCH
        locked.failure_reason = f"Provider reported {verified.amount} {verified.currency}."
        logger.error("Payment %s doesn't match: %s", locked.reference, locked.failure_reason)
    else:
        locked.status = PaymentStatus.SUCCESSFUL
        locked.paid_at = datetime.now(timezone.utc)
        fulfil = _fulfillers.get(locked.purpose)
        if fulfil is not None:
            fulfil(session, locked)      # same transaction: paid and given together
    session.add(locked)
    session.commit()
    session.refresh(locked)
    return locked


async def confirm(session: Session, reference: str) -> Payment:
    """Ask the provider about a payment and, the first time it's confirmed
    paid, give what was bought. Safe to call any number of times. Uses the
    provider the payment was started with, even if the setting changed since."""
    payment = await run_in_threadpool(_find, session, reference)
    if payment is None:
        raise HTTPException(status_code=404, detail="Payment not found.")
    if payment.status != PaymentStatus.PENDING:
        return payment
    try:
        verified = await provider_named(payment.provider).verify(reference)
    except PaymentProviderError as e:
        logger.warning("Couldn't verify %s: %s", reference, e)
        return payment                    # still pending; the webhook or a later check settles it
    return await run_in_threadpool(_settle, session, payment.id, verified)
