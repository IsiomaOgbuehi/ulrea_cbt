"""Paystack, kept so switching back is a setting."""
import hashlib
import hmac
import json
from decimal import Decimal
from typing import Any, Mapping

import httpx

from .base import (
    CheckoutRequest, CheckoutSession, InvalidWebhook, PaymentProviderError, PaymentState,
    VerifiedPayment, WebhookNotice,
)


class PaystackProvider:
    name: str = "paystack"
    BASE_URL: str = "https://api.paystack.co"

    def __init__(self, secret_key: str) -> None:
        self._secret_key = secret_key

    def _headers(self) -> dict[str, str]:
        if not self._secret_key:
            raise PaymentProviderError("Paystack isn't set up (PAYSTACK_SECRET_KEY is empty).")
        return {"Authorization": f"Bearer {self._secret_key}", "Content-Type": "application/json"}

    async def _call(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.request(method, f"{self.BASE_URL}{path}", headers=self._headers(), **kwargs)
        except httpx.HTTPError as e:
            raise PaymentProviderError(f"Couldn't reach Paystack: {e}") from e
        try:
            data: dict[str, Any] = response.json()
        except ValueError as e:
            raise PaymentProviderError(f"Paystack replied with {response.status_code}") from e
        if response.status_code >= 400 or not data.get("status"):
            raise PaymentProviderError(str(data.get("message") or f"Paystack replied with {response.status_code}"))
        return data

    async def create_checkout(self, request: CheckoutRequest) -> CheckoutSession:
        data = await self._call("POST", "/transaction/initialize", json={
            "email": request.email,
            "amount": int((request.amount * 100).to_integral_value()),   # subunits
            "currency": request.currency,
            "reference": request.reference,
            "callback_url": request.redirect_url,
            "metadata": dict(request.meta),
        })
        return CheckoutSession(url=str(data["data"]["authorization_url"]))

    async def verify(self, reference: str, provider_txn_id: str | None = None) -> VerifiedPayment:
        data = await self._call("GET", f"/transaction/verify/{reference}")
        tx: dict[str, Any] = data.get("data") or {}
        status = str(tx.get("status", "")).lower()
        state: PaymentState = (
            "successful" if status == "success"
            else "failed" if status in ("failed", "abandoned", "reversed")
            else "pending"
        )
        return VerifiedPayment(
            reference=str(tx.get("reference") or reference),
            state=state,
            amount=Decimal(str(tx.get("amount", 0))) / 100,
            currency=str(tx.get("currency", "")).upper(),
            provider_txn_id=str(tx["id"]) if tx.get("id") is not None else provider_txn_id,
            channel=str(tx.get("channel")) if tx.get("channel") else None,
        )

    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> WebhookNotice | None:
        sent = headers.get("x-paystack-signature") or ""
        computed = hmac.new(self._secret_key.encode(), body, hashlib.sha512).hexdigest()
        if not self._secret_key or not hmac.compare_digest(sent, computed):
            raise InvalidWebhook("Bad Paystack webhook signature.")
        try:
            payload: dict[str, Any] = json.loads(body)
        except ValueError:
            return None
        data: dict[str, Any] = payload.get("data") or {}
        reference = data.get("reference")
        return WebhookNotice(reference=str(reference), provider_txn_id=str(data.get("id"))) if reference else None
