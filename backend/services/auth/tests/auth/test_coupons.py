"""Coupons: Juwazi's discount codes on schools' and students' payments."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from auth.database.schema.billing.billing_db import (
    CouponRedemption, OrgBillingPeriod, Payment, PeriodKind, RedemptionStatus,
)
from .test_billing import FakeProvider, _auth, _make_platform_admin, _owner, _status
from .test_external_exams import _setup, fake  # noqa: F401  (fixture)
from ..conftest import engine

SEATS = "org_seats"
SELF = "exam_body_access"
BULK = "exam_body_bulk"


def _coupon(client: TestClient, token: str, **fields: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"code": "HALF", "kind": "percent", "percent_off": "50", "purposes": [SEATS, SELF, BULK]}
    body.update(fields)
    r = client.post("/api/v1/admin/coupons", json=body, headers=_auth(token))
    assert r.status_code == 201, r.json()
    return r.json()


def _admin(client: TestClient) -> str:
    token = _owner(client)
    _make_platform_admin()
    return token


def _my_org(client: TestClient, token: str) -> str:
    r = client.get("/api/v1/auth/organizations", headers=_auth(token)).json()
    return next(o["org_id"] for o in r if o.get("is_current"))


def test_only_juwazi_admins_make_coupons(client: TestClient) -> None:
    token = _owner(client)
    r = client.post("/api/v1/admin/coupons", json={"code": "X1X", "kind": "percent", "percent_off": "10", "purposes": [SEATS]}, headers=_auth(token))
    assert r.status_code == 403
    _make_platform_admin()
    c = _coupon(client, token, code="welcome-10", percent_off="10")
    assert c["code"] == "WELCOME-10" and c["uses"] == 0 and c["active"] is True
    # Codes are unique whatever the case; an amount coupon needs its currency.
    dup = client.post("/api/v1/admin/coupons", json={"code": "Welcome-10", "kind": "percent", "percent_off": "5", "purposes": [SEATS]}, headers=_auth(token))
    assert dup.status_code == 409
    no_cur = client.post("/api/v1/admin/coupons", json={"code": "OFF1", "kind": "amount", "amount_off": "500", "purposes": [SEATS]}, headers=_auth(token))
    assert no_cur.status_code == 400
    assert [x["code"] for x in client.get("/api/v1/admin/coupons", headers=_auth(token)).json()] == ["WELCOME-10"]


def test_percent_coupon_on_student_places(client: TestClient, fake: FakeProvider) -> None:  # noqa: F811
    token = _admin(client)
    _coupon(client, token)
    q = client.get("/api/v1/billing/quote", params={"seats": 10, "currency": "NGN", "coupon": "half"}, headers=_auth(token)).json()
    assert Decimal(q["total"]) == 30000 and Decimal(q["discount"]) == 15000 and Decimal(q["amount_due"]) == 15000
    assert q["coupon_code"] == "HALF"
    plain = client.get("/api/v1/billing/quote", params={"seats": 10, "currency": "NGN"}, headers=_auth(token)).json()
    assert Decimal(plain["amount_due"]) == 30000 and Decimal(plain["discount"]) == 0

    c = client.post("/api/v1/billing/checkout", json={"seats": 10, "currency": "NGN", "coupon_code": "HALF"}, headers=_auth(token)).json()
    assert Decimal(c["amount"]) == 15000 and fake.checkouts[-1].amount == Decimal(15000)
    with Session(engine) as s:
        assert s.exec(select(CouponRedemption)).one().status == RedemptionStatus.RESERVED
    paid = client.post(f"/api/v1/payments/{c['reference']}/confirm", headers=_auth(token)).json()
    assert paid["status"] == "successful" and "coupon HALF" in paid["description"]
    assert _status(client, token)["seats"] == 10
    with Session(engine) as s:
        r = s.exec(select(CouponRedemption)).one()
        assert r.status == RedemptionStatus.USED and r.discount == Decimal(15000)
    assert client.get("/api/v1/admin/coupons", headers=_auth(token)).json()[0]["uses"] == 1


def test_a_coupon_covering_everything_skips_the_payment_page(client: TestClient, fake: FakeProvider) -> None:  # noqa: F811
    token = _admin(client)
    _coupon(client, token, code="FREE", percent_off="100")
    c = client.post("/api/v1/billing/checkout", json={"seats": 5, "currency": "NGN", "coupon_code": "FREE"}, headers=_auth(token)).json()
    assert Decimal(c["amount"]) == 0 and c["checkout_url"].endswith(f"/app/subscription?reference={c['reference']}")
    assert fake.checkouts == []
    # Already given; confirming (as the return page does) just reports it.
    paid = client.post(f"/api/v1/payments/{c['reference']}/confirm", headers=_auth(token)).json()
    assert paid["status"] == "successful" and paid["provider"] == "coupon" and fake.verify_calls == 0
    with Session(engine) as s:
        period = s.exec(select(OrgBillingPeriod).where(OrgBillingPeriod.kind == PeriodKind.PAID)).one()
        assert period.seats == 5
        assert s.exec(select(CouponRedemption)).one().status == RedemptionStatus.USED


def test_failed_payment_frees_the_coupon(client: TestClient, fake: FakeProvider) -> None:  # noqa: F811
    token = _admin(client)
    _coupon(client, token, code="ONCE", max_redemptions=1)
    body = {"seats": 10, "currency": "NGN", "coupon_code": "ONCE"}
    c = client.post("/api/v1/billing/checkout", json=body, headers=_auth(token)).json()
    # Held while that checkout is open.
    held = client.post("/api/v1/billing/checkout", json=body, headers=_auth(token))
    assert held.status_code == 400 and "used up" in held.json()["detail"]
    fake.outcome = "failed"
    assert client.post(f"/api/v1/payments/{c['reference']}/confirm", headers=_auth(token)).json()["status"] == "failed"
    fake.outcome = "successful"
    again = client.post("/api/v1/billing/checkout", json=body, headers=_auth(token))
    assert again.status_code == 200


def test_limits_expiry_schools_and_payments(client: TestClient, fake: FakeProvider) -> None:  # noqa: F811
    token = _admin(client)
    org_id = _my_org(client, token)
    q = lambda code: client.get("/api/v1/billing/quote", params={"seats": 10, "currency": "NGN", "coupon": code}, headers=_auth(token))  # noqa: E731

    assert q("NOPE").json()["detail"] == "This coupon code isn't valid."
    c = _coupon(client, token, code="SOON", expires_at=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat())
    with Session(engine) as s:
        from auth.database.schema.billing.billing_db import Coupon
        row = s.get(Coupon, UUID(c["id"]))
        assert row is not None
        row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        s.add(row)
        s.commit()
    assert q("SOON").json()["detail"] == "This coupon has expired."

    _coupon(client, token, code="STUDENTS", purposes=[SELF])
    assert "can't be used to pay for student places" in q("STUDENTS").json()["detail"]

    other = _coupon(client, token, code="SPONSOR", org_ids=[])
    patched = client.patch(f"/api/v1/admin/coupons/{other['id']}", json={"org_ids": [org_id]}, headers=_auth(token))
    assert patched.status_code == 200 and patched.json()["org_ids"] == [org_id]
    assert q("SPONSOR").status_code == 200

    off = client.patch(f"/api/v1/admin/coupons/{other['id']}", json={"active": False}, headers=_auth(token))
    assert off.json()["active"] is False and q("SPONSOR").status_code == 400

    # Once per school.
    _coupon(client, token, code="ONEEACH", once_per_payer=True)
    c1 = client.post("/api/v1/billing/checkout", json={"seats": 10, "currency": "NGN", "coupon_code": "ONEEACH"}, headers=_auth(token)).json()
    client.post(f"/api/v1/payments/{c1['reference']}/confirm", headers=_auth(token))
    assert q("ONEEACH").json()["detail"] == "Your school has already used this coupon."

    # Amount coupons are for their own currency.
    _coupon(client, token, code="USD5", kind="amount", amount_off="5", currency="USD", percent_off=None)
    assert q("USD5").json()["detail"] == "This coupon is for payments in USD."

    # A used coupon can't be deleted, an unused one can.
    coupons = {x["code"]: x["id"] for x in client.get("/api/v1/admin/coupons", headers=_auth(token)).json()}
    assert client.delete(f"/api/v1/admin/coupons/{coupons['ONEEACH']}", headers=_auth(token)).status_code == 409
    assert client.delete(f"/api/v1/admin/coupons/{coupons['USD5']}", headers=_auth(token)).status_code == 204


def test_coupon_on_another_school_is_refused(client: TestClient) -> None:
    token = _admin(client)
    from auth.database.schema.organization.organization_db import OrganizationModel
    from auth.database.schema.organization.enums import OrganizationType
    from auth.database.schema.user.user_db import UserModel
    from ..conftest import USER_EMAIL
    with Session(engine) as s:
        owner = s.exec(select(UserModel).where(UserModel.email == USER_EMAIL)).one()
        school = OrganizationModel(name="Other School", slug="other-school", email="o@example.com", phone="+2341",
                                   organization_type=OrganizationType.SCHOOL, owner_user_id=owner.id)
        s.add(school)
        s.commit()
        other_id = str(school.id)
    _coupon(client, token, code="OTHER", org_ids=[other_id])
    r = client.get("/api/v1/billing/quote", params={"seats": 10, "currency": "NGN", "coupon": "OTHER"}, headers=_auth(token))
    assert r.json()["detail"] == "This coupon isn't for your school."


def test_amount_coupon_per_student_for_students_and_schools(client: TestClient, fake: FakeProvider) -> None:  # noqa: F811
    token, s_token, body_id = _setup(client)
    _make_platform_admin()
    _coupon(client, token, code="NGN400", kind="amount", amount_off="400", currency="NGN", percent_off=None, once_per_payer=True)

    # A student paying for themselves: ₦1,000 less ₦400.
    q = client.post(f"/api/v1/external/bodies/{body_id}/quote", json={"currency": "NGN", "coupon_code": "ngn400"}, headers=_auth(s_token)).json()
    assert Decimal(q["amount"]) == 1000 and Decimal(q["discount"]) == 400 and Decimal(q["amount_due"]) == 600
    c = client.post(f"/api/v1/external/bodies/{body_id}/subscribe", json={"currency": "NGN", "coupon_code": "NGN400"}, headers=_auth(s_token)).json()
    assert Decimal(c["amount"]) == 600
    assert client.post(f"/api/v1/payments/{c['reference']}/confirm", headers=_auth(s_token)).json()["status"] == "successful"
    again = client.post(f"/api/v1/external/bodies/{body_id}/quote", json={"currency": "NGN", "coupon_code": "NGN400"}, headers=_auth(s_token))
    assert again.json()["detail"] == "You've already used this coupon."

    # The school paying for its students: ₦400 off each. ("Once" is per payer, so the school still can.)
    req = {"exam_body_org_id": str(body_id), "currency": "NGN", "coupon_code": "NGN400"}
    bq = client.post("/api/v1/billing/exam-access/quote", json=req, headers=_auth(token)).json()
    n = bq["students"]
    assert Decimal(bq["discount"]) == 400 * n and Decimal(bq["amount_due"]) == 600 * n
    bc = client.post("/api/v1/billing/exam-access/checkout", json=req, headers=_auth(token)).json()
    assert Decimal(bc["amount"]) == 600 * n
    assert client.post(f"/api/v1/payments/{bc['reference']}/confirm", headers=_auth(token)).json()["status"] == "successful"
    with Session(engine) as s:
        assert len(s.exec(select(CouponRedemption).where(CouponRedemption.status == RedemptionStatus.USED)).all()) == 2
        assert all(p.details.get("coupon_code") == "NGN400" for p in s.exec(select(Payment)).all())
