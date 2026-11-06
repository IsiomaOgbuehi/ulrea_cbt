"""
Sign-in sessions: refresh tokens work once and are replaced on each use; a
reused one ends the session; sessions slide up to a hard limit, shorter for
students; logging out ends the session.
"""
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi.testclient import TestClient

from auth.core.settings import settings
from auth.utility.jwt.jwt import create_refresh_token
from tests.auth.test_user_management import (
    STUDENT_ANSWER, _create_active_student, _student_login,
)
from tests.conftest import USER_EMAIL, do_full_signup, get_super_admin_token

REFRESH = "/api/v1/auth/token/refresh"


def _claims(token: str) -> dict[str, Any]:
    return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])


def _login(client: TestClient) -> dict[str, Any]:
    do_full_signup(client)
    r = client.post("/api/v1/auth/login", data={"username": USER_EMAIL, "password": "chekicicici"})
    assert r.status_code == 200, r.json()
    return r.json()


def _refresh(client: TestClient, token: str) -> Any:
    return client.post(REFRESH, json={"refresh_token": token})


def test_refresh_replaces_the_refresh_token_in_the_same_session(client: TestClient) -> None:
    first = _login(client)["refresh_token"]
    r = _refresh(client, first)
    assert r.status_code == 200, r.json()
    second = r.json()["refresh_token"]
    assert second and second != first
    a, b = _claims(first), _claims(second)
    assert a["sid"] == b["sid"] and a["auth_time"] == b["auth_time"] and b["exp"] >= a["exp"]
    assert _refresh(client, second).status_code == 200


def test_two_tabs_refreshing_together_share_the_new_pair(client: TestClient) -> None:
    first = _login(client)["refresh_token"]
    one = _refresh(client, first).json()
    two = _refresh(client, first)
    assert two.status_code == 200 and two.json()["refresh_token"] == one["refresh_token"]


def test_reusing_an_old_refresh_token_ends_the_session(client: TestClient, mock_redis: dict[str, Any]) -> None:
    first = _login(client)["refresh_token"]
    second = _refresh(client, first).json()["refresh_token"]
    # After the grace period, the old token turns up again (a copy someone kept).
    for key in [k for k in mock_redis if k.startswith("refresh:rotated:")]:
        del mock_redis[key]
    assert _refresh(client, first).status_code == 401
    # ...so the real user's newer token stops working too.
    assert _refresh(client, second).status_code == 401


def test_logout_ends_the_session(client: TestClient) -> None:
    login = _login(client)
    rotated = _refresh(client, login["refresh_token"]).json()
    r = client.post(
        "/api/v1/auth/logout", json={"refresh_token": rotated["refresh_token"]},
        headers={"Authorization": f"Bearer {rotated['access_token']}"},
    )
    assert r.status_code == 200
    assert _refresh(client, rotated["refresh_token"]).status_code == 401


def test_staff_sessions_slide_up_to_the_hard_limit() -> None:
    from uuid import uuid4
    now = datetime.now(timezone.utc)
    fresh = _claims(create_refresh_token(uuid4(), role="admin"))
    assert abs(fresh["exp"] - (now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)).timestamp()) < 5
    # A session that began long ago can't outlive its hard limit.
    old_start = now - timedelta(days=settings.SESSION_MAX_DAYS - 1)
    late = _claims(create_refresh_token(uuid4(), role="admin", sid="s", auth_time=old_start))
    assert abs(late["exp"] - (old_start + timedelta(days=settings.SESSION_MAX_DAYS)).timestamp()) < 5


def test_students_get_a_short_idle_limit(client: TestClient) -> None:
    token = get_super_admin_token(client)
    student = _create_active_student(client, token)
    r = _student_login(client, student["access_code"], STUDENT_ANSWER)
    assert r.status_code == 200, r.json()
    claims = _claims(r.json()["refresh_token"])
    expected = datetime.now(timezone.utc) + timedelta(hours=settings.STUDENT_SESSION_IDLE_HOURS)
    assert claims["role"] == "student" and abs(claims["exp"] - expected.timestamp()) < 5
    rotated = _refresh(client, r.json()["refresh_token"]).json()["refresh_token"]
    assert _claims(rotated)["role"] == "student"


def test_tokens_from_before_sessions_still_refresh(client: TestClient) -> None:
    login = _login(client)
    old = _claims(login["refresh_token"])
    legacy = jwt.encode(
        {"sub": old["sub"], "jti": "legacy-jti", "exp": old["exp"], "type": "refresh", "org_id": old["org_id"]},
        settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM,
    )
    r = _refresh(client, legacy)
    assert r.status_code == 200 and _claims(r.json()["refresh_token"])["sid"] == "legacy-jti"
