"""Graduate, move or remove chosen students while the cohort carries on."""
import copy

from fastapi.testclient import TestClient

from tests.auth.test_cohorts import (
    COHORT_2_PAYLOAD, STUDENT_2_PAYLOAD, STUDENT_PAYLOAD,
    _add_students_to_cohort, _create_cohort, _create_student, _get_cohort_member_ids, get_super_admin_token,
)

H = dict[str, str]


def _auth(token: str) -> H:
    return {"Authorization": f"Bearer {token}"}


def _setup(client: TestClient) -> tuple[str, dict, list[str]]:
    token = get_super_admin_token(client)
    cohort = _create_cohort(client, token)
    third = copy.deepcopy(STUDENT_PAYLOAD) | {"firstname": "Ezra", "phone": "+1000000005", "institution_id": "STU/2024/003"}
    ids = [_create_student(client, token, p)["id"] for p in (STUDENT_PAYLOAD, STUDENT_2_PAYLOAD, third)]
    _add_students_to_cohort(client, token, cohort["id"], ids)
    return token, cohort, ids


def _act(client: TestClient, token: str, cohort_id: str, **body: object) -> dict:
    r = client.post(f"/api/v1/cohorts/{cohort_id}/members/actions", json=body, headers=_auth(token))
    assert r.status_code == 200, r.json()
    return r.json()


def test_graduate_selected_students_keeps_cohort_open(client: TestClient) -> None:
    token, cohort, (a, b, c) = _setup(client)
    res = _act(client, token, cohort["id"], action="graduate", student_ids=[a, b], note="2024/2025 session")
    assert (res["done"], res["skipped"]) == (2, [])

    # Only the one left is active; the cohort is still active and counts 1.
    assert _get_cohort_member_ids(client, token, cohort["id"]) == [c]
    got = client.get(f"/api/v1/cohorts/{cohort['id']}", headers=_auth(token)).json()
    assert (got["status"], got["member_count"]) == ("active", 1)
    grads = client.get(f"/api/v1/cohorts/{cohort['id']}/members?status=graduated", headers=_auth(token)).json()["items"]
    assert {m["student_id"] for m in grads} == {a, b}
    assert all(m["status"] == "graduated" and m["end_note"] == "2024/2025 session" and m["ended_at"] for m in grads)

    # New students can still join, and a graduate isn't counted as already in it.
    new = _create_student(client, token, copy.deepcopy(STUDENT_PAYLOAD) | {
        "firstname": "Fola", "phone": "+1000000006", "institution_id": "STU/2025/001"})["id"]
    added = _add_students_to_cohort(client, token, cohort["id"], [new, a])
    assert added["added"] == 2

    # Students list shows the graduation and filters on it.
    lst = client.get("/api/v1/users/students?graduated=true", headers=_auth(token)).json()["students"]
    assert {s["id"] for s in lst} == {a, b}
    assert lst[0]["graduations"][0]["cohort_name"] == cohort["name"]
    not_grad = client.get("/api/v1/users/students?graduated=false", headers=_auth(token)).json()["students"]
    assert {s["id"] for s in not_grad} == {c, new}


def test_graduating_again_or_outsiders_are_skipped(client: TestClient) -> None:
    token, cohort, (a, b, c) = _setup(client)
    _act(client, token, cohort["id"], action="graduate", student_ids=[a])
    res = _act(client, token, cohort["id"], action="graduate", student_ids=[a, b])
    assert res["done"] == 1
    assert res["skipped"] == [{"student_id": a, "reason": "not_an_active_member"}]


def test_move_students_to_another_cohort(client: TestClient) -> None:
    token, cohort, (a, b, c) = _setup(client)
    other = _create_cohort(client, token, COHORT_2_PAYLOAD)
    res = _act(client, token, cohort["id"], action="move", student_ids=[a, b], target_cohort_id=other["id"])
    assert (res["done"], res["added_to_target"]) == (2, 2)
    assert _get_cohort_member_ids(client, token, cohort["id"]) == [c]
    assert set(_get_cohort_member_ids(client, token, other["id"])) == {a, b}
    left = client.get(f"/api/v1/cohorts/{cohort['id']}/members?status=left", headers=_auth(token)).json()["items"]
    assert {m["end_note"] for m in left} == {f"Moved to {other['name']}"}

    # Moving into the same cohort, or with no target, is refused.
    r = client.post(f"/api/v1/cohorts/{cohort['id']}/members/actions",
                    json={"action": "move", "student_ids": [c], "target_cohort_id": cohort["id"]}, headers=_auth(token))
    assert r.status_code == 400
    r = client.post(f"/api/v1/cohorts/{cohort['id']}/members/actions",
                    json={"action": "move", "student_ids": [c]}, headers=_auth(token))
    assert r.status_code == 422


def test_remove_is_kept_as_history_and_student_can_rejoin(client: TestClient) -> None:
    token, cohort, (a, b, c) = _setup(client)
    r = client.delete(f"/api/v1/cohorts/{cohort['id']}/members/{a}", headers=_auth(token))
    assert r.status_code == 204
    assert a not in _get_cohort_member_ids(client, token, cohort["id"])
    left = client.get(f"/api/v1/cohorts/{cohort['id']}/members?status=left", headers=_auth(token)).json()["items"]
    assert [m["student_id"] for m in left] == [a]
    assert _add_students_to_cohort(client, token, cohort["id"], [a])["added"] == 1
    every = client.get(f"/api/v1/cohorts/{cohort['id']}/members?status=all", headers=_auth(token)).json()
    assert every["total"] == 4   # the old "left" row plus 3 active


