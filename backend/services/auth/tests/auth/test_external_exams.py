"""External Exams: students subscribe to an exam body's practice exams."""
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import patch
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from auth.core.settings import settings as auth_settings
from auth.database.schema.organization.enums import OrganizationType, OrganizationVisibility
from auth.database.schema.organization.organization_db import OrganizationModel
from auth.database.schema.user.user_db import UserModel
from .test_billing import FakeProvider, _auth, _seed_prices
from .test_user_management import STUDENT_ANSWER, _create_active_student, _student_login
from ..conftest import USER_EMAIL, engine, get_super_admin_token


@pytest.fixture
def fake() -> Iterator[FakeProvider]:
    provider = FakeProvider()
    pick = lambda name=None: provider  # noqa: E731
    with patch("auth.api.v1.routes.external.active_provider", pick), \
         patch("auth.api.v1.routes.billing.active_provider", pick), \
         patch("auth.api.v1.routes.billing.provider_named", pick), \
         patch("auth.services.billing.payments.provider_named", pick):
        yield provider


def _setup(client: TestClient) -> tuple[str, str, UUID]:
    """(staff token, student token, exam body id)."""
    token = get_super_admin_token(client)
    _seed_prices()
    student = _create_active_student(client, token)
    s_token: str = _student_login(client, student["access_code"], STUDENT_ANSWER).json()["access_token"]
    with Session(engine) as s:
        owner = s.exec(select(UserModel).where(UserModel.email == USER_EMAIL)).one()
        body = OrganizationModel(
            name="WAEC", slug="waec", email="waec@example.com", phone="+2340", organization_type=OrganizationType.EXAM_BODY,
            visibility=OrganizationVisibility.PUBLIC, owner_user_id=owner.id,
        )
        s.add(body)
        s.commit()
        return token, s_token, body.id


def _access(client: TestClient, student_token: str, body_id: UUID) -> dict[str, Any]:
    with Session(engine) as s:
        student_id = s.exec(select(UserModel.id).where(UserModel.access_code.is_not(None))).first()  # type: ignore[union-attr]
    r = client.get(
        "/api/v1/internal/exam-access", params={"student_id": str(student_id), "exam_body_org_id": str(body_id)},
        headers={"X-Internal-Secret": auth_settings.INTERNAL_SECRET},
    )
    return r.json()


def test_students_see_exam_bodies_and_subscribe(client: TestClient, fake: FakeProvider) -> None:
    token, s_token, body_id = _setup(client)
    bodies = client.get("/api/v1/external/bodies", headers=_auth(s_token)).json()
    assert [b["name"] for b in bodies] == ["WAEC"]
    assert bodies[0]["active"] is False and Decimal(bodies[0]["price"]["amount"]) == 1000 and bodies[0]["price"]["days"] == 30
    assert _access(client, s_token, body_id) == {"public": True, "allowed": False, "expires_at": None}

    c = client.post(f"/api/v1/external/bodies/{body_id}/subscribe", json={"currency": "NGN"}, headers=_auth(s_token))
    assert c.status_code == 200 and Decimal(c.json()["amount"]) == 1000
    paid = client.post(f"/api/v1/payments/{c.json()['reference']}/confirm", headers=_auth(s_token)).json()
    assert paid["status"] == "successful"
    card = client.get("/api/v1/external/bodies", headers=_auth(s_token)).json()[0]
    ends = datetime.fromisoformat(card["expires_at"])
    assert card["active"] is True and timedelta(days=29) < ends - datetime.now(timezone.utc) <= timedelta(days=30)
    assert _access(client, s_token, body_id)["allowed"] is True

    # Paying again before it ends adds 30 days to the end.
    c2 = client.post(f"/api/v1/external/bodies/{body_id}/subscribe", json={"currency": "NGN"}, headers=_auth(s_token)).json()
    client.post(f"/api/v1/payments/{c2['reference']}/confirm", headers=_auth(s_token))
    later = datetime.fromisoformat(client.get("/api/v1/external/bodies", headers=_auth(s_token)).json()[0]["expires_at"])
    assert timedelta(days=59) < later - datetime.now(timezone.utc) <= timedelta(days=60)

    # Staff can't use it; private organisations aren't exam bodies.
    assert client.get("/api/v1/external/bodies", headers=_auth(token)).status_code == 403


