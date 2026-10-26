# cbt_service/tests/test_banks_topics.py
"""Banks (class/level...) and topics inside a subject, on items and exams."""
import io

import openpyxl
from fastapi.testclient import TestClient

from .conftest import create_exam, create_subject

H = dict[str, str]
V1 = "/api/v1"


# ------------------------------------------------------------------ helpers

def make_bank(client: TestClient, headers: H, subject_id: str, name: str = "SS2", **extra: object) -> dict:
    r = client.post(f"{V1}/subjects/{subject_id}/banks", json={"name": name, **extra}, headers=headers)
    assert r.status_code == 201, r.json()
    return r.json()


def make_topic(client: TestClient, headers: H, subject_id: str, name: str, parent_id: str | None = None) -> dict:
    r = client.post(
        f"{V1}/subjects/{subject_id}/topics", json={"name": name, "parent_id": parent_id}, headers=headers
    )
    assert r.status_code == 201, r.json()
    return r.json()


def make_item(
    client: TestClient, headers: H, subject_id: str, text: str = "2 + 2?",
    bank_id: str | None = None, topic_ids: list[str] | None = None, difficulty: str = "easy",
) -> dict:
    payload = {
        "question_text": text,
        "item_type": "mcq_single",
        "options": [{"key": "A", "text": "3"}, {"key": "B", "text": "4"}],
        "correct_answers": ["B"],
        "difficulty": difficulty,
        "bank_id": bank_id,
        "topic_ids": topic_ids or [],
    }
    r = client.post(f"{V1}/subjects/{subject_id}/items", json=payload, headers=headers)
    assert r.status_code == 200, r.json()
    return r.json()


def list_items(client: TestClient, headers: H, subject_id: str, **params: object) -> list[dict]:
    r = client.get(f"{V1}/subjects/{subject_id}/items", params={"page_size": 100, **params}, headers=headers)
    assert r.status_code == 200, r.json()
    return r.json()["items"]


def setup_subject(client: TestClient, headers: H) -> tuple[str, dict, dict, dict, dict]:
    """Subject with banks SS1, SS2 and topics Algebra > Quadratics, Geometry."""
    subject = create_subject(client, headers)["id"]
    ss1 = make_bank(client, headers, subject, "SS1")
    ss2 = make_bank(client, headers, subject, "SS2")
    algebra = make_topic(client, headers, subject, "Algebra")
    quad = make_topic(client, headers, subject, "Quadratics", parent_id=algebra["id"])
    geometry = make_topic(client, headers, subject, "Geometry")
    return subject, ss1, ss2, {"algebra": algebra, "quad": quad}, geometry


# -------------------------------------------------------------------- banks

def test_create_and_list_banks_with_counts(client: TestClient, admin_headers: H) -> None:
    subject = create_subject(client, admin_headers)["id"]
    bank = make_bank(client, admin_headers, subject, "  SS2  ", kind="level")
    make_bank(client, admin_headers, subject, "WASSCE 2008", kind="past_paper", year=2008)
    make_item(client, admin_headers, subject, bank_id=bank["id"])
    make_item(client, admin_headers, subject, bank_id=bank["id"])
    make_item(client, admin_headers, subject)  # no bank: still allowed

    banks = client.get(f"{V1}/subjects/{subject}/banks", headers=admin_headers).json()
    by_name = {b["name"]: b for b in banks}
    assert set(by_name) == {"SS2", "WASSCE 2008"}          # name trimmed
    assert by_name["SS2"]["item_count"] == 2
    assert by_name["WASSCE 2008"] == {**by_name["WASSCE 2008"], "kind": "past_paper", "year": 2008, "item_count": 0}


def test_duplicate_bank_name_in_subject_conflicts(client: TestClient, admin_headers: H) -> None:
    subject = create_subject(client, admin_headers)["id"]
    make_bank(client, admin_headers, subject, "SS2")
    r = client.post(f"{V1}/subjects/{subject}/banks", json={"name": "SS2"}, headers=admin_headers)
    assert r.status_code == 409
    # Same name in another subject is fine.
    other = create_subject(client, admin_headers, name="English")["id"]
    make_bank(client, admin_headers, other, "SS2")


