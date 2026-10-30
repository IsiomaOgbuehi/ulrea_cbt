# cbt_service/tests/test_bank_assignments.py
"""Teachers see unbanked questions plus the banks they're given; admins see all."""
from uuid import uuid4

from fastapi.testclient import TestClient

from .conftest import TEACHER_ID, assign_teacher, create_exam, create_subject
from .test_banks_topics import list_items, make_bank, make_item

H = dict[str, str]
V1 = "/api/v1"


def give_bank(client: TestClient, headers: H, subject: str, bank: str, user_ids: list[str]) -> dict:
    r = client.put(f"{V1}/subjects/{subject}/banks/{bank}/staff", json={"user_ids": user_ids}, headers=headers)
    assert r.status_code == 200, r.json()
    return r.json()


def setup(client: TestClient, admin: H) -> tuple[str, dict, dict, dict, dict, dict]:
    """Subject (teacher assigned) with banks SS1, SS2 and a question in each, plus one unbanked."""
    subject = create_subject(client, admin)["id"]
    assign_teacher(client, admin, subject, TEACHER_ID)
    ss1 = make_bank(client, admin, subject, "SS1")
    ss2 = make_bank(client, admin, subject, "SS2")
    q1 = make_item(client, admin, subject, "In SS1", bank_id=ss1["id"])
    q2 = make_item(client, admin, subject, "In SS2", bank_id=ss2["id"])
    loose = make_item(client, admin, subject, "No bank")
    return subject, ss1, ss2, q1, q2, loose


def test_teacher_sees_only_unbanked_until_given_a_bank(client: TestClient, admin_headers: H, teacher_headers: H) -> None:
    subject, ss1, ss2, q1, q2, loose = setup(client, admin_headers)

    assert client.get(f"{V1}/subjects/{subject}/banks", headers=teacher_headers).json() == []
    assert {i["id"] for i in list_items(client, teacher_headers, subject)} == {loose["id"]}
    assert client.get(f"{V1}/subjects/{subject}/items/{q1['id']}", headers=teacher_headers).status_code == 403

    bank = give_bank(client, admin_headers, subject, ss1["id"], [str(TEACHER_ID)])
    assert bank["staff_ids"] == [str(TEACHER_ID)]

    banks = client.get(f"{V1}/subjects/{subject}/banks", headers=teacher_headers).json()
    assert [b["id"] for b in banks] == [ss1["id"]]
    assert {i["id"] for i in list_items(client, teacher_headers, subject)} == {loose["id"], q1["id"]}
    assert client.get(f"{V1}/subjects/{subject}/items/{q1['id']}", headers=teacher_headers).status_code == 200
    assert client.get(f"{V1}/subjects/{subject}/items/{q2['id']}", headers=teacher_headers).status_code == 403
    # Filtering by a bank they don't have finds nothing.
    assert list_items(client, teacher_headers, subject, bank_id=ss2["id"]) == []


def test_admin_sees_every_bank(client: TestClient, admin_headers: H) -> None:
    subject, ss1, ss2, q1, q2, loose = setup(client, admin_headers)
    banks = client.get(f"{V1}/subjects/{subject}/banks", headers=admin_headers).json()
    assert {b["id"] for b in banks} == {ss1["id"], ss2["id"]}
    assert {i["id"] for i in list_items(client, admin_headers, subject)} == {q1["id"], q2["id"], loose["id"]}


def test_teacher_cannot_write_into_or_move_to_an_unassigned_bank(
    client: TestClient, admin_headers: H, teacher_headers: H,
) -> None:
    subject, ss1, ss2, q1, q2, loose = setup(client, admin_headers)
    give_bank(client, admin_headers, subject, ss1["id"], [str(TEACHER_ID)])
    r = client.post(
        f"{V1}/subjects/{subject}/items",
        json={"question_text": "x", "item_type": "true_false", "options": [{"key": "A", "text": "True"}, {"key": "B", "text": "False"}],
              "correct_answers": ["A"], "bank_id": ss2["id"]},
        headers=teacher_headers,
    )
    assert r.status_code == 403
    r = client.patch(f"{V1}/subjects/{subject}/items/{q1['id']}", json={"bank_id": ss2["id"]}, headers=teacher_headers)
    assert r.status_code == 403
    r = client.patch(f"{V1}/subjects/{subject}/items/{q2['id']}", json={"question_text": "edited"}, headers=teacher_headers)
    assert r.status_code == 403


