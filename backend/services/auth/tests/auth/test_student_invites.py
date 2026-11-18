"""Schools inviting students by email; students join with email and password."""
from typing import Any
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from auth.database.schema.membership.membership_db import OrgMembership
from auth.database.schema.user.enums import MembershipStatus, UserRole
from auth.database.schema.user.user_db import UserModel
from .test_billing import _auth
from ..conftest import engine, get_super_admin_token

INVITE = "/api/v1/users/students/invites"


def _invite(client: TestClient, token: str, email: str) -> tuple[Any, AsyncMock]:
    sent = AsyncMock()
    with patch("auth.api.v1.routes.users.EmailService.send_student_invite_email", sent), \
         patch("auth.api.v1.routes.users.IS_DEV", True):
        r = client.post(INVITE, json={"email": email}, headers=_auth(token))
    return r, sent


def _token_from(sent: AsyncMock) -> str:
    link: str = sent.await_args.kwargs["invite_link"]
    assert link.split("?")[0].endswith("/accept-invite")
    return parse_qs(urlparse(link).query)["token"][0]


def test_invite_a_new_student_who_sets_a_password(client: TestClient) -> None:
    staff = get_super_admin_token(client)
    r, sent = _invite(client, staff, "  New.Student@Example.com ")
    assert r.status_code == 201, r.json()
    body = r.json()
    assert body["email"] == "new.student@example.com" and body["already_has_account"] is False
    assert body["institution_id"].startswith("JW-")
    sent.assert_awaited_once()
    assert sent.await_args.kwargs["email"] == "new.student@example.com"
    token = _token_from(sent)

    # Shown as a pending student in the school.
    listed = client.get("/api/v1/users/students", headers=_auth(staff)).json()["students"]
    assert any(s["id"] == body["student_id"] and s["status"] == "pending" for s in listed)

    p = client.post(f"{INVITE}/preview", json={"token": token}).json()
    assert p["email"] == "new.student@example.com" and p["has_account"] is False and p["needs_name"] is True

    bad = client.post(f"{INVITE}/accept", json={"token": token, "password": "longenough1", "confirm_password": "longenough1"})
    assert bad.status_code == 422 and "name" in bad.json()["detail"]
    ok = client.post(f"{INVITE}/accept", json={
        "token": token, "firstname": "Ngozi", "lastname": "Eze", "password": "longenough1", "confirm_password": "longenough1",
    })
    assert ok.status_code == 200, ok.json()
    assert ok.json()["access_token"]

    with Session(engine) as s:
        user = s.exec(select(UserModel).where(UserModel.email == "new.student@example.com")).one()
        m = s.exec(select(OrgMembership).where(OrgMembership.user_id == user.id)).one()
        assert user.verified and user.firstname == "Ngozi" and m.status == MembershipStatus.ACTIVE and m.role == UserRole.STUDENT

    # Signs in with email and password from now on; the link is used up.
    login = client.post("/api/v1/auth/login", data={"username": "new.student@example.com", "password": "longenough1"})
    assert login.status_code == 200, login.json()
    again = client.post(f"{INVITE}/accept", json={"token": token, "password": "longenough1"})
    assert again.status_code == 409


def test_someone_with_an_account_confirms_with_their_password(client: TestClient) -> None:
    staff = get_super_admin_token(client)
    with Session(engine) as s:
        from auth.utility.password.password_harsher import PasswordHasher
        s.add(UserModel(firstname="Tobi", lastname="Ade", email="tobi@example.com", verified=True,
                        is_first_login=False, password=PasswordHasher.create("mypassword1")))
        s.commit()
    r, sent = _invite(client, staff, "tobi@example.com")
    assert r.status_code == 201 and r.json()["already_has_account"] is True
    token = _token_from(sent)
    p = client.post(f"{INVITE}/preview", json={"token": token}).json()
    assert p["has_account"] is True and p["needs_name"] is False
    wrong = client.post(f"{INVITE}/accept", json={"token": token, "password": "nope"})
    assert wrong.status_code == 400
    ok = client.post(f"{INVITE}/accept", json={"token": token, "password": "mypassword1"})
    assert ok.status_code == 200
    with Session(engine) as s:
        user = s.exec(select(UserModel).where(UserModel.email == "tobi@example.com")).one()
        assert user.firstname == "Tobi"          # name kept


def test_duplicates_resend_and_bad_links(client: TestClient) -> None:
    staff = get_super_admin_token(client)
    r, sent = _invite(client, staff, "kemi@example.com")
    first = _token_from(sent)
    dup, _ = _invite(client, staff, "KEMI@example.com")
    assert dup.status_code == 409 and "already been invited" in dup.json()["detail"]
    resend = AsyncMock()
    with patch("auth.api.v1.routes.users.EmailService.send_student_invite_email", resend):
        rr = client.post(f"/api/v1/users/students/{r.json()['student_id']}/invite/resend", headers=_auth(staff))
    assert rr.status_code == 200 and resend.await_count == 1
    assert client.post(f"{INVITE}/preview", json={"token": first + "x"}).status_code == 400
    assert client.post(f"{INVITE}/preview", json={"token": "not-a-real-token"}).status_code == 400
    # A student who's in already can't be invited again.
    client.post(f"{INVITE}/accept", json={"token": first, "firstname": "K", "lastname": "O", "password": "longenough1", "confirm_password": "longenough1"})
    active, _ = _invite(client, staff, "kemi@example.com")
    assert active.status_code == 409 and "already in your organisation" in active.json()["detail"]
    # Students can't invite.
    assert client.post(INVITE, json={"email": "x@example.com"}).status_code == 401
