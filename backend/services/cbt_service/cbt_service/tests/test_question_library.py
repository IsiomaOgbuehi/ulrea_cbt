"""
Exam bodies' shared questions: organisations copy questions from the banks
bodies share (question library), and students practise the bodies' External
Exams questions by topic (topic tests, marked without storing anything).
"""
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlmodel import Session, col, select
from starlette.testclient import TestClient

from cbt_service.database.models.bank_topic import ItemTopic, QuestionBank, Topic
from cbt_service.database.models.item import ItemModel
from cbt_service.database.models.subject import SubjectModel
from .conftest import ORG_ID, TEACHER_ID, engine, make_auth_header, make_token_payload
from .test_external_practice import _listed, access  # noqa: F401  (fixture)

H = dict[str, str]
V1 = "/api/v1"
SCHOOL_ID = uuid4()
SCHOOL_ADMIN = make_auth_header(make_token_payload(uuid4(), SCHOOL_ID, "admin"))


@pytest.fixture
def plan(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"allowed": True}

    async def bodies(self: Any) -> dict[UUID, str]:
        return {ORG_ID: "WAEC"}

    async def allowed(self: Any, org_id: UUID) -> tuple[bool, str | None]:
        return state["allowed"], None if state["allowed"] else "Your plan has ended."

    monkeypatch.setattr("cbt_service.clients.auth_client.AuthClient.exam_bodies", bodies)
    monkeypatch.setattr("cbt_service.clients.auth_client.AuthClient.question_library_allowed", allowed)
    return state


def _school_subject() -> UUID:
    sid = uuid4()
    with Session(engine) as s:
        s.add(SubjectModel(id=sid, org_id=SCHOOL_ID, name="School maths", created_by=TEACHER_ID))
        s.commit()
    return sid


def _tag(exam: dict[str, Any], name: str, item_ids: list[UUID]) -> UUID:
    """Put the exam body's questions under a topic of their subject."""
    with Session(engine) as s:
        first = s.get(ItemModel, item_ids[0])
        assert first is not None
        topic = Topic(org_id=ORG_ID, subject_id=first.subject_id, name=name, created_by=TEACHER_ID)
        s.add(topic)
        s.flush()
        for i in item_ids:
            s.add(ItemTopic(item_id=i, topic_id=topic.id))
        s.commit()
        return topic.id


def _subject_of(exam: dict[str, Any]) -> UUID:
    with Session(engine) as s:
        item = s.get(ItemModel, exam["items"][0]["item_id"])
        assert item is not None
        return item.subject_id


def _bank(exam: dict[str, Any], name: str, shared: bool = True, n: int | None = None) -> UUID:
    """A bank in the exam body's subject holding the exam's questions (or the first n)."""
    ids = [i["item_id"] for i in exam["items"]][: n]
    with Session(engine) as s:
        bank = QuestionBank(org_id=ORG_ID, subject_id=_subject_of(exam), name=name, created_by=TEACHER_ID, shared=shared)
        s.add(bank)
        s.flush()
        for item in s.exec(select(ItemModel).where(col(ItemModel.id).in_(ids))).all():
            item.bank_id = bank.id
            s.add(item)
        s.commit()
        return bank.id


def _plain_exam(n_mcq: int = 2) -> dict[str, Any]:
    from .test_exam_settings import _exam
    return _exam(n_mcq=n_mcq)                     # not offered in External Exams


def test_library_shows_shared_banks_only_on_a_plan(client: TestClient, plan: dict[str, Any]) -> None:
    exam = _plain_exam(n_mcq=3)                   # 3 MCQs + true/false + essay
    bank = _bank(exam, "WAEC 2023", n=4)
    lib = client.get(f"{V1}/question-library", headers=SCHOOL_ADMIN).json()
    assert lib["allowed"] is True and lib["bodies"] == [{"id": str(ORG_ID), "name": "WAEC", "question_count": 4}]
    subjects = client.get(f"{V1}/question-library/bodies/{ORG_ID}/subjects", headers=SCHOOL_ADMIN).json()
    mine = next(s for s in subjects if s["id"] == str(_subject_of(exam)))
    assert mine["question_count"] == 4
    assert [(b["id"], b["name"], b["question_count"]) for b in mine["banks"]] == [(str(bank), "WAEC 2023", 4)]
    page = client.get(
        f"{V1}/question-library/bodies/{ORG_ID}/items", params={"bank_id": str(bank)}, headers=SCHOOL_ADMIN,
    ).json()
    assert page["total"] == 4 and all(i["bank_name"] == "WAEC 2023" for i in page["items"])
    assert all(i["correct_answers"] == ["A"] for i in page["items"] if i["item_type"] == "mcq_single")

    plan["allowed"] = False
    r = client.get(f"{V1}/question-library/bodies/{ORG_ID}/items", headers=SCHOOL_ADMIN)
    assert r.status_code == 403 and "plan" in r.json()["detail"]
    assert client.get(f"{V1}/question-library", headers=SCHOOL_ADMIN).json()["allowed"] is False