def test_archived_bank_is_hidden_and_cannot_take_new_questions(client: TestClient, admin_headers: H) -> None:
    subject = create_subject(client, admin_headers)["id"]
    bank = make_bank(client, admin_headers, subject, "Old CAT")
    item = make_item(client, admin_headers, subject, bank_id=bank["id"])

    r = client.delete(f"{V1}/subjects/{subject}/banks/{bank['id']}", headers=admin_headers)
    assert r.status_code == 200 and r.json()["status"] == "archived"

    assert client.get(f"{V1}/subjects/{subject}/banks", headers=admin_headers).json() == []
    archived = client.get(f"{V1}/subjects/{subject}/banks", params={"include_archived": True}, headers=admin_headers).json()
    assert [b["id"] for b in archived] == [bank["id"]]

    # Existing question keeps its bank; new ones can't use it.
    assert client.get(f"{V1}/subjects/{subject}/items/{item['id']}", headers=admin_headers).json()["bank_id"] == bank["id"]
    payload = {"question_text": "x", "item_type": "short_answer", "bank_id": bank["id"]}
    assert client.post(f"{V1}/subjects/{subject}/items", json=payload, headers=admin_headers).status_code == 400

    # Restoring makes it usable again.
    r = client.patch(f"{V1}/subjects/{subject}/banks/{bank['id']}", json={"status": "active"}, headers=admin_headers)
    assert r.status_code == 200
    make_item(client, admin_headers, subject, bank_id=bank["id"])


def test_unassigned_teacher_cannot_manage_banks(client: TestClient, admin_headers: H, teacher_headers: H) -> None:
    subject = create_subject(client, admin_headers)["id"]
    assert client.get(f"{V1}/subjects/{subject}/banks", headers=teacher_headers).status_code == 403
    assert client.post(f"{V1}/subjects/{subject}/banks", json={"name": "SS1"}, headers=teacher_headers).status_code == 403


def test_student_cannot_list_banks(client: TestClient, admin_headers: H, student_headers: H) -> None:
    subject = create_subject(client, admin_headers)["id"]
    assert client.get(f"{V1}/subjects/{subject}/banks", headers=student_headers).status_code == 403


# ------------------------------------------------------------------- topics

def test_topics_nest_and_count_questions(client: TestClient, admin_headers: H) -> None:
    subject, _, _, alg, geometry = setup_subject(client, admin_headers)
    make_item(client, admin_headers, subject, topic_ids=[alg["quad"]["id"], geometry["id"]])
    make_item(client, admin_headers, subject, topic_ids=[alg["quad"]["id"]])

    topics = {t["name"]: t for t in client.get(f"{V1}/subjects/{subject}/topics", headers=admin_headers).json()}
    assert topics["Quadratics"]["parent_id"] == alg["algebra"]["id"]
    assert topics["Quadratics"]["item_count"] == 2
    assert topics["Geometry"]["item_count"] == 1
    assert topics["Algebra"]["item_count"] == 0


def test_duplicate_sibling_topic_conflicts_but_cousins_are_fine(client: TestClient, admin_headers: H) -> None:
    subject, _, _, alg, geometry = setup_subject(client, admin_headers)
    r = client.post(f"{V1}/subjects/{subject}/topics", json={"name": "algebra"}, headers=admin_headers)
    assert r.status_code == 409
    make_topic(client, admin_headers, subject, "Quadratics", parent_id=geometry["id"])  # different parent


def test_topic_cannot_move_under_itself(client: TestClient, admin_headers: H) -> None:
    subject, _, _, alg, _ = setup_subject(client, admin_headers)
    r = client.patch(
        f"{V1}/subjects/{subject}/topics/{alg['algebra']['id']}",
        json={"parent_id": alg["quad"]["id"]}, headers=admin_headers,
    )
    assert r.status_code == 400


def test_delete_topic_untags_questions_and_needs_no_children(client: TestClient, admin_headers: H) -> None:
    subject, _, _, alg, geometry = setup_subject(client, admin_headers)
    item = make_item(client, admin_headers, subject, topic_ids=[geometry["id"], alg["quad"]["id"]])

    assert client.delete(f"{V1}/subjects/{subject}/topics/{alg['algebra']['id']}", headers=admin_headers).status_code == 409
    assert client.delete(f"{V1}/subjects/{subject}/topics/{geometry['id']}", headers=admin_headers).status_code == 204

    after = client.get(f"{V1}/subjects/{subject}/items/{item['id']}", headers=admin_headers).json()
    assert after["topic_ids"] == [alg["quad"]["id"]]


# ------------------------------------------------------- items: bank & topics

def test_item_bank_and_topics_round_trip_and_filters(client: TestClient, admin_headers: H) -> None:
    subject, ss1, ss2, alg, geometry = setup_subject(client, admin_headers)
    a = make_item(client, admin_headers, subject, "a", bank_id=ss1["id"], topic_ids=[alg["quad"]["id"]])
    b = make_item(client, admin_headers, subject, "b", bank_id=ss2["id"], topic_ids=[geometry["id"]])
    c = make_item(client, admin_headers, subject, "c")

    assert a["bank_id"] == ss1["id"] and a["topic_ids"] == [alg["quad"]["id"]]
    assert {i["id"] for i in list_items(client, admin_headers, subject)} == {a["id"], b["id"], c["id"]}
    assert [i["id"] for i in list_items(client, admin_headers, subject, bank_id=ss1["id"])] == [a["id"]]
    assert [i["id"] for i in list_items(client, admin_headers, subject, topic_id=geometry["id"])] == [b["id"]]
    assert [i["id"] for i in list_items(client, admin_headers, subject, no_bank=True)] == [c["id"]]
    assert list_items(client, admin_headers, subject, bank_id=ss1["id"], topic_id=geometry["id"]) == []


