"""Refer and earn: share links, commissions on friends' payments, withdrawals."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from auth.database.schema.referral.referral_db import CommissionEntry, EntryKind, Referral
from auth.database.schema.user.user_db import UserModel
from auth.services import referrals
from .test_billing import FakeProvider, _auth, _make_platform_admin
from .test_external_exams import _setup, fake  # noqa: F401  (fixture)
from .test_user_management import STUDENT_ANSWER, _create_active_student, _student_login
from ..conftest import engine


def _friend(client: TestClient, staff_token: str) -> tuple[str, UUID]:
    """A second student (the friend): their token and id."""
    s = _create_active_student(client, staff_token, phone="+1000000099", institution_id="STU/2024/099", firstname="Ada")
    token: str = _student_login(client, s["access_code"], STUDENT_ANSWER).json()["access_token"]
    with Session(engine) as db:
        uid = db.exec(select(UserModel.id).where(UserModel.access_code == s["access_code"])).one()
    return token, uid


def _refer(code: str, friend_id: UUID) -> None:
    with Session(engine) as db:
        friend = db.get(UserModel, friend_id)
        assert friend is not None
        assert referrals.attach(db, friend, code) is not None


def _pay(client: TestClient, token: str, body_id: UUID) -> dict[str, Any]:
    c = client.post(f"/api/v1/external/bodies/{body_id}/subscribe", json={"currency": "NGN"}, headers=_auth(token)).json()
    return client.post(f"/api/v1/payments/{c['reference']}/confirm", headers=_auth(token)).json()


def _settings(client: TestClient, token: str, **changes: Any) -> None:
    _make_platform_admin()
    assert client.patch("/api/v1/admin/settings", json=changes, headers=_auth(token)).status_code == 200


def test_link_and_the_invite_page(client: TestClient) -> None:
    staff, s_token, _ = _setup(client)
    me = client.get("/api/v1/referrals/me", headers=_auth(s_token)).json()
    assert len(me["code"]) == 8 and me["link"].endswith(f"/join?ref={me['code']}")
    assert me["percent"] == 10 and me["months"] == 0 and Decimal(me["balance"]) == 0 and me["can_withdraw"] is False
    # Same code every time; anyone can see whose invite it is (first name only).
    assert client.get("/api/v1/referrals/me", headers=_auth(s_token)).json()["code"] == me["code"]
    assert client.get(f"/api/v1/referrals/invite/{me['code'].lower()}").json() == {"referrer_first_name": "Charlie"}
    assert client.get("/api/v1/referrals/invite/NOPE1234").status_code == 404
    # Staff don't refer.
    assert client.get("/api/v1/referrals/me", headers=_auth(staff)).status_code == 403


def test_signing_up_through_a_link(client: TestClient) -> None:
    _, s_token, _ = _setup(client)
    code = client.get("/api/v1/referrals/me", headers=_auth(s_token)).json()["code"]
    body = {"firstname": "Bola", "lastname": "Friend", "email": "bola@example.com", "password": "Passw0rd!x", "confirm_password": "Passw0rd!x"}
    r = client.post("/api/v1/student/signup", json={**body, "referral_code": code})
    assert r.status_code in (200, 201), r.json()
    # A bad code never stops a sign-up.
    r2 = client.post("/api/v1/student/signup", json={**body, "email": "c@example.com", "referral_code": "WRONG"})
    assert r2.status_code in (200, 201), r2.json()
    with Session(engine) as db:
        rows = db.exec(select(Referral)).all()
        assert len(rows) == 1
        bola = db.exec(select(UserModel).where(UserModel.email == "bola@example.com")).one()
        assert rows[0].referred_id == bola.id
        # Nobody is referred twice, or by themselves.
        assert referrals.attach(db, bola, code) is None
        me = db.get(UserModel, rows[0].referrer_id)
        assert me is not None and referrals.attach(db, me, code) is None
    assert client.get("/api/v1/referrals/me", headers=_auth(s_token)).json()["friends"] == 1


def test_commission_on_every_friend_payment(client: TestClient, fake: FakeProvider) -> None:  # noqa: F811
    staff, s_token, body_id = _setup(client)
    code = client.get("/api/v1/referrals/me", headers=_auth(s_token)).json()["code"]
    f_token, f_id = _friend(client, staff)
    _refer(code, f_id)

    assert _pay(client, f_token, body_id)["status"] == "successful"
    assert _pay(client, f_token, body_id)["status"] == "successful"
    me = client.get("/api/v1/referrals/me", headers=_auth(s_token)).json()
    assert Decimal(me["balance"]) == 200 and Decimal(me["total_earned"]) == 200      # 10% of ₦1,000, twice
    entries = client.get("/api/v1/referrals/me/entries", headers=_auth(s_token)).json()
    assert entries["total"] == 2 and entries["items"][0]["kind"] == "commission"
    assert "Ada's payment" in entries["items"][0]["description"]
    # The friend paying earns themselves nothing; confirming again adds nothing.
    assert Decimal(client.get("/api/v1/referrals/me", headers=_auth(f_token)).json()["balance"]) == 0
    with Session(engine) as db:
        assert len(db.exec(select(CommissionEntry)).all()) == 2


def test_months_limit_and_switching_off(client: TestClient, fake: FakeProvider) -> None:  # noqa: F811
    staff, s_token, body_id = _setup(client)
    code = client.get("/api/v1/referrals/me", headers=_auth(s_token)).json()["code"]
    f_token, f_id = _friend(client, staff)
    _refer(code, f_id)
    _settings(client, staff, referral_months=3)
    with Session(engine) as db:
        ref = db.exec(select(Referral)).one()
        ref.created_at = datetime.now(timezone.utc) - timedelta(days=100)
        db.add(ref)
        db.commit()
    _pay(client, f_token, body_id)
    assert Decimal(client.get("/api/v1/referrals/me", headers=_auth(s_token)).json()["balance"]) == 0
    _settings(client, staff, referral_months=0, referral_percent=0)
    _pay(client, f_token, body_id)
    assert Decimal(client.get("/api/v1/referrals/me", headers=_auth(s_token)).json()["balance"]) == 0
    _settings(client, staff, referral_percent=25)
    _pay(client, f_token, body_id)
    assert Decimal(client.get("/api/v1/referrals/me", headers=_auth(s_token)).json()["balance"]) == 250


def _give(user_token: str, client: TestClient, amount: str) -> UUID:
    """Put commission into the student's account directly."""
    with Session(engine) as db:
        code = client.get("/api/v1/referrals/me", headers=_auth(user_token)).json()["code"]
        user = referrals.referrer_by_code(db, code)
        assert user is not None
        db.add(CommissionEntry(user_id=user.id, kind=EntryKind.ADJUSTMENT, amount=Decimal(amount), currency="NGN", description="test"))
        db.commit()
        return user.id


