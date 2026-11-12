"""
One interface for every payment provider, so switching (Flutterwave,
Paystack, ...) is a setting, not a rewrite. The rest of the app only ever
talks to `PaymentProvider`.
"""
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal, Mapping, Protocol

PaymentState = Literal["successful", "failed", "pending"]


class PaymentProviderError(Exception):
    """The provider couldn't be reached or refused the request."""


class InvalidWebhook(Exception):
    """A webhook that didn't come from the provider (bad or missing signature)."""


@dataclass(frozen=True)
class CheckoutRequest:
    reference: str              # ours, unique per payment
    amount: Decimal             # in major units (naira, dollars...)
    currency: str               # ISO code: NGN, USD, GHS...
    email: str
    name: str
    redirect_url: str           # where the payer comes back to
    title: str = "Juwazi"
    description: str = ""
    meta: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CheckoutSession:
    url: str                    # the provider's hosted payment page


@dataclass(frozen=True)
class VerifiedPayment:
    """What the provider says happened, fetched by us from the provider."""
    reference: str
    state: PaymentState
    amount: Decimal
    currency: str
    provider_txn_id: str | None = None
    channel: str | None = None      # card, bank transfer, ussd, mobile money...


@dataclass(frozen=True)
class WebhookNotice:
    """A provider's notice that something happened to a payment. Never trusted
    on its own: the payment is always re-verified with the provider."""
    reference: str | None
    provider_txn_id: str | None = None


class PaymentProvider(Protocol):
    name: str

    async def create_checkout(self, request: CheckoutRequest) -> CheckoutSession: ...

    async def verify(self, reference: str, provider_txn_id: str | None = None) -> VerifiedPayment: ...

    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> WebhookNotice | None: ...