def test_teacher_bank_they_create_stays_theirs(client: TestClient, admin_headers: H, teacher_headers: H) -> None:
    subject = create_subject(client, admin_headers)["id"]
    assign_teacher(client, admin_headers, subject, TEACHER_ID)
    bank = make_bank(client, teacher_headers, subject, "My set")
    assert bank["staff_ids"] == [str(TEACHER_ID)]
    banks = client.get(f"{V1}/subjects/{subject}/banks", headers=teacher_headers).json()
    assert [b["id"] for b in banks] == [bank["id"]]


def test_only_subject_staff_can_be_given_a_bank(client: TestClient, admin_headers: H, teacher_headers: H) -> None:
    subject, ss1, *_ = setup(client, admin_headers)
    r = client.put(
        f"{V1}/subjects/{subject}/banks/{ss1['id']}/staff", json={"user_ids": [str(uuid4())]}, headers=admin_headers
    )
    assert r.status_code == 400
    # Teachers can't hand out banks.
    r = client.put(
        f"{V1}/subjects/{subject}/banks/{ss1['id']}/staff", json={"user_ids": [str(TEACHER_ID)]}, headers=teacher_headers
    )
    assert r.status_code == 403
    # Replacing with an empty list takes it away again.
    give_bank(client, admin_headers, subject, ss1["id"], [str(TEACHER_ID)])
    assert give_bank(client, admin_headers, subject, ss1["id"], [])["staff_ids"] == []


def test_unassigning_from_subject_removes_its_banks(client: TestClient, admin_headers: H, teacher_headers: H) -> None:
    subject, ss1, *_ = setup(client, admin_headers)
    give_bank(client, admin_headers, subject, ss1["id"], [str(TEACHER_ID)])
    assert client.delete(f"{V1}/subjects/{subject}/assign/{TEACHER_ID}", headers=admin_headers).status_code == 204
    banks = client.get(f"{V1}/subjects/{subject}/banks", headers=admin_headers).json()
    assert next(b for b in banks if b["id"] == ss1["id"])["staff_ids"] == []


def test_exam_questions_only_from_teachers_banks(client: TestClient, admin_headers: H, teacher_headers: H) -> None:
    subject, ss1, ss2, q1, q2, loose = setup(client, admin_headers)
    give_bank(client, admin_headers, subject, ss1["id"], [str(TEACHER_ID)])
    exam = create_exam(client, teacher_headers, subject_id=subject)
    r = client.post(f"{V1}/exams/{exam['id']}/items", json={"item_ids": [q2["id"]]}, headers=teacher_headers)
    assert r.status_code == 400
    r = client.post(f"{V1}/exams/{exam['id']}/items", json={"item_ids": [q1["id"], loose["id"]]}, headers=teacher_headers)
    assert r.status_code in (200, 201), r.json()
    # Drawing from a bank they don't have is refused; drawing from "any bank" skips it.
    r = client.post(f"{V1}/exams/{exam['id']}/items/from-bank", json={"bank_id": ss2["id"]}, headers=teacher_headers)
    assert r.status_code == 403
    # Exam scoped to a bank they don't have.
    r = client.post(
        f"{V1}/exams", json={"title": "x", "subject_id": subject, "duration_minutes": 10, "bank_id": ss2["id"]},
        headers=teacher_headers,
    )
    assert r.status_code == 403


def test_give_one_teacher_banks_from_the_staff_list(client: TestClient, admin_headers: H, teacher_headers: H) -> None:
    subject, ss1, ss2, q1, q2, loose = setup(client, admin_headers)
    url = f"{V1}/subjects/{subject}/staff/{TEACHER_ID}/banks"
    r = client.put(url, json={"bank_ids": [ss1["id"], ss2["id"]]}, headers=admin_headers)
    assert r.status_code == 200, r.json()
    assert {i["id"] for i in list_items(client, teacher_headers, subject)} == {q1["id"], q2["id"], loose["id"]}
    r = client.put(url, json={"bank_ids": [ss2["id"]]}, headers=admin_headers)
    assert r.json()["bank_ids"] == [ss2["id"]]
    banks = {b["id"]: b for b in client.get(f"{V1}/subjects/{subject}/banks", headers=admin_headers).json()}
    assert banks[ss1["id"]]["staff_ids"] == [] and banks[ss2["id"]]["staff_ids"] == [str(TEACHER_ID)]
    # Teachers can't give banks; banks must be in the subject; person must be on the subject.
    assert client.put(url, json={"bank_ids": []}, headers=teacher_headers).status_code == 403
    other = create_subject(client, admin_headers, name="Physics")["id"]
    other_bank = make_bank(client, admin_headers, other, "P1")
    assert client.put(url, json={"bank_ids": [other_bank["id"]]}, headers=admin_headers).status_code == 400
    r = client.put(f"{V1}/subjects/{other}/staff/{TEACHER_ID}/banks", json={"bank_ids": []}, headers=admin_headers)
    assert r.status_code == 400