SELF = {"holder": "self", "adult_confirmed": True, "account_name": "Charlie Student", "account_number": "0123456789", "bank_name": "GTBank"}


def test_withdrawal_rules(client: TestClient) -> None:
    _, s_token, _ = _setup(client)
    w = lambda body: client.post("/api/v1/referrals/me/withdrawals", json=body, headers=_auth(s_token))  # noqa: E731
    _give(s_token, client, "3000")
    assert "least you can withdraw" in w({**SELF, "amount": "3000"}).json()["detail"]
    _give(s_token, client, "4000")
    me = client.get("/api/v1/referrals/me", headers=_auth(s_token)).json()
    assert Decimal(me["balance"]) == 7000 and me["can_withdraw"] is True

    assert "18 or older" in w({**SELF, "adult_confirmed": False, "amount": "5000"}).json()["detail"]
    guardian = {**SELF, "holder": "guardian", "adult_confirmed": False, "account_name": "Mrs Student"}
    assert "related" in w({**guardian, "amount": "5000"}).json()["detail"]
    assert "digits" in w({**SELF, "account_number": "01234-567", "amount": "5000"}).json()["detail"]
    assert "You have 7,000.00" in w({**SELF, "amount": "8000"}).json()["detail"]

    ok = w({**guardian, "guardian_relationship": "Mother", "amount": "5000"})
    assert ok.status_code == 201 and ok.json()["account_number_last4"] == "6789" and ok.json()["status"] == "requested"
    me = client.get("/api/v1/referrals/me", headers=_auth(s_token)).json()
    assert Decimal(me["balance"]) == 2000 and me["can_withdraw"] is False and me["waiting_withdrawal"]["id"] == ok.json()["id"]
    assert w({**SELF, "amount": "5000"}).status_code == 409


def test_juwazi_pays_or_returns_withdrawals(client: TestClient) -> None:
    staff, s_token, _ = _setup(client)
    _give(s_token, client, "12000")
    first = client.post("/api/v1/referrals/me/withdrawals", json={**SELF, "amount": "5000"}, headers=_auth(s_token)).json()

    assert client.get("/api/v1/admin/withdrawals", headers=_auth(staff)).status_code == 403
    _make_platform_admin()
    waiting = client.get("/api/v1/admin/withdrawals", params={"status": "requested"}, headers=_auth(staff)).json()
    assert waiting["total"] == 1 and waiting["items"][0]["account_number"] == "0123456789"
    assert waiting["items"][0]["student_name"] == "Charlie Student" and Decimal(waiting["items"][0]["balance_after"]) == 7000

    paid = client.post(f"/api/v1/admin/withdrawals/{first['id']}/paid", json={"reference": "GTB-TRF-001"}, headers=_auth(staff))
    assert paid.status_code == 200 and paid.json()["status"] == "paid"
    assert client.post(f"/api/v1/admin/withdrawals/{first['id']}/reject", json={"reason": "late"}, headers=_auth(staff)).status_code == 409

    second = client.post("/api/v1/referrals/me/withdrawals", json={**SELF, "amount": "6000"}, headers=_auth(s_token)).json()
    r = client.post(f"/api/v1/admin/withdrawals/{second['id']}/reject", json={"reason": "Account name doesn't match"}, headers=_auth(staff))
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    me = client.get("/api/v1/referrals/me", headers=_auth(s_token)).json()
    assert Decimal(me["balance"]) == 7000          # 12,000 − 5,000 paid; the 6,000 came back
    kinds = [e["kind"] for e in client.get("/api/v1/referrals/me/entries", headers=_auth(s_token)).json()["items"]]
    assert kinds.count("withdrawal") == 2 and kinds.count("withdrawal_returned") == 1
    mine = client.get("/api/v1/referrals/me/withdrawals", headers=_auth(s_token)).json()
    assert [x["status"] for x in mine] == ["rejected", "paid"] and mine[0]["reject_reason"] == "Account name doesn't match"