def test_only_shared_banks_not_practice_exams(client: TestClient, plan: dict[str, Any], admin_headers: H) -> None:
    listed = _listed(n_mcq=2)                     # offered in External Exams, but in no shared bank
    private = _bank(_plain_exam(), "Internal", shared=False)
    for params in ({"subject_id": str(_subject_of(listed))}, {"bank_id": str(private)}):
        page = client.get(f"{V1}/question-library/bodies/{ORG_ID}/items", params=params, headers=SCHOOL_ADMIN).json()
        assert page["total"] == 0

    # The body shares a bank with the ordinary bank update; unsharing hides it again.
    sid = _subject_of(listed)
    with Session(engine) as s:
        b = QuestionBank(org_id=ORG_ID, subject_id=sid, name="Past papers", created_by=TEACHER_ID)
        s.add(b)
        s.commit()
        bank_id = b.id
    r = client.patch(f"{V1}/subjects/{sid}/banks/{bank_id}", json={"shared": True}, headers=admin_headers)
    assert r.status_code == 200 and r.json()["shared"] is True
    client.patch(f"{V1}/subjects/{sid}/banks/{bank_id}", json={"shared": False}, headers=admin_headers)
    with Session(engine) as s:
        bank = s.get(QuestionBank, bank_id)
        assert bank is not None and bank.shared is False


def test_copy_into_a_subject_once(client: TestClient, plan: dict[str, Any]) -> None:
    exam = _plain_exam(n_mcq=2)
    _bank(exam, "WAEC 2022")
    ids = [str(i["item_id"]) for i in exam["items"][:2]]
    target = _school_subject()
    body = {"exam_body_org_id": str(ORG_ID), "item_ids": ids, "subject_id": str(target)}
    r = client.post(f"{V1}/question-library/copy", json=body, headers=SCHOOL_ADMIN)
    assert r.status_code == 200, r.json()
    assert r.json()["copied"] == 2 and r.json()["skipped"] == 0
    again = client.post(f"{V1}/question-library/copy", json=body, headers=SCHOOL_ADMIN).json()
    assert again["copied"] == 0 and again["skipped"] == 2
    with Session(engine) as s:
        copies = s.exec(select(ItemModel).where(ItemModel.org_id == SCHOOL_ID, ItemModel.subject_id == target)).all()
        assert {str(c.copied_from_item_id) for c in copies} == set(ids)
        assert all(c.source == "imported" and c.correct_answers == ["A"] for c in copies)
    page = client.get(
        f"{V1}/question-library/bodies/{ORG_ID}/items",
        params={"subject_id": str(_subject_of(exam)), "into_subject_id": str(target)}, headers=SCHOOL_ADMIN,
    ).json()
    assert sum(1 for i in page["items"] if i["already_copied"]) == 2

    # Not into another organisation's subject.
    other = {**body, "subject_id": str(_subject_of(exam))}
    assert client.post(f"{V1}/question-library/copy", json=other, headers=SCHOOL_ADMIN).status_code == 404


def test_copy_a_whole_bank_into_a_bank_of_the_same_name(client: TestClient, plan: dict[str, Any]) -> None:
    exam = _plain_exam(n_mcq=3)
    source = _bank(exam, "WAEC 2021")             # 3 MCQs + true/false + essay
    target = _school_subject()
    body = {"exam_body_org_id": str(ORG_ID), "source_bank_id": str(source), "subject_id": str(target), "new_bank": True}
    r = client.post(f"{V1}/question-library/copy", json=body, headers=SCHOOL_ADMIN)
    assert r.status_code == 200, r.json()
    assert r.json()["copied"] == 5 and r.json()["bank_id"]
    with Session(engine) as s:
        bank = s.get(QuestionBank, UUID(r.json()["bank_id"]))
        assert bank is not None and bank.org_id == SCHOOL_ID and bank.name == "WAEC 2021" and bank.shared is False
        n = len(s.exec(select(ItemModel).where(ItemModel.bank_id == bank.id)).all())
        assert n == 5
    # Copying it again reuses the bank and adds nothing.
    again = client.post(f"{V1}/question-library/copy", json=body, headers=SCHOOL_ADMIN).json()
    assert again["copied"] == 0 and again["bank_id"] == r.json()["bank_id"]

    # Unshared: nothing to copy.
    with Session(engine) as s:
        b = s.get(QuestionBank, source)
        assert b is not None
        b.shared = False
        s.add(b)
        s.commit()
    assert client.post(f"{V1}/question-library/copy", json=body, headers=SCHOOL_ADMIN).status_code == 404


