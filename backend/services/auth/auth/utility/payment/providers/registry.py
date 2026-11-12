"""Which provider takes payments: the PAYMENT_PROVIDER setting."""
from auth.core.settings import settings

from .base import PaymentProvider, PaymentProviderError
from .flutterwave import FlutterwaveProvider
from .paystack import PaystackProvider


def provider_named(name: str) -> PaymentProvider:
    if name == "flutterwave":
        return FlutterwaveProvider(settings.FLUTTERWAVE_SECRET_KEY, settings.FLUTTERWAVE_WEBHOOK_HASH)
    if name == "paystack":
        return PaystackProvider(settings.PAYSTACK_SECRET_KEY)
    raise PaymentProviderError(f"Unknown payment provider: {name}")


def active_provider() -> PaymentProvider:
    return provider_named(settings.PAYMENT_PROVIDER)
