"""
Billing: the sign-up trial, student places, prices and currencies, paying
through a provider (a fake one here), and Juwazi's admin endpoints.
"""
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Mapping
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from auth.database.schema.billing.billing_db import (
    Currency, OrgBillingPeriod, Payment, PaymentStatus, PeriodKind, Price, Product,
)
from auth.database.schema.user.user_db import UserModel
from auth.utility.payment.providers.base import (
    CheckoutRequest, CheckoutSession, InvalidWebhook, VerifiedPayment, WebhookNotice,
)
from .test_user_management import STUDENT_PAYLOAD, _create_student
from ..conftest import USER_EMAIL, engine, get_super_admin_token

H = dict[str, str]


def _auth(token: str) -> H:
    return {"Authorization": f"Bearer {token}"}


class FakeProvider:
    """Records checkouts; `outcome` decides what verify() reports."""
    name: str = "fake"

    def __init__(self) -> None:
        self.checkouts: list[CheckoutRequest] = []
        self.outcome: str = "successful"
        self.paid_amount: Decimal | None = None
        self.verify_calls: int = 0

    async def create_checkout(self, request: CheckoutRequest) -> CheckoutSession:
        self.checkouts.append(request)
        return CheckoutSession(url=f"https://pay.example/{request.reference}")

    async def verify(self, reference: str, provider_txn_id: str | None = None) -> VerifiedPayment:
        self.verify_calls += 1
        request = next(c for c in self.checkouts if c.reference == reference)
        return VerifiedPayment(
            reference=reference, state=self.outcome,  # type: ignore[arg-type]
            amount=self.paid_amount if self.paid_amount is not None else request.amount,
            currency=request.currency, provider_txn_id="txn-1", channel="bank_transfer",
        )

    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> WebhookNotice | None:
        if headers.get("verif-hash") != "secret":
            raise InvalidWebhook("bad")
        import json
        return WebhookNotice(reference=json.loads(body)["data"]["tx_ref"])


@pytest.fixture
def fake() -> Iterator[FakeProvider]:
    provider = FakeProvider()
    pick = lambda name=None: provider  # noqa: E731
    with patch("auth.api.v1.routes.billing.active_provider", pick), \
         patch("auth.api.v1.routes.billing.provider_named", pick), \
         patch("auth.services.billing.payments.provider_named", pick):
        yield provider


def _seed_prices() -> None:
    now = datetime.now(timezone.utc)
    with Session(engine) as s:
        for code, whole in (("NGN", False), ("USD", False), ("UGX", True)):
            s.add(Currency(code=code, name=code, enabled=code == "NGN", whole_units=whole, updated_at=now))
        for product, amount in ((Product.SCHOOL_SEAT, "3000"), (Product.TRAINING_SEAT, "1000"), (Product.EXAM_BODY_ACCESS, "1000")):
            s.add(Price(product=product, currency="NGN", amount=Decimal(amount), updated_at=now))
        s.commit()


def _owner(client: TestClient) -> str:
    token = get_super_admin_token(client)
    _seed_prices()
    return token


def _make_platform_admin(email: str = USER_EMAIL) -> None:
    with Session(engine) as s:
        user = s.exec(select(UserModel).where(UserModel.email == email)).one()
        user.is_platform_admin = True
        s.add(user)
        s.commit()


def _enforce(client: TestClient, token: str, on: bool = True) -> None:
    _make_platform_admin()
    r = client.patch("/api/v1/admin/settings", json={"enforce_limits": on}, headers=_auth(token))
    assert r.status_code == 200, r.json()


def _status(client: TestClient, token: str) -> dict[str, Any]:
    r = client.get("/api/v1/billing/status", headers=_auth(token))
    assert r.status_code == 200, r.json()
    return r.json()


# ------------------------------------------------------------ trial and places

def test_new_organisation_gets_a_30_day_trial_with_10_places(client: TestClient) -> None:
    st = _status(client, _owner(client))
    assert st["state"] == "trial" and st["seats"] == 10 and st["students"] == 0
    ends = datetime.fromisoformat(st["ends_at"])
    assert timedelta(days=29) < ends - datetime.now(timezone.utc) <= timedelta(days=30)
    assert st["product"] == "school_seat" and st["period_days"] == 120 and st["enforced"] is False