def test_add_shared_questions_straight_into_an_exam(client: TestClient, plan: dict[str, Any]) -> None:
    from cbt_service.database.models.enums.exam_enum import ExamStatus
    from cbt_service.database.models.exam import ExamItem, ExamModel
    exam = _plain_exam(n_mcq=2)
    _bank(exam, "WAEC 2020")
    target = _school_subject()
    school_exam = ExamModel(org_id=SCHOOL_ID, subject_id=target, created_by=TEACHER_ID, title="Mock", status=ExamStatus.DRAFT)
    with Session(engine) as s:
        s.add(school_exam)
        s.commit()
        s.refresh(school_exam)
    ids = [str(i["item_id"]) for i in exam["items"][:2]]
    body = {"exam_body_org_id": str(ORG_ID), "item_ids": ids, "exam_id": str(school_exam.id)}
    r = client.post(f"{V1}/question-library/copy", json=body, headers=SCHOOL_ADMIN)
    assert r.status_code == 200, r.json()
    assert r.json()["copied"] == 2 and r.json()["added_to_exam"] == 2
    with Session(engine) as s:
        in_exam = s.exec(select(ExamItem.item_id).where(ExamItem.exam_id == school_exam.id)).all()
        copies = s.exec(select(ItemModel).where(col(ItemModel.id).in_(list(in_exam)))).all()
        assert len(copies) == 2 and all(c.org_id == SCHOOL_ID and str(c.copied_from_item_id) in ids for c in copies)
    # Again: the earlier copies are reused and the exam doesn't get duplicates.
    again = client.post(f"{V1}/question-library/copy", json=body, headers=SCHOOL_ADMIN).json()
    assert again["copied"] == 0 and again["added_to_exam"] == 0


def test_topic_test_draws_without_answers_and_marks_statelessly(
    client: TestClient, student_headers: H, access: dict[str, bool],
) -> None:
    exam = _listed(n_mcq=4)
    mcqs = [i["item_id"] for i in exam["items"] if i["type"] == "mcq_single"]
    topic = _tag(exam, "Algebra", mcqs[:3])
    subject = _subject_of(exam)

    topics = client.get(f"{V1}/external/bodies/{ORG_ID}/topics", headers=student_headers).json()
    mine = next(s for s in topics if s["id"] == str(subject))
    assert mine["question_count"] == 5                          # objective only: 4 MCQs + true/false
    assert mine["topics"] == [{"id": str(topic), "name": "Algebra", "parent_id": None, "question_count": 3}]

    test = client.post(
        f"{V1}/external/bodies/{ORG_ID}/topic-test",
        json={"subject_id": str(subject), "topic_ids": [str(topic)], "count": 10}, headers=student_headers,
    ).json()
    assert len(test["items"]) == 3 and all("correct_answers" not in i for i in test["items"])
    drawn = [i["id"] for i in test["items"]]
    answers = [{"item_id": drawn[0], "answer": ["A"]}, {"item_id": drawn[1], "answer": ["B"]}, {"item_id": drawn[2]}]
    result = client.post(f"{V1}/external/bodies/{ORG_ID}/topic-test/score", json={"answers": answers}, headers=student_headers).json()
    assert result["score"] == 1 and result["max_score"] == 3 and result["percentage"] == 33.3 and result["missing"] == 0
    assert all(m["correct_answers"] == ["A"] for m in result["items"])

    # Unsubscribed students can't draw or mark.
    access["subscribed"] = False
    assert client.post(
        f"{V1}/external/bodies/{ORG_ID}/topic-test/score", json={"answers": answers}, headers=student_headers,
    ).status_code == 403


def test_topic_test_ignores_questions_that_are_not_shared(client: TestClient, student_headers: H, access: dict[str, bool]) -> None:
    from .test_exam_settings import _exam
    secret = _exam(n_mcq=1)                       # not offered in External Exams
    item = secret["items"][0]["item_id"]
    r = client.post(
        f"{V1}/external/bodies/{ORG_ID}/topic-test/score",
        json={"answers": [{"item_id": str(item), "answer": ["A"]}]}, headers=student_headers,
    ).json()
    assert r["items"] == [] and r["missing"] == 1