def test_own_price_per_exam_body(client: TestClient, fake: FakeProvider) -> None:
    from auth.database.schema.billing.billing_db import Price, Product
    _, s_token, body_id = _setup(client)
    with Session(engine) as s:
        s.add(Price(product=Product.EXAM_BODY_ACCESS, exam_body_org_id=body_id, currency="NGN", amount=Decimal("2500"), duration_days=90, updated_at=datetime.now(timezone.utc)))
        s.commit()
    card = client.get("/api/v1/external/bodies", headers=_auth(s_token)).json()[0]
    assert Decimal(card["price"]["amount"]) == 2500 and card["price"]["days"] == 90


def test_new_badge_until_opened(client: TestClient) -> None:
    _, s_token, _ = _setup(client)
    assert client.get("/api/v1/external/seen", headers=_auth(s_token)).json() == {"seen": False}
    client.post("/api/v1/external/seen", headers=_auth(s_token))
    assert client.get("/api/v1/external/seen", headers=_auth(s_token)).json() == {"seen": True}


def test_one_free_trial_per_exam_body(client: TestClient, fake: FakeProvider) -> None:
    from .test_billing import _make_platform_admin
    token, s_token, body_id = _setup(client)
    card = client.get("/api/v1/external/bodies", headers=_auth(s_token)).json()[0]
    assert card["trial_days"] == 10 and card["active"] is False

    r = client.post(f"/api/v1/external/bodies/{body_id}/trial", headers=_auth(s_token))
    assert r.status_code == 200 and r.json()["on_trial"] is True
    ends = datetime.fromisoformat(r.json()["expires_at"])
    assert timedelta(days=9) < ends - datetime.now(timezone.utc) <= timedelta(days=10)
    assert _access(client, s_token, body_id)["allowed"] is True
    assert client.post(f"/api/v1/external/bodies/{body_id}/trial", headers=_auth(s_token)).status_code == 409
    card = client.get("/api/v1/external/bodies", headers=_auth(s_token)).json()[0]
    assert card["trial_days"] == 0 and card["on_trial"] is True

    # Paying during the trial adds the paid days after it, and it's no longer a trial.
    c = client.post(f"/api/v1/external/bodies/{body_id}/subscribe", json={"currency": "NGN"}, headers=_auth(s_token)).json()
    client.post(f"/api/v1/payments/{c['reference']}/confirm", headers=_auth(s_token))
    card = client.get("/api/v1/external/bodies", headers=_auth(s_token)).json()[0]
    assert card["on_trial"] is False
    assert timedelta(days=39) < datetime.fromisoformat(card["expires_at"]) - datetime.now(timezone.utc) <= timedelta(days=40)

    # Juwazi can change the length, or switch trials off.
    _make_platform_admin()
    assert client.patch("/api/v1/admin/settings", json={"external_trial_days": 0}, headers=_auth(token)).json()["external_trial_days"] == 0


def _hide(body_id: UUID) -> None:
    with Session(engine) as s:
        body = s.get(OrganizationModel, body_id)
        assert body is not None
        body.visibility = OrganizationVisibility.PRIVATE
        s.add(body)
        s.commit()


def test_hidden_body_keeps_paid_students_but_stops_selling(client: TestClient, fake: FakeProvider) -> None:
    _, s_token, body_id = _setup(client)
    c = client.post(f"/api/v1/external/bodies/{body_id}/subscribe", json={"currency": "NGN"}, headers=_auth(s_token)).json()
    client.post(f"/api/v1/payments/{c['reference']}/confirm", headers=_auth(s_token))
    _hide(body_id)

    # Still listed for this student, and they can still practise until it ends...
    card = client.get("/api/v1/external/bodies", headers=_auth(s_token)).json()[0]
    assert card["name"] == "WAEC" and card["active"] is True and card["expires_at"] is not None
    assert card["on_sale"] is False and card["price"] is None and card["trial_days"] == 0
    access = _access(client, s_token, body_id)
    assert access["public"] is True and access["allowed"] is True

    # ...but it sells nothing more, not even extra days.
    r = client.post(f"/api/v1/external/bodies/{body_id}/subscribe", json={"currency": "NGN"}, headers=_auth(s_token))
    assert r.status_code == 409