def test_places_are_only_enforced_once_switched_on(client: TestClient) -> None:
    token = _owner(client)
    for n in range(10):
        _create_student(client, token, phone=f"+23480000000{n:02d}", institution_id=f"S-{n}")
    # Off: an 11th is fine.
    _create_student(client, token, phone="+2348000000099", institution_id="S-99")
    _enforce(client, token)
    r = client.post("/api/v1/users/students/create", json={**STUDENT_PAYLOAD, "phone": "+2348000000100", "institution_id": "S-100"}, headers=_auth(token))
    assert r.status_code == 403 and "Not enough student places" in r.json()["detail"]
    assert _status(client, token)["can_add_students"] is False


def test_expired_trial_blocks_adding_students(client: TestClient) -> None:
    token = _owner(client)
    _enforce(client, token)
    with Session(engine) as s:
        for p in s.exec(select(OrgBillingPeriod)).all():
            p.ends_at = datetime.now(timezone.utc) - timedelta(days=1)
            s.add(p)
        s.commit()
    assert _status(client, token)["state"] == "expired"
    r = client.post("/api/v1/users/students/create", json=STUDENT_PAYLOAD, headers=_auth(token))
    assert r.status_code == 403 and "plan has ended" in r.json()["detail"]


# ------------------------------------------------------------ prices and currencies

def test_quote_in_naira_and_converted_currencies(client: TestClient) -> None:
    token = _owner(client)
    q = client.get("/api/v1/billing/quote", params={"seats": 250, "currency": "NGN"}, headers=_auth(token)).json()
    assert Decimal(q["unit_amount"]) == 3000 and Decimal(q["total"]) == 750_000      # the pricing page's example
    assert client.get("/api/v1/billing/quote", params={"seats": 1, "currency": "USD"}, headers=_auth(token)).status_code == 400

    _make_platform_admin()
    assert client.patch("/api/v1/admin/currencies/USD", json={"enabled": True}, headers=_auth(token)).status_code == 422
    client.patch("/api/v1/admin/currencies/USD", json={"enabled": True, "base_per_unit": "1600"}, headers=_auth(token))
    client.patch("/api/v1/admin/currencies/UGX", json={"enabled": True, "base_per_unit": "0.42"}, headers=_auth(token))
    usd = client.get("/api/v1/billing/quote", params={"seats": 2, "currency": "USD"}, headers=_auth(token)).json()
    assert Decimal(usd["unit_amount"]) == Decimal("1.88")        # 3000 / 1600 = 1.875, rounded up
    ugx = client.get("/api/v1/billing/quote", params={"seats": 1, "currency": "UGX"}, headers=_auth(token)).json()
    assert Decimal(ugx["unit_amount"]) == Decimal("7143")         # whole units
    # A price set in the currency itself wins over converting.
    client.put("/api/v1/admin/prices", json={"product": "school_seat", "currency": "USD", "amount": "2.00"}, headers=_auth(token))
    assert Decimal(client.get("/api/v1/billing/quote", params={"seats": 1, "currency": "USD"}, headers=_auth(token)).json()["unit_amount"]) == 2
    codes = [c["code"] for c in client.get("/api/v1/billing/currencies", headers=_auth(token)).json()]
    assert codes == ["NGN", "UGX", "USD"]


# ------------------------------------------------------------ paying

def _checkout(client: TestClient, token: str, seats: int = 50) -> dict[str, Any]:
    r = client.post("/api/v1/billing/checkout", json={"seats": seats, "currency": "NGN"}, headers=_auth(token))
    assert r.status_code == 200, r.json()
    return r.json()


def test_paying_for_places_after_the_provider_confirms(client: TestClient, fake: FakeProvider) -> None:
    token = _owner(client)
    c = _checkout(client, token)
    assert c["checkout_url"].startswith("https://pay.example/") and Decimal(c["amount"]) == 150_000
    assert _status(client, token)["state"] == "trial"            # nothing until confirmed

    paid = client.post(f"/api/v1/payments/{c['reference']}/confirm", headers=_auth(token)).json()
    assert paid["status"] == "successful" and paid["channel"] == "bank_transfer"
    st = _status(client, token)
    assert st["state"] == "active" and st["seats"] == 50
    assert timedelta(days=119) < datetime.fromisoformat(st["ends_at"]) - datetime.now(timezone.utc) <= timedelta(days=120)

    # Confirming again (return page + webhook) gives nothing twice.
    client.post(f"/api/v1/payments/{c['reference']}/confirm", headers=_auth(token))
    with Session(engine) as s:
        assert len(s.exec(select(OrgBillingPeriod).where(OrgBillingPeriod.kind == PeriodKind.PAID)).all()) == 1
    assert fake.verify_calls == 1


