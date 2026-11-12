"""Redis being slow or down must not take sign-in down with it."""
import asyncio
from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from redis.exceptions import TimeoutError as RedisTimeoutError

from auth.utility.redis import token_blacklist
from auth.utility.redis.token_blacklist import is_revoked, revoke
from tests.conftest import get_super_admin_token


class _BrokenRedis:
    async def get(self, *args: Any, **kwargs: Any) -> Any:
        raise RedisTimeoutError("Timeout reading from redis")

    async def set(self, *args: Any, **kwargs: Any) -> Any:
        raise RedisTimeoutError("Timeout reading from redis")


@pytest.fixture
def broken_redis() -> Iterator[None]:
    token_blacklist._down_until = 0.0
    with patch("auth.utility.redis.redis_client.redis_client", _BrokenRedis()):
        yield
    token_blacklist._down_until = 0.0


def test_access_check_fails_open(broken_redis: None) -> None:
    assert asyncio.run(is_revoked("abc", fail_open=True)) is False


def test_refresh_check_fails_closed_with_503(broken_redis: None) -> None:
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        asyncio.run(is_revoked("abc", fail_open=False))
    assert e.value.status_code == 503


def test_revoke_is_best_effort(broken_redis: None) -> None:
    asyncio.run(revoke("abc", 60))   # no exception


def test_skips_redis_during_cooldown(broken_redis: None) -> None:
    calls: list[str] = []

    class _Counting(_BrokenRedis):
        async def get(self, *args: Any, **kwargs: Any) -> Any:
            calls.append("get")
            raise RedisTimeoutError("down")

    with patch("auth.utility.redis.redis_client.redis_client", _Counting()):
        asyncio.run(is_revoked("a", fail_open=True))
        asyncio.run(is_revoked("b", fail_open=True))
    assert calls == ["get"]


def test_signed_in_requests_work_while_redis_is_down(client: TestClient) -> None:
    token: str = get_super_admin_token(client)
    token_blacklist._down_until = 0.0
    with patch("auth.utility.redis.redis_client.redis_client", _BrokenRedis()):
        r = client.get("/api/v1/roles/me", headers={"Authorization": f"Bearer {token}"})
    token_blacklist._down_until = 0.0
    assert r.status_code == 200, r.json()


def test_refresh_says_busy_instead_of_failing_while_redis_is_down(client: TestClient) -> None:
    from tests.conftest import USER_EMAIL, do_full_signup
    do_full_signup(client)
    refresh: str = client.post(
        "/api/v1/auth/login", data={"username": USER_EMAIL, "password": "chekicicici"},
    ).json()["refresh_token"]
    token_blacklist._down_until = 0.0
    with patch("auth.utility.redis.redis_client.redis_client", _BrokenRedis()):
        r = client.post("/api/v1/auth/token/refresh", json={"refresh_token": refresh})
    token_blacklist._down_until = 0.0
    assert r.status_code == 503
    # Redis back: the same token still works (it was never used).
    assert client.post("/api/v1/auth/token/refresh", json={"refresh_token": refresh}).status_code == 200
