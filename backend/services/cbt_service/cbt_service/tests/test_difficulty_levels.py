"""Organisation difficulty levels: built-ins, custom levels, and questions using them."""
from typing import Any

from .conftest import TEACHER_ID, assign_teacher, create_subject
from .test_bulk_import import find_item, native_file, upload
from .test_items import MCQ_ITEM

URL = "/api/v1/difficulty-levels"


def levels(client, headers) -> dict[str, dict[str, Any]]:
    r = client.get(URL, headers=headers)
    assert r.status_code == 200, r.json()
    return {l["key"]: l for l in r.json()}


def subject_for_teacher(client, admin_headers) -> str:
    subject = create_subject(client, admin_headers, "Physics")
    assign_teacher(client, admin_headers, subject["id"], str(TEACHER_ID))
    return subject["id"]


def new_item(client, headers, subject_id: str, **extra: Any):
    base: dict[str, Any] = {k: v for k, v in MCQ_ITEM.items() if k != "difficulty"}
    return client.post(f"/api/v1/subjects/{subject_id}/items", json={**base, **extra}, headers=headers)


def test_built_in_levels_by_default(client, teacher_headers) -> None:
    found = levels(client, teacher_headers)
    assert list(found) == ["easy", "medium", "hard", "test", "cat1", "cat2", "cat3", "exam"]
    assert found["medium"]["is_default"] and found["cat1"]["name"] == "CAT 1"
    assert all(l["is_built_in"] and l["status"] == "active" for l in found.values())


def test_custom_level_used_by_questions(client, admin_headers, teacher_headers) -> None:
    r = client.post(URL, json={"name": "Very hard", "colour": "orange"}, headers=admin_headers)
    assert r.status_code == 201, r.json()
    assert r.json()["key"] == "very-hard"

    subject_id = subject_for_teacher(client, admin_headers)
    # by key or by name
    assert new_item(client, teacher_headers, subject_id, difficulty="very-hard").json()["difficulty"] == "very-hard"
    assert new_item(client, teacher_headers, subject_id, question_text="Q2", difficulty="Very Hard").json()["difficulty"] == "very-hard"
    r = new_item(client, teacher_headers, subject_id, question_text="Q3", difficulty="impossible")
    assert r.status_code == 422 and "Very hard" in r.json()["detail"]

    r = client.get(f"/api/v1/subjects/{subject_id}/items", params={"difficulty": "very-hard"}, headers=teacher_headers)
    assert r.json()["total"] == 2
    assert levels(client, admin_headers)["very-hard"]["item_count"] == 2


def test_default_level_for_new_questions(client, admin_headers, teacher_headers) -> None:
    subject_id = subject_for_teacher(client, admin_headers)
    assert new_item(client, teacher_headers, subject_id).json()["difficulty"] == "medium"

    hard = levels(client, admin_headers)["hard"]
    r = client.patch(f"{URL}/{hard['id']}", json={"is_default": True}, headers=admin_headers)
    assert r.status_code == 200 and r.json()["is_default"]
    found = levels(client, admin_headers)
    assert not found["medium"]["is_default"]
    assert new_item(client, teacher_headers, subject_id, question_text="Q2").json()["difficulty"] == "hard"


def test_archived_levels_stay_on_questions_but_cannot_be_picked(client, admin_headers, teacher_headers) -> None:
    subject_id = subject_for_teacher(client, admin_headers)
    item = new_item(client, teacher_headers, subject_id, difficulty="cat2").json()
    cat2 = levels(client, admin_headers)["cat2"]
    r = client.patch(f"{URL}/{cat2['id']}", json={"status": "archived"}, headers=admin_headers)
    assert r.status_code == 200, r.json()

    assert new_item(client, teacher_headers, subject_id, question_text="Q2", difficulty="cat2").status_code == 422
    # Editing the question without changing difficulty still works.
    r = client.patch(f"/api/v1/subjects/{subject_id}/items/{item['id']}", json={"marks": 2, "difficulty": "cat2"}, headers=teacher_headers)
    assert r.status_code == 200, r.json()
    assert "cat2" not in {l["key"] for l in client.get(URL, params={"include_archived": False}, headers=admin_headers).json()}


def test_guard_rails(client, admin_headers, teacher_headers) -> None:
    found = levels(client, admin_headers)
    # teachers can read but not change
    assert client.post(URL, json={"name": "X"}, headers=teacher_headers).status_code == 403
    # the default can't be archived; built-ins can't be deleted; names are unique
    assert client.patch(f"{URL}/{found['medium']['id']}", json={"status": "archived"}, headers=admin_headers).status_code == 409
    assert client.delete(f"{URL}/{found['easy']['id']}", headers=admin_headers).status_code == 409
    assert client.post(URL, json={"name": "easy"}, headers=admin_headers).status_code == 409

    # a used custom level can't be deleted; an unused one can
    custom = client.post(URL, json={"name": "Olympiad"}, headers=admin_headers).json()
    subject_id = subject_for_teacher(client, admin_headers)
    new_item(client, teacher_headers, subject_id, difficulty="olympiad")
    assert client.delete(f"{URL}/{custom['id']}", headers=admin_headers).status_code == 409
    spare = client.post(URL, json={"name": "Spare"}, headers=admin_headers).json()
    assert client.delete(f"{URL}/{spare['id']}", headers=admin_headers).status_code == 204


def test_reorder(client, admin_headers) -> None:
    ids = [l["id"] for l in client.get(URL, headers=admin_headers).json()]
    r = client.put(f"{URL}/order", json={"ids": list(reversed(ids))}, headers=admin_headers)
    assert r.status_code == 200, r.json()
    assert [l["key"] for l in r.json()][0] == "exam"
    assert client.put(f"{URL}/order", json={"ids": ids[:2]}, headers=admin_headers).status_code == 422


def test_bulk_upload_uses_levels(client, admin_headers, teacher_headers) -> None:
    client.post(URL, json={"name": "Very hard"}, headers=admin_headers)
    subject_id = subject_for_teacher(client, admin_headers)
    row = lambda q, d: [q, "mcq_single", "1", "2", "3", "4", "A", "", 1, 0, "", d]   # noqa: E731
    r = upload(client, teacher_headers, subject_id, "q.xlsx", native_file([
        row("Uses name", "Very hard"), row("Uses key", "cat1"), row("Blank", ""), row("Bad", "legendary"),
    ]))
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["successful_rows"] == 3 and body["failed_rows"] == 1
    assert "legendary" in body["errors"][0]["error"]
    assert find_item(client, teacher_headers, subject_id, "Uses name")["difficulty"] == "very-hard"
    assert find_item(client, teacher_headers, subject_id, "Blank")["difficulty"] == "medium"
