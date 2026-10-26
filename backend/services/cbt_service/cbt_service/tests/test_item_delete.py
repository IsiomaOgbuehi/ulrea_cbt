"""Deleting a question for good: only when no student answered it and it isn't on an exam past draft."""
from uuid import UUID, uuid4

from sqlmodel import Session, select
from starlette.testclient import TestClient

from cbt_service.database.models.attempt import ResponseModel
from cbt_service.database.models.exam import ExamAuditLog, ExamItem, ExamModel
from cbt_service.database.models.item import ItemModel
from .conftest import ORG_ID, add_items, create_exam, engine, seed_items

H = dict[str, str]
V1 = "/api/v1"


def _item_url(subject_id: str, item_id: UUID) -> str:
    return f"{V1}/subjects/{subject_id}/items/{item_id}"


def _set_exam_status(exam_id: str, status: str) -> None:
    with Session(engine) as session:
        exam = session.get(ExamModel, UUID(exam_id))
        assert exam is not None
        exam.status = status
        session.add(exam)
        session.commit()


def test_unused_question_is_deleted_for_good(client: TestClient, admin_headers: H) -> None:
    subject = str(uuid4())
    [item_id] = seed_items(subject)
    usage = client.get(_item_url(subject, item_id) + "/usage", headers=admin_headers).json()
    assert usage == {"answered_count": 0, "exams": [], "can_delete": True, "reason": None}

    assert client.delete(_item_url(subject, item_id), headers=admin_headers).status_code == 204
    with Session(engine) as session:
        assert session.get(ItemModel, item_id) is None
    assert client.get(_item_url(subject, item_id), headers=admin_headers).status_code == 404


def test_deleting_takes_it_off_draft_exams(client: TestClient, admin_headers: H) -> None:
    subject = str(uuid4())
    keep, gone = seed_items(subject, count=2)
    exam = create_exam(client, admin_headers, subject_id=subject)
    add_items(client, admin_headers, exam["id"], [keep, gone])
    before = client.get(f"{V1}/exams/{exam['id']}", headers=admin_headers).json()["total_marks"]

    usage = client.get(_item_url(subject, gone) + "/usage", headers=admin_headers).json()
    assert usage["can_delete"] and [e["title"] for e in usage["exams"]] == [exam["title"]]

    assert client.delete(_item_url(subject, gone), headers=admin_headers).status_code == 204
    with Session(engine) as session:
        left = session.exec(select(ExamItem).where(ExamItem.exam_id == UUID(exam["id"]))).all()
        assert [ei.item_id for ei in left] == [keep]
        log = session.exec(
            select(ExamAuditLog).where(ExamAuditLog.exam_id == UUID(exam["id"]), ExamAuditLog.action == "item_removed")
        ).one()
        assert log.extra_data == {"item_id": str(gone), "reason": "question deleted"}
    after = client.get(f"{V1}/exams/{exam['id']}", headers=admin_headers).json()["total_marks"]
    assert after == before - 1


def test_answered_question_cannot_be_deleted(client: TestClient, admin_headers: H) -> None:
    subject = str(uuid4())
    [item_id] = seed_items(subject)
    with Session(engine) as session:
        session.add(ResponseModel(attempt_id=uuid4(), item_id=item_id, exam_item_id=uuid4(), org_id=ORG_ID, answer=["A"]))
        session.commit()

    usage = client.get(_item_url(subject, item_id) + "/usage", headers=admin_headers).json()
    assert usage["answered_count"] == 1 and usage["can_delete"] is False
    r = client.delete(_item_url(subject, item_id), headers=admin_headers)
    assert r.status_code == 409 and "archive" in r.json()["detail"].lower()
    with Session(engine) as session:
        assert session.get(ItemModel, item_id) is not None


def test_question_on_an_approved_exam_cannot_be_deleted(client: TestClient, admin_headers: H) -> None:
    subject = str(uuid4())
    [item_id] = seed_items(subject)
    exam = create_exam(client, admin_headers, subject_id=subject, title="Mock exam")
    add_items(client, admin_headers, exam["id"], [item_id])
    _set_exam_status(exam["id"], "approved")

    r = client.delete(_item_url(subject, item_id), headers=admin_headers)
    assert r.status_code == 409 and "Mock exam" in r.json()["detail"]


def test_archiving_still_keeps_the_question(client: TestClient, admin_headers: H) -> None:
    subject = str(uuid4())
    [item_id] = seed_items(subject)
    r = client.patch(_item_url(subject, item_id) + "/status", json={"status": "archived"}, headers=admin_headers)
    assert r.status_code == 200 and r.json()["status"] == "archived"


def test_teacher_cannot_delete_outside_their_subjects(client: TestClient, teacher_headers: H) -> None:
    subject = str(uuid4())
    [item_id] = seed_items(subject)
    assert client.delete(_item_url(subject, item_id), headers=teacher_headers).status_code == 403
