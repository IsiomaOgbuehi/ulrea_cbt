# auth/utility/payment/paystack.py
import httpx
import hmac
import hashlib
from auth.core.settings import settings


class PaystackClient:

    BASE_URL = "https://api.paystack.co"

    def __init__(self):
        self.secret_key = settings.PAYSTACK_SECRET_KEY
        self.headers = {
            "Authorization": f"Bearer {self.secret_key}",
            "Content-Type": "application/json",
        }

    async def initialize_payment(
        self,
        email: str,
        amount_kobo: int,           # Paystack uses kobo (100 kobo = 1 NGN)
        reference: str,
        callback_url: str,
        metadata: dict | None = None,
    ) -> dict:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.BASE_URL}/transaction/initialize",
                headers=self.headers,
                json={
                    "email": email,
                    "amount": amount_kobo,
                    "reference": reference,
                    "callback_url": callback_url,
                    "metadata": metadata or {},
                },
                timeout=10.0,
            )
            response.raise_for_status()
            return response.json()

    async def verify_payment(self, reference: str) -> dict:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.BASE_URL}/transaction/verify/{reference}",
                headers=self.headers,
                timeout=10.0,
            )
            response.raise_for_status()
            return response.json()

    def verify_webhook_signature(self, payload: bytes, signature: str) -> bool:
        """Verify Paystack webhook is genuinely from Paystack."""
        computed = hmac.new(
            key=self.secret_key.encode(),
            msg=payload,
            digestmod=hashlib.sha512,
        ).hexdigest()
        return hmac.compare_digest(computed, signature)


paystack = PaystackClient()