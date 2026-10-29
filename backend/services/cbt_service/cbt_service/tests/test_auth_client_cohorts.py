"""AuthClient.get_cohort_student_ids against a stubbed auth service."""
import asyncio
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import HTTPException

from cbt_service.clients import auth_client as module
from cbt_service.clients.auth_client import AuthClient


class _FakeHttp:
    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        self.calls: list[str] = []

    async def get(self, url: str, **_: Any) -> httpx.Response:
        self.calls.append(url)
        return self.response


def _client(monkeypatch: pytest.MonkeyPatch, response: httpx.Response) -> tuple[AuthClient, _FakeHttp]:
    fake = _FakeHttp(response)
    monkeypatch.setattr(module, "get_http_client", lambda: fake)
    return AuthClient(redis_client=None, base_url="http://auth"), fake


def test_cohort_members_are_fetched_fresh_each_time(monkeypatch: pytest.MonkeyPatch) -> None:
    ids: list[UUID] = [uuid4(), uuid4()]
    client, fake = _client(monkeypatch, httpx.Response(200, json=[str(i) for i in ids]))
    cohort, org = uuid4(), uuid4()
    assert asyncio.run(client.get_cohort_student_ids(cohort, org)) == ids
    assert asyncio.run(client.get_cohort_student_ids(cohort, org)) == ids
    assert len(fake.calls) == 2 and fake.calls[0].endswith(f"/api/v1/internal/cohorts/{cohort}/student_ids")


@pytest.mark.parametrize("status,expected", [(400, 400), (404, 404), (500, 502)])
def test_cohort_member_errors_are_passed_on(monkeypatch: pytest.MonkeyPatch, status: int, expected: int) -> None:
    client, _ = _client(monkeypatch, httpx.Response(status, json={"detail": "Cohort has graduated."}))
    with pytest.raises(HTTPException) as err:
        asyncio.run(client.get_cohort_student_ids(uuid4(), uuid4()))
    assert err.value.status_code == expected