def test_member_ids_for_select_all(client: TestClient) -> None:
    token, cohort, ids = _setup(client)
    r = client.get(f"/api/v1/cohorts/{cohort['id']}/members/ids", headers=_auth(token))
    assert set(r.json()) == set(ids)
    r = client.get(f"/api/v1/cohorts/{cohort['id']}/members/ids?search=Diana", headers=_auth(token))
    assert r.json() == [ids[1]]


def test_internal_ids_for_exam_assignment_exclude_graduates(client: TestClient, monkeypatch) -> None:
    # Use a known secret for this test, whatever INTERNAL_SECRET is set to locally.
    from auth.core.settings import settings
    monkeypatch.setattr(settings, "INTERNAL_SECRET", "test-internal-secret")
    token, cohort, (a, b, c) = _setup(client)
    _act(client, token, cohort["id"], action="graduate", student_ids=[a])
    org_id = client.get(f"/api/v1/cohorts/{cohort['id']}", headers=_auth(token)).json()["org_id"]
    r = client.get(
        f"/api/v1/internal/cohorts/{cohort['id']}/student_ids",
        headers={"x-org-id": org_id, "x-internal-secret": "test-internal-secret"},
    )
    assert r.status_code == 200, r.json()
    assert set(r.json()) == {b, c}


def test_graduate_cohort_graduates_everyone_and_keeps_it_open(client: TestClient) -> None:
    token, cohort, ids = _setup(client)
    r = client.post(f"/api/v1/cohorts/{cohort['id']}/graduate",
                    json={"note": "2024/2025 session"}, headers=_auth(token))
    assert r.status_code == 200, r.json()
    assert (r.json()["graduated"], r.json()["cohort"]["status"], r.json()["cohort"]["member_count"]) == (3, "active", 0)
    grads = client.get(f"/api/v1/cohorts/{cohort['id']}/members?status=graduated", headers=_auth(token)).json()["items"]
    assert {m["student_id"] for m in grads} == set(ids)
    assert {m["end_note"] for m in grads} == {"2024/2025 session"}
    # Ready for the next set.
    new = _create_student(client, token, copy.deepcopy(STUDENT_PAYLOAD) | {
        "firstname": "Gbenga", "phone": "+1000000007", "institution_id": "STU/2025/002"})["id"]
    assert _add_students_to_cohort(client, token, cohort["id"], [new])["added"] == 1


def test_graduate_cohort_then_archive_and_reopen(client: TestClient) -> None:
    token, cohort, ids = _setup(client)
    r = client.post(f"/api/v1/cohorts/{cohort['id']}/graduate", json={"then": "archive"}, headers=_auth(token))
    assert r.status_code == 200 and r.json()["cohort"]["status"] == "archived" and r.json()["graduated"] == 3
    # Archived: no more changes until reopened.
    assert client.post(f"/api/v1/cohorts/{cohort['id']}/members/actions",
                       json={"action": "graduate", "student_ids": ids}, headers=_auth(token)).status_code == 400
    r = client.post(f"/api/v1/cohorts/{cohort['id']}/reopen", headers=_auth(token))
    assert r.status_code == 200 and r.json()["status"] == "active"
    assert client.post(f"/api/v1/cohorts/{cohort['id']}/reopen", headers=_auth(token)).status_code == 400


# ---------------------------------------------------------------- deleting

def _exams(monkeypatch, count: int) -> None:
    from auth.clients.cbt_service_client import CBTServiceClient

    async def _count(self, cohort_id, org_id) -> int:
        return count
    monkeypatch.setattr(CBTServiceClient, "get_cohort_exam_assignment_count", _count)


def test_empty_cohort_can_be_deleted(client: TestClient, monkeypatch) -> None:
    _exams(monkeypatch, 0)
    token = get_super_admin_token(client)
    cohort = _create_cohort(client, token)
    assert client.get(f"/api/v1/cohorts/{cohort['id']}/deletable", headers=_auth(token)).json() == {
        "deletable": True, "reason": None}
    assert client.delete(f"/api/v1/cohorts/{cohort['id']}/permanently", headers=_auth(token)).status_code == 204
    assert client.get(f"/api/v1/cohorts/{cohort['id']}", headers=_auth(token)).status_code == 404


def test_cohort_with_any_student_history_cannot_be_deleted(client: TestClient, monkeypatch) -> None:
    _exams(monkeypatch, 0)
    token, cohort, ids = _setup(client)
    # Even after everyone has left, the history blocks deleting.
    _act(client, token, cohort["id"], action="remove", student_ids=ids)
    r = client.get(f"/api/v1/cohorts/{cohort['id']}/deletable", headers=_auth(token)).json()
    assert r["deletable"] is False and "had students" in r["reason"]
    assert client.delete(f"/api/v1/cohorts/{cohort['id']}/permanently", headers=_auth(token)).status_code == 409


def test_cohort_given_exams_cannot_be_deleted(client: TestClient, monkeypatch) -> None:
    _exams(monkeypatch, 2)
    token = get_super_admin_token(client)
    cohort = _create_cohort(client, token)
    r = client.get(f"/api/v1/cohorts/{cohort['id']}/deletable", headers=_auth(token)).json()
    assert r["deletable"] is False and "exams" in r["reason"]
    assert client.delete(f"/api/v1/cohorts/{cohort['id']}/permanently", headers=_auth(token)).status_code == 409