def test_update_item_topics_and_bank(client: TestClient, admin_headers: H) -> None:
    subject, ss1, _, alg, geometry = setup_subject(client, admin_headers)
    item = make_item(client, admin_headers, subject, bank_id=ss1["id"], topic_ids=[alg["quad"]["id"]])
    url = f"{V1}/subjects/{subject}/items/{item['id']}"

    r = client.patch(url, json={"topic_ids": [geometry["id"]]}, headers=admin_headers)
    assert r.status_code == 200 and r.json()["topic_ids"] == [geometry["id"]] and r.json()["bank_id"] == ss1["id"]

    r = client.patch(url, json={"marks": 3}, headers=admin_headers)          # untouched when not sent
    assert r.json()["topic_ids"] == [geometry["id"]] and r.json()["bank_id"] == ss1["id"]

    r = client.patch(url, json={"bank_id": None, "topic_ids": []}, headers=admin_headers)
    assert r.json()["bank_id"] is None and r.json()["topic_ids"] == []


def test_item_rejects_bank_or_topic_from_another_subject(client: TestClient, admin_headers: H) -> None:
    subject, ss1, _, _, geometry = setup_subject(client, admin_headers)
    other = create_subject(client, admin_headers, name="English")["id"]
    for extra in ({"bank_id": ss1["id"]}, {"topic_ids": [geometry["id"]]}):
        payload = {"question_text": "x", "item_type": "short_answer", **extra}
        r = client.post(f"{V1}/subjects/{other}/items", json=payload, headers=admin_headers)
        assert r.status_code == 400, extra


# --------------------------------------------------------------- bulk upload