def test_more_places_mid_term_end_with_the_term(client: TestClient, fake: FakeProvider) -> None:
    token = _owner(client)
    client.post(f"/api/v1/payments/{_checkout(client, token, 20)['reference']}/confirm", headers=_auth(token))
    first_end = _status(client, token)["ends_at"]
    q = client.get("/api/v1/billing/quote", params={"seats": 5, "currency": "NGN"}, headers=_auth(token)).json()
    assert q["adding_to_current"] is True and q["ends_at"] == first_end
    client.post(f"/api/v1/payments/{_checkout(client, token, 5)['reference']}/confirm", headers=_auth(token))
    st = _status(client, token)
    assert st["seats"] == 25 and st["ends_at"] == first_end


def test_wrong_amount_or_failed_payment_gives_nothing(client: TestClient, fake: FakeProvider) -> None:
    token = _owner(client)
    fake.paid_amount = Decimal("100")
    r = client.post(f"/api/v1/payments/{_checkout(client, token)['reference']}/confirm", headers=_auth(token)).json()
    assert r["status"] == "mismatch"
    fake.paid_amount, fake.outcome = None, "failed"
    r = client.post(f"/api/v1/payments/{_checkout(client, token)['reference']}/confirm", headers=_auth(token)).json()
    assert r["status"] == "failed"
    fake.outcome = "pending"
    r = client.post(f"/api/v1/payments/{_checkout(client, token)['reference']}/confirm", headers=_auth(token)).json()
    assert r["status"] == "pending"
    assert _status(client, token)["state"] == "trial"


def test_webhook_needs_the_signature_and_reverifies(client: TestClient, fake: FakeProvider) -> None:
    token = _owner(client)
    ref = _checkout(client, token)["reference"]
    body = {"event": "charge.completed", "data": {"tx_ref": ref, "status": "successful"}}
    assert client.post("/api/v1/payments/webhook/fake", json=body, headers={"verif-hash": "nope"}).status_code == 401
    assert client.post("/api/v1/payments/webhook/fake", json=body, headers={"verif-hash": "secret"}).status_code == 200
    assert _status(client, token)["state"] == "active" and fake.verify_calls == 1


def test_only_the_organisation_can_confirm_its_payment(client: TestClient, fake: FakeProvider) -> None:
    token = _owner(client)
    ref = _checkout(client, token)["reference"]
    other = client.post("/api/v1/payments/jw_org_doesnotexist/confirm", headers=_auth(token))
    assert other.status_code == 404
    assert client.post(f"/api/v1/payments/{ref}/confirm").status_code == 401


# ------------------------------------------------------------ admin

def test_admin_endpoints_are_for_juwazi_only(client: TestClient) -> None:
    token = _owner(client)
    assert client.get("/api/v1/admin/me", headers=_auth(token)).json() == {"is_platform_admin": False}
    assert client.get("/api/v1/admin/settings", headers=_auth(token)).status_code == 403
    _make_platform_admin()
    assert client.get("/api/v1/admin/me", headers=_auth(token)).json() == {"is_platform_admin": True}
    s = client.get("/api/v1/admin/settings", headers=_auth(token)).json()
    assert s["trial_days"] == 30 and s["trial_student_limit"] == 10 and s["term_days"] == 120
    assert client.patch("/api/v1/admin/settings", json={"trial_days": -1}, headers=_auth(token)).status_code == 422
    assert client.patch("/api/v1/admin/settings", json={"nonsense": 1}, headers=_auth(token)).status_code == 422
    assert client.patch("/api/v1/admin/settings", json={"trial_days": 14}, headers=_auth(token)).json()["trial_days"] == 14


def test_admin_grants_places_and_sees_organisations(client: TestClient) -> None:
    token = _owner(client)
    _make_platform_admin()
    orgs = client.get("/api/v1/admin/organizations", headers=_auth(token)).json()
    org_id = orgs["items"][0]["id"]
    assert orgs["total"] == 1 and orgs["items"][0]["billing"]["state"] == "trial"
    r = client.post(f"/api/v1/admin/organizations/{org_id}/periods", json={"seats": 500, "days": 365, "note": "Invoice 42"}, headers=_auth(token))
    assert r.status_code == 200 and r.json()["kind"] == "manual"
    assert _status(client, token)["state"] == "active" and _status(client, token)["seats"] == 500
    assert len(client.get(f"/api/v1/admin/organizations/{org_id}/periods", headers=_auth(token)).json()) == 2


