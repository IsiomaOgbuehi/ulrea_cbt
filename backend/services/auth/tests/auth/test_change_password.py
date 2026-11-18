"""Signed-in people changing their own password."""
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from auth.database.schema.membership.membership_db import OrgMembership
from auth.database.schema.user.user_db import UserModel
from auth.utility.jwt.jwt import create_refresh_token
from .test_billing import _auth
from ..conftest import USER_EMAIL, engine, get_super_admin_token

CHANGE = "/api/v1/users/me/password"
LOGIN = "/api/v1/auth/login"
REFRESH = "/api/v1/auth/token/refresh"
OLD = "chekicicici"
NEW = "a-new-password-9"


def _change(client: TestClient, token: str, current: str, new: str = NEW, confirm: str | None = None) -> Any:
    return client.post(
        CHANGE,
        json={"current_password": current, "new_password": new, "confirm_password": confirm or new},
        headers=_auth(token),
    )


def _login(client: TestClient, password: str) -> int:
    return client.post(LOGIN, data={"username": USER_EMAIL, "password": password}).status_code


def _earlier_refresh_token() -> str:
    """A refresh token from a sign-in that began a minute ago (another device)."""
    with Session(engine) as session:
        user = session.exec(select(UserModel).where(UserModel.email == USER_EMAIL)).one()
        m = session.exec(select(OrgMembership).where(OrgMembership.user_id == user.id)).first()
        assert m is not None
        return create_refresh_token(
            user.id, m.org_id, m.role, auth_time=datetime.now(UTC) - timedelta(minutes=1),
        )


def test_change_password_signs_out_other_devices(client: TestClient) -> None:
    token = get_super_admin_token(client)
    other_device = _earlier_refresh_token()

    r = _change(client, token, OLD)
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["access_token"] and body["refresh_token"]

    assert _login(client, OLD) == 401
    assert _login(client, NEW) == 200

    stale = client.post(REFRESH, json={"refresh_token": other_device})
    assert stale.status_code == 401
    assert "password was changed" in stale.json()["detail"]
    fresh = client.post(REFRESH, json={"refresh_token": body["refresh_token"]})
    assert fresh.status_code == 200, fresh.json()


def test_change_password_checks(client: TestClient) -> None:
    token = get_super_admin_token(client)
    assert _change(client, token, "wrong-one").status_code == 400
    assert _change(client, token, OLD, new="short").status_code == 422
    assert _change(client, token, OLD, confirm="something-else").status_code == 400
    assert _change(client, token, OLD, new=OLD).status_code == 400
    assert _login(client, OLD) == 200


def test_too_many_wrong_passwords_locks_for_a_while(client: TestClient) -> None:
    token = get_super_admin_token(client)
    for _ in range(5):
        assert _change(client, token, "wrong-one").status_code == 400
    r = _change(client, token, OLD)
    assert r.status_code == 429
    with Session(engine) as session:
        user = session.exec(select(UserModel).where(UserModel.email == USER_EMAIL)).one()
        assert user.password_changed_at is None