def _xlsx(rows: list[list[object]]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["question_text", "item_type", "option_a", "option_b", "correct_answers", "topics"])
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_bulk_upload_into_bank_with_topics_column(client: TestClient, admin_headers: H) -> None:
    subject, _, ss2, alg, geometry = setup_subject(client, admin_headers)
    content = _xlsx([
        ["Q1", "mcq_single", "1", "2", "A", "Algebra > Quadratics; geometry"],
        ["Q2", "mcq_single", "1", "2", "B", ""],
        ["Q3", "mcq_single", "1", "2", "A", "Trigonometry"],
    ])
    r = client.post(
        f"{V1}/subjects/{subject}/items/bulk",
        files={"file": ("q.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"bank_id": ss2["id"]},
        headers=admin_headers,
    )
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["successful_rows"] == 2 and body["failed_rows"] == 1
    assert "Trigonometry" in body["errors"][0]["error"]

    items = {i["question_text"]: i for i in list_items(client, admin_headers, subject)}
    assert items["Q1"]["bank_id"] == ss2["id"] and items["Q2"]["bank_id"] == ss2["id"]
    assert set(items["Q1"]["topic_ids"]) == {alg["quad"]["id"], geometry["id"]}
    assert items["Q2"]["topic_ids"] == []


def test_bulk_upload_without_bank_still_works(client: TestClient, admin_headers: H) -> None:
    subject = create_subject(client, admin_headers)["id"]
    r = client.post(
        f"{V1}/subjects/{subject}/items/bulk",
        files={"file": ("q.xlsx", _xlsx([["Q1", "mcq_single", "1", "2", "A", ""]]),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=admin_headers,
    )
    assert r.status_code == 200 and r.json()["successful_rows"] == 1
    assert list_items(client, admin_headers, subject)[0]["bank_id"] is None


# --------------------------------------------------------------------- exams

def test_exam_scope_round_trip(client: TestClient, admin_headers: H) -> None:
    subject, _, ss2, alg, geometry = setup_subject(client, admin_headers)
    exam = create_exam(client, admin_headers, subject_id=subject, bank_id=ss2["id"], topic_ids=[alg["algebra"]["id"]])
    assert exam["bank_id"] == ss2["id"] and exam["topic_ids"] == [alg["algebra"]["id"]]

    got = client.get(f"{V1}/exams/{exam['id']}", headers=admin_headers).json()
    assert got["topic_ids"] == [alg["algebra"]["id"]]
    listed = client.get(f"{V1}/exams", headers=admin_headers).json()["items"]
    assert listed[0]["topic_ids"] == [alg["algebra"]["id"]] and listed[0]["subject_name"] == "Mathematics"

    r = client.patch(f"{V1}/exams/{exam['id']}", json={"bank_id": None, "topic_ids": [geometry["id"]]}, headers=admin_headers)
    assert r.status_code == 200 and r.json()["bank_id"] is None and r.json()["topic_ids"] == [geometry["id"]]


def test_exam_rejects_scope_from_another_subject(client: TestClient, admin_headers: H) -> None:
    subject, _, ss2, _, _ = setup_subject(client, admin_headers)
    other = create_subject(client, admin_headers, name="English")["id"]
    r = client.post(
        f"{V1}/exams", json={"title": "x", "subject_id": other, "bank_id": ss2["id"]}, headers=admin_headers
    )
    assert r.status_code == 400


def test_exam_without_scope_is_unchanged(client: TestClient, admin_headers: H) -> None:
    subject = create_subject(client, admin_headers)["id"]
    exam = create_exam(client, admin_headers, subject_id=subject)
    assert exam["bank_id"] is None and exam["topic_ids"] == []


def _from_bank(client: TestClient, headers: H, exam_id: str, **payload: object) -> dict:
    r = client.post(f"{V1}/exams/{exam_id}/items/from-bank", json=payload, headers=headers)
    assert r.status_code == 200, r.json()
    return r.json()


def test_add_whole_bank_then_topic_draw(client: TestClient, admin_headers: H) -> None:
    subject, ss1, ss2, alg, geometry = setup_subject(client, admin_headers)
    ss2_alg = [make_item(client, admin_headers, subject, f"alg{i}", bank_id=ss2["id"], topic_ids=[alg["quad"]["id"]]) for i in range(5)]
    ss2_geo = [make_item(client, admin_headers, subject, f"geo{i}", bank_id=ss2["id"], topic_ids=[geometry["id"]]) for i in range(3)]
    make_item(client, admin_headers, subject, "ss1", bank_id=ss1["id"], topic_ids=[alg["quad"]["id"]])
    exam = create_exam(client, admin_headers, subject_id=subject)

    # 2 random Quadratics questions from SS2
    first = _from_bank(client, admin_headers, exam["id"], bank_id=ss2["id"], topic_ids=[alg["quad"]["id"]], count=2)
    assert first["matched"] == 5 and first["added"] == 2
    assert set(first["item_ids"]) <= {i["id"] for i in ss2_alg}

    # The rest of SS2: skips the two already in the exam
    rest = _from_bank(client, admin_headers, exam["id"], bank_id=ss2["id"])
    assert rest["matched"] == 6 and rest["added"] == 6

    exam_item_ids = {i["item_id"] for i in client.get(f"{V1}/exams/{exam['id']}/items", headers=admin_headers).json()}
    assert exam_item_ids == {i["id"] for i in ss2_alg + ss2_geo}
    assert client.get(f"{V1}/exams/{exam['id']}", headers=admin_headers).json()["total_marks"] == 8

    # Nothing left to add
    assert _from_bank(client, admin_headers, exam["id"], bank_id=ss2["id"]) == {"matched": 0, "added": 0, "item_ids": []}


def test_from_bank_filters_difficulty_and_skips_archived(client: TestClient, admin_headers: H) -> None:
    subject, _, ss2, _, _ = setup_subject(client, admin_headers)
    easy = make_item(client, admin_headers, subject, "easy", bank_id=ss2["id"], difficulty="easy")
    make_item(client, admin_headers, subject, "hard", bank_id=ss2["id"], difficulty="hard")
    gone = make_item(client, admin_headers, subject, "gone", bank_id=ss2["id"], difficulty="easy")
    client.delete(f"{V1}/subjects/{subject}/items/{gone['id']}", headers=admin_headers)
    exam = create_exam(client, admin_headers, subject_id=subject)

    result = _from_bank(client, admin_headers, exam["id"], bank_id=ss2["id"], difficulty="easy")
    assert result["item_ids"] == [easy["id"]]


def test_from_bank_only_draws_from_the_exam_subject(client: TestClient, admin_headers: H) -> None:
    subject, _, ss2, _, _ = setup_subject(client, admin_headers)
    other = create_subject(client, admin_headers, name="English")["id"]
    make_item(client, admin_headers, other, "english q")
    exam = create_exam(client, admin_headers, subject_id=subject)

    assert _from_bank(client, admin_headers, exam["id"])["matched"] == 0
    r = client.post(f"{V1}/exams/{exam['id']}/items/from-bank", json={"bank_id": ss2["id"]}, headers=admin_headers)
    assert r.status_code == 200
    other_bank = make_bank(client, admin_headers, other, "Eng SS2")
    r = client.post(f"{V1}/exams/{exam['id']}/items/from-bank", json={"bank_id": other_bank["id"]}, headers=admin_headers)
    assert r.status_code == 400