def test_hidden_body_ends_free_trials_and_hides_from_others(client: TestClient, fake: FakeProvider) -> None:
    _, s_token, body_id = _setup(client)
    assert client.post(f"/api/v1/external/bodies/{body_id}/trial", headers=_auth(s_token)).status_code == 200
    _hide(body_id)

    # A free trial isn't paid access: the body disappears and practice stops.
    assert client.get("/api/v1/external/bodies", headers=_auth(s_token)).json() == []
    assert _access(client, s_token, body_id) == {"public": False, "allowed": False, "expires_at": None}
    assert client.post(f"/api/v1/external/bodies/{body_id}/trial", headers=_auth(s_token)).status_code == 404
    assert client.post(
        f"/api/v1/external/bodies/{body_id}/subscribe", json={"currency": "NGN"}, headers=_auth(s_token),
    ).status_code == 404


def test_school_pays_for_its_students(client: TestClient, fake: FakeProvider) -> None:
    from auth.database.schema.exam_subscription.enum import SubscribedBy
    from auth.database.schema.exam_subscription.exam_subscription_db import ExamBodySubscription
    token, s_token, body_id = _setup(client)
    bodies = client.get("/api/v1/billing/exam-access/bodies", headers=_auth(token)).json()
    assert [(b["name"], Decimal(b["unit_amount"]), b["students_with_access"]) for b in bodies] == [("WAEC", 1000, 0)]

    req = {"exam_body_org_id": str(body_id), "currency": "NGN"}
    q = client.post("/api/v1/billing/exam-access/quote", json=req, headers=_auth(token)).json()
    n = q["students"]
    assert n >= 1 and Decimal(q["total"]) == 1000 * n and q["already_have_access"] == 0 and q["days"] == 30

    c = client.post("/api/v1/billing/exam-access/checkout", json=req, headers=_auth(token)).json()
    assert Decimal(c["amount"]) == 1000 * n
    assert client.post(f"/api/v1/payments/{c['reference']}/confirm", headers=_auth(token)).json()["status"] == "successful"

    card = client.get("/api/v1/external/bodies", headers=_auth(s_token)).json()[0]
    assert card["active"] is True and card["on_trial"] is False
    with Session(engine) as s:
        subs = s.exec(select(ExamBodySubscription).where(ExamBodySubscription.exam_body_org_id == body_id)).all()
        assert len(subs) == n and all(x.subscribed_by == SubscribedBy.ORGANIZATION for x in subs)
    bodies = client.get("/api/v1/billing/exam-access/bodies", headers=_auth(token)).json()
    assert bodies[0]["students_with_access"] == n

    # Paying again adds the days to the end; the quote says who has access already.
    q2 = client.post("/api/v1/billing/exam-access/quote", json=req, headers=_auth(token)).json()
    assert q2["already_have_access"] == n
    c2 = client.post("/api/v1/billing/exam-access/checkout", json=req, headers=_auth(token)).json()
    client.post(f"/api/v1/payments/{c2['reference']}/confirm", headers=_auth(token))
    later = datetime.fromisoformat(client.get("/api/v1/external/bodies", headers=_auth(s_token)).json()[0]["expires_at"])
    assert timedelta(days=59) < later - datetime.now(timezone.utc) <= timedelta(days=60)

    # Students can't pay for others; a class with nobody in it has nothing to pay for.
    assert client.post("/api/v1/billing/exam-access/quote", json=req, headers=_auth(s_token)).status_code == 403
    from uuid import uuid4
    empty = client.post("/api/v1/billing/exam-access/quote", json={**req, "cohort_id": str(uuid4())}, headers=_auth(token))
    assert empty.status_code == 400


def test_internal_exam_bodies_and_question_library(client: TestClient) -> None:
    token, _, body_id = _setup(client)
    h = {"X-Internal-Secret": auth_settings.INTERNAL_SECRET}
    bodies = client.get("/api/v1/internal/exam-bodies", headers=h).json()
    assert {"id": str(body_id), "name": "WAEC"} in bodies
    with Session(engine) as s:
        owner = s.exec(select(UserModel).where(UserModel.email == USER_EMAIL)).one()
        from auth.database.schema.membership.membership_db import OrgMembership
        school = s.exec(select(OrgMembership.org_id).where(OrgMembership.user_id == owner.id)).first()
    r = client.get(f"/api/v1/internal/orgs/{school}/question-library", headers=h).json()
    assert r["allowed"] is True                      # on its free trial
    assert client.get(f"/api/v1/internal/orgs/{body_id}/question-library", headers=h).json()["allowed"] is True