def test_exam_body_prices_per_body(client: TestClient) -> None:
    from auth.database.schema.organization.enums import OrganizationType, OrganizationVisibility
    from auth.database.schema.organization.organization_db import OrganizationModel
    from auth.services.billing.pricing import quote
    token = _owner(client)
    _make_platform_admin()
    with Session(engine) as s:
        owner = s.exec(select(UserModel).where(UserModel.email == USER_EMAIL)).one()
        body = OrganizationModel(
            name="WAEC", slug="waec", email="waec@example.com", phone="+2340", organization_type=OrganizationType.EXAM_BODY,
            visibility=OrganizationVisibility.PUBLIC, owner_user_id=owner.id,
        )
        s.add(body)
        s.commit()
        body_id = body.id
    r = client.put("/api/v1/admin/prices", json={"product": "exam_body_access", "exam_body_org_id": str(body_id), "currency": "NGN", "amount": "1500", "duration_days": 90}, headers=_auth(token))
    assert r.status_code == 200
    bodies = client.get("/api/v1/admin/exam-bodies", headers=_auth(token)).json()
    assert bodies[0]["name"] == "WAEC" and Decimal(bodies[0]["prices"][0]["amount"]) == 1500
    with Session(engine) as s:
        own = quote(s, Product.EXAM_BODY_ACCESS, "NGN", 1, body_id)
        default = quote(s, Product.EXAM_BODY_ACCESS, "NGN", 1, uuid4())
    assert (own.unit_amount, own.duration_days) == (Decimal("1500"), 90)
    assert default.unit_amount == Decimal("1000") and default.duration_days is None


def test_a_new_period_must_cover_existing_students(client: TestClient, fake: FakeProvider) -> None:
    token = _owner(client)
    for n in range(3):
        _create_student(client, token, phone=f"+23481000000{n:02d}", institution_id=f"M-{n}")
    r = client.get("/api/v1/billing/quote", params={"seats": 2, "currency": "NGN"}, headers=_auth(token))
    assert r.status_code == 400 and "at least 3" in r.json()["detail"]
    assert client.post("/api/v1/billing/checkout", json={"seats": 2, "currency": "NGN"}, headers=_auth(token)).status_code == 400
    client.post(f"/api/v1/payments/{_checkout(client, token, 3)['reference']}/confirm", headers=_auth(token))
    # Topping up a running period: any number.
    assert client.get("/api/v1/billing/quote", params={"seats": 1, "currency": "NGN"}, headers=_auth(token)).status_code == 200


def test_exam_starts_stop_only_when_an_enforced_plan_has_ended(client: TestClient) -> None:
    from auth.core.settings import settings as auth_settings
    token = _owner(client)
    with Session(engine) as s:
        org_id = s.exec(select(OrgBillingPeriod.org_id)).first()
    url = f"/api/v1/internal/orgs/{org_id}/exam-starts"
    h = {"X-Internal-Secret": auth_settings.INTERNAL_SECRET}
    assert client.get(url).status_code in (401, 403, 422)
    assert client.get(url, headers=h).json() == {"allowed": True, "reason": None}       # trial running
    with Session(engine) as s:
        for p in s.exec(select(OrgBillingPeriod)).all():
            p.ends_at = datetime.now(timezone.utc) - timedelta(days=1)
            s.add(p)
        s.commit()
    assert client.get(url, headers=h).json()["allowed"] is True                          # ended, not enforced
    _enforce(client, token)
    ended = client.get(url, headers=h).json()
    assert ended["allowed"] is False and "plan has ended" in ended["reason"]


def test_admin_makes_an_organisation_an_exam_body(client: TestClient) -> None:
    token = _owner(client)
    _make_platform_admin()
    oid = client.get("/api/v1/admin/organizations", headers=_auth(token)).json()["items"][0]["id"]
    r = client.patch(f"/api/v1/admin/organizations/{oid}/visibility", json={"visibility": "public"}, headers=_auth(token))
    assert r.status_code == 200 and r.json()["visibility"] == "public"
    assert client.get("/api/v1/admin/exam-bodies", headers=_auth(token)).json()[0]["id"] == oid
