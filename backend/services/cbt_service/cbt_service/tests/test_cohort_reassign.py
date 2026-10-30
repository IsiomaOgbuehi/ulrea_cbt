# cbt_service/tests/test_cohort_reassign.py
"""Assigning a cohort again (e.g. while the exam is live) only adds new members."""
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from cbt_service.clients.auth_client import AuthClient

from .test_exams import create_approved_exam

H = dict[str, str]


def set_members(monkeypatch: pytest.MonkeyPatch, members: list[UUID]) -> None:
    async def _members(self: AuthClient, cohort_id: UUID, org_id: UUID) -> list[UUID]:
        return list(members)
    monkeypatch.setattr(AuthClient, "get_cohort_student_ids", _members)


def test_reassigning_cohort_adds_only_new_members(
    client: TestClient, teacher_headers: H, admin_headers: H, monkeypatch: pytest.MonkeyPatch,
) -> None:
    exam = create_approved_exam(client, teacher_headers, admin_headers)
    cohort = str(uuid4())
    url = f"/api/v1/exams/{exam['id']}/assign_cohort"
    first, second, newcomer = uuid4(), uuid4(), uuid4()

    assert client.get(f"/api/v1/exams/{exam['id']}/assigned-cohorts", headers=admin_headers).json() == []

    set_members(monkeypatch, [first, second])
    r = client.post(f"{url}?summary=true", json={"cohort_id": cohort}, headers=admin_headers)
    assert r.status_code == 200, r.json()
    assert r.json() == {"cohort_id": cohort, "members": 2, "added": 2, "already_assigned": 0}

    # A student joins the cohort later; assigning again adds just them.
    set_members(monkeypatch, [first, second, newcomer])
    r = client.post(f"{url}?summary=true", json={"cohort_id": cohort}, headers=admin_headers)
    assert r.json() == {"cohort_id": cohort, "members": 3, "added": 1, "already_assigned": 2}

    assert client.get(f"/api/v1/exams/{exam['id']}/assigned-cohorts", headers=admin_headers).json() == [
        {"cohort_id": cohort, "students": 3}
    ]
    # Without summary the old response shape is unchanged.
    r = client.post(url, json={"cohort_id": cohort}, headers=admin_headers)
    assert isinstance(r.json(), list) and len(r.json()) == 3


def test_preview_names_only_members_without_the_exam(
    client: TestClient, teacher_headers: H, admin_headers: H, monkeypatch: pytest.MonkeyPatch,
) -> None:
    exam = create_approved_exam(client, teacher_headers, admin_headers)
    cohort = str(uuid4())
    first, second, newcomer = uuid4(), uuid4(), uuid4()
    preview = f"/api/v1/exams/{exam['id']}/assign_cohort/preview?cohort_id={cohort}"

    set_members(monkeypatch, [first, second])
    r = client.get(preview, headers=admin_headers).json()
    assert (r["members"], r["already_assigned"], r["new_count"]) == (2, 0, 2)
    client.post(f"/api/v1/exams/{exam['id']}/assign_cohort", json={"cohort_id": cohort}, headers=admin_headers)

    set_members(monkeypatch, [first, second, newcomer])
    r = client.get(preview, headers=admin_headers).json()
    assert (r["members"], r["already_assigned"], r["new_count"]) == (3, 2, 1)
    assert [m["id"] for m in r["new_members"]] == [str(newcomer)]
    assert r["new_members"][0]["name"] == "Test User"
    # Only names up to the limit, but the count is still exact.
    r = client.get(f"{preview}&limit=0", headers=admin_headers).json()
    assert r["new_count"] == 1 and r["new_members"] == []
    # Previewing changes nothing.
    assert client.get(f"/api/v1/exams/{exam['id']}/assigned-cohorts", headers=admin_headers).json()[0]["students"] == 2


def test_unfinished_exams_for_students(
    client: TestClient, teacher_headers: H, admin_headers: H, monkeypatch: pytest.MonkeyPatch,
) -> None:
    exam = create_approved_exam(client, teacher_headers, admin_headers)
    busy, free = uuid4(), uuid4()
    set_members(monkeypatch, [busy])
    client.post(f"/api/v1/exams/{exam['id']}/assign_cohort", json={"cohort_id": str(uuid4())}, headers=admin_headers)
    r = client.post("/api/v1/exams/unfinished-for-students",
                    json={"student_ids": [str(busy), str(free)]}, headers=admin_headers)
    assert r.status_code == 200, r.json()
    assert r.json() == [{"student_id": str(busy), "exams": [
        {"exam_id": exam["id"], "title": exam["title"], "status": "assigned"}]}]
    # Teachers don't manage cohorts, so they can't ask.
    assert client.post("/api/v1/exams/unfinished-for-students",
                       json={"student_ids": [str(busy)]}, headers=teacher_headers).status_code == 403


def test_internal_cohort_exam_assignment_count(
    client: TestClient, teacher_headers: H, admin_headers: H, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cbt_service.core.settings import settings
    from .conftest import ORG_ID
    exam = create_approved_exam(client, teacher_headers, admin_headers)
    cohort, unused = str(uuid4()), str(uuid4())
    set_members(monkeypatch, [uuid4(), uuid4()])
    client.post(f"/api/v1/exams/{exam['id']}/assign_cohort", json={"cohort_id": cohort}, headers=admin_headers)
    monkeypatch.setattr(settings, "INTERNAL_SECRET", "test-internal")
    h = {"X-Internal-Secret": "test-internal"}
    assert client.get(f"/internal/cohorts/{cohort}/exam-assignment-count?org_id={ORG_ID}", headers=h).json() == 2
    assert client.get(f"/internal/cohorts/{unused}/exam-assignment-count?org_id={ORG_ID}", headers=h).json() == 0
    assert client.get(f"/internal/cohorts/{cohort}/exam-assignment-count?org_id={ORG_ID}",
                      headers={"X-Internal-Secret": "wrong"}).status_code == 403
