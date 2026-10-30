"""
Written answers end to end: short answers auto-marked against accepted
answers, essays waiting in the marking queue, marks capped, and the attempt
re-scored (total, %, pass/fail) on every save.
"""
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlmodel import Session
from starlette.testclient import TestClient

from cbt_service.database.models.enums.exam_enum import AssignmentStatus, ExamStatus
from cbt_service.database.models.enums.item_subject_enums import ItemStatus, ItemType
from cbt_service.database.models.exam import ExamAssignment, ExamItem, ExamModel, ExamSection
from cbt_service.database.models.item import ItemModel
from cbt_service.database.models.subject import SubjectModel
from cbt_service.schemas.marking_schemas import MarkingScript
from cbt_service.services.marking.scoring import normalise_answer, score_answer
from .conftest import ORG_ID, STUDENT_ID, TEACHER_ID, engine

H = dict[str, str]
V1 = "/api/v1"


# ------------------------------------------------------------- pure scoring

def test_normalise_answer_ignores_case_spacing_punctuation_and_markup() -> None:
    assert normalise_answer("  The   <b>Nile</b>. ") == "the nile"
    assert normalise_answer("“Abuja”") == "abuja"


def test_score_answer_rules() -> None:
    auto = {"auto_mark_short_answers": True}
    assert score_answer("short_answer", ["Abuja", "FCT"], ["abuja!"], 2, **auto).marks_awarded == 2
    wrong = score_answer("short_answer", ["Abuja"], ["Lagos"], 2, 0.5, **auto)
    assert (wrong.is_correct, wrong.marks_awarded) == (False, -0.5)
    # Off (the default): even a matching short answer waits for a person.
    assert score_answer("short_answer", ["Abuja"], ["Abuja"], 2).needs_marking
    assert score_answer("short_answer", None, ["anything"], 2).needs_marking
    assert score_answer("essay", None, ["<p>My essay</p>"], 5).needs_marking
    blank = score_answer("essay", None, ["<p> </p>"], 5)
    assert (blank.is_correct, blank.marks_awarded) == (False, 0.0)


# ------------------------------------------------------------- end to end

def _make_exam(auto_mark: bool) -> dict[str, Any]:
    """MCQ 2 marks, short answer with accepted answers 2, short answer to mark 3, essay 5 (= 12)."""
    subject_id, exam_id, section_id = uuid4(), uuid4(), uuid4()
    specs: list[tuple[str, ItemType, list[str] | None, float]] = [
        ("mcq", ItemType.MCQ_SINGLE, ["B"], 2.0),
        ("capital", ItemType.SHORT_ANSWER, ["Abuja", "FCT Abuja"], 2.0),
        ("define", ItemType.SHORT_ANSWER, None, 3.0),
        ("essay", ItemType.ESSAY, None, 5.0),
    ]
    out: dict[str, Any] = {"exam_id": exam_id}
    with Session(engine) as session:
        session.add(SubjectModel(id=subject_id, org_id=ORG_ID, name="Civics", created_by=TEACHER_ID))
        session.add(ExamModel(
            id=exam_id, org_id=ORG_ID, subject_id=subject_id, created_by=TEACHER_ID, title="Civics paper",
            status=ExamStatus.ACTIVE, pass_mark=50, total_marks=12, max_attempts=1,
            auto_mark_short_answers=auto_mark,
        ))
        session.add(ExamSection(id=section_id, exam_id=exam_id, org_id=ORG_ID, title="All", order=0))
        for n, (name, item_type, correct, marks) in enumerate(specs):
            item = ItemModel(
                org_id=ORG_ID, subject_id=subject_id, created_by=TEACHER_ID, question_text=f"Q {name}",
                item_type=item_type, correct_answers=correct, marks=marks, status=ItemStatus.ACTIVE,
                options=[{"key": "A", "text": "x"}, {"key": "B", "text": "y"}] if name == "mcq" else None,
                marking_guide="2 marks for X" if name == "essay" else None, word_limit=150 if name == "essay" else None,
            )
            session.add(item)
            session.flush()
            exam_item = ExamItem(exam_id=exam_id, section_id=section_id, item_id=item.id, org_id=ORG_ID, order=n, marks=marks)
            session.add(exam_item)
            session.flush()
            out[name] = {"item_id": item.id, "exam_item_id": exam_item.id}
        assignment = ExamAssignment(
            exam_id=exam_id, student_id=STUDENT_ID, org_id=ORG_ID, assigned_by=TEACHER_ID,
            status=AssignmentStatus.ASSIGNED,
        )
        session.add(assignment)
        session.commit()
        out["assignment_id"] = assignment.id
    return out


@pytest.fixture
def exam_setup(client: TestClient) -> dict[str, Any]:
    """An exam that auto-marks short answers."""
    return _make_exam(auto_mark=True)


@pytest.fixture
def manual_exam(client: TestClient) -> dict[str, Any]:
    """The default: every written answer is marked by a person."""
    return _make_exam(auto_mark=False)


def _sit_exam(client: TestClient, student_headers: H, setup: dict[str, Any], answers: dict[str, list[str]]) -> dict[str, Any]:
    r = client.post(f"{V1}/attempts", json={"exam_id": str(setup["exam_id"]), "assignment_id": str(setup["assignment_id"])}, headers=student_headers)
    assert r.status_code == 200, r.json()
    attempt = r.json()
    for name, answer in answers.items():
        q = setup[name]
        r = client.post(f"{V1}/attempts/{attempt['id']}/responses", json={
            "item_id": str(q["item_id"]), "exam_item_id": str(q["exam_item_id"]), "answer": answer,
        }, headers=student_headers)
        assert r.status_code == 200, r.json()
    r = client.post(f"{V1}/attempts/{attempt['id']}/submit", headers=student_headers)
    assert r.status_code == 200, r.json()
    return r.json()


def test_students_get_the_word_limit_but_never_the_marking_guide(client: TestClient, exam_setup: dict[str, Any], student_headers: H) -> None:
    r = client.post(f"{V1}/attempts", json={"exam_id": str(exam_setup["exam_id"]), "assignment_id": str(exam_setup["assignment_id"])}, headers=student_headers)
    content = client.get(f"{V1}/attempts/{r.json()['id']}/exam", headers=student_headers).json()
    essay = next(i for s in content["sections"] for i in s["items"] if i["item_type"] == "essay")
    assert essay["word_limit"] == 150
    assert "marking_guide" not in str(content) and "Abuja" not in str(content)


def test_submission_auto_marks_what_it_can_out_of_the_whole_exam(client: TestClient, exam_setup: dict[str, Any], student_headers: H) -> None:
    attempt = _sit_exam(client, student_headers, exam_setup, {
        "mcq": ["B"], "capital": [" abuja. "], "essay": ["<p>Long <b>answer</b></p>"],
        # "define" left unanswered
    })
    assert attempt["final_score"] == 4.0
    assert attempt["percentage"] == round(4 / 12 * 100, 2)   # out of 12, not just the answered ones
    assert attempt["passed"] is None                          # the essay still has to be marked


def test_marking_queue_and_rescoring(client: TestClient, exam_setup: dict[str, Any], student_headers: H, teacher_headers: H, admin_headers: H) -> None:
    attempt = _sit_exam(client, student_headers, exam_setup, {
        "mcq": ["B"], "capital": ["Lagos"], "define": ["A rule made by a legislature"], "essay": ["<p>My essay</p>"],
    })
    assert attempt["final_score"] == 2.0

    overview = client.get(f"{V1}/exams/{exam_setup['exam_id']}/marking", headers=admin_headers).json()
    by_type = {(q["item_type"], q["marks"]): q for q in overview["questions"]}
    assert [q["item_type"] for q in overview["questions"]] == ["short_answer", "short_answer", "essay"]
    assert overview["pending"] == 2 and overview["attempts_waiting"] == 1
    assert by_type[("short_answer", 2.0)]["auto_marked"] == 1          # "Lagos" marked wrong automatically
    assert by_type[("essay", 5.0)]["marking_guide"] == "2 marks for X"

    essay_q = exam_setup["essay"]["exam_item_id"]
    page = client.get(f"{V1}/exams/{exam_setup['exam_id']}/marking/{essay_q}/answers", headers=admin_headers).json()
    assert page["total"] == 1
    answer = page["items"][0]
    assert answer["student_name"] == "Test User" and answer["word_count"] == 2
    anon = client.get(f"{V1}/exams/{exam_setup['exam_id']}/marking/{essay_q}/answers?anonymous=true", headers=admin_headers).json()
    assert anon["items"][0]["student_name"] is None

    url = f"{V1}/attempts/responses/{answer['response_id']}/review"
    assert client.post(url, json={"marks_awarded": 6}, headers=admin_headers).status_code == 422     # over 5
    assert client.post(url, json={"marks_awarded": -1}, headers=admin_headers).status_code == 422

    r = client.post(url, json={"marks_awarded": 4, "review_notes": "Good"}, headers=admin_headers)
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["is_correct"] is None and body["attempt_final_score"] == 6.0
    assert body["attempt_pending"] == 1 and body["attempt_passed"] is None

    define_q = exam_setup["define"]["exam_item_id"]
    define = client.get(f"{V1}/exams/{exam_setup['exam_id']}/marking/{define_q}/answers", headers=admin_headers).json()["items"][0]
    r = client.post(f"{V1}/attempts/responses/{define['response_id']}/review", json={"marks_awarded": 3}, headers=admin_headers).json()
    assert r["attempt_final_score"] == 9.0 and r["attempt_percentage"] == 75.0
    assert r["attempt_pending"] == 0 and r["attempt_passed"] is True      # 75% >= 50%

    # Override an automatic mark ("Lagos" -> the marker gives 1 of 2).
    capital_q = exam_setup["capital"]["exam_item_id"]
    auto = client.get(f"{V1}/exams/{exam_setup['exam_id']}/marking/{capital_q}/answers?status=auto", headers=admin_headers).json()["items"][0]
    r = client.post(f"{V1}/attempts/responses/{auto['response_id']}/review", json={"marks_awarded": 1}, headers=admin_headers).json()
    assert r["attempt_final_score"] == 10.0

    overview = client.get(f"{V1}/exams/{exam_setup['exam_id']}/marking", headers=admin_headers).json()
    assert overview["pending"] == 0 and overview["marked"] == 3

    detail = client.get(f"{V1}/attempts/{attempt['id']}/detail", headers=admin_headers).json()
    essay = next(d for d in detail["responses"] if d["item_type"] == "essay")
    assert essay["response_id"] and essay["question_text"] == "Q essay" and essay["max_marks"] == 5
    assert essay["marking_guide"] == "2 marks for X" and essay["review_notes"] == "Good"


def test_cannot_mark_an_attempt_still_in_progress(client: TestClient, exam_setup: dict[str, Any], student_headers: H, admin_headers: H) -> None:
    r = client.post(f"{V1}/attempts", json={"exam_id": str(exam_setup["exam_id"]), "assignment_id": str(exam_setup["assignment_id"])}, headers=student_headers)
    q = exam_setup["essay"]
    resp = client.post(f"{V1}/attempts/{r.json()['id']}/responses", json={
        "item_id": str(q["item_id"]), "exam_item_id": str(q["exam_item_id"]), "answer": ["draft"],
    }, headers=student_headers).json()
    r = client.post(f"{V1}/attempts/responses/{resp['id']}/review", json={"marks_awarded": 1}, headers=admin_headers)
    assert r.status_code == 409


def test_students_cannot_open_the_marking_queue(client: TestClient, exam_setup: dict[str, Any], student_headers: H) -> None:
    assert client.get(f"{V1}/exams/{exam_setup['exam_id']}/marking", headers=student_headers).status_code == 403


def test_questions_keep_accepted_answers_and_marking_guide(client: TestClient, admin_headers: H) -> None:
    subject = client.post(f"{V1}/subjects", json={"name": "English"}, headers=admin_headers).json()
    r = client.post(f"{V1}/subjects/{subject['id']}/items", json={
        "question_text": "Plural of mouse?", "item_type": "short_answer",
        "correct_answers": [" mice ", "Mice", "", "MICE."], "marking_guide": "  Accept mice only  ",
        "options": [{"key": "A", "text": "ignored"}], "word_limit": 50,
    }, headers=admin_headers)
    assert r.status_code == 200, r.json()
    item = r.json()
    assert item["correct_answers"] == ["mice"] and item["options"] is None
    assert item["marking_guide"] == "Accept mice only" and item["word_limit"] is None

    r = client.post(f"{V1}/subjects/{subject['id']}/items", json={
        "question_text": "Write about your hero", "item_type": "essay", "correct_answers": ["x"], "word_limit": 300,
    }, headers=admin_headers)
    assert r.status_code == 200, r.json()
    assert r.json()["item_type"] == "essay" and r.json()["correct_answers"] is None and r.json()["word_limit"] == 300


def test_exams_copy_each_questions_marks_and_drafts_follow_edits(client: TestClient, admin_headers: H) -> None:
    from .conftest import create_exam
    subject = client.post(f"{V1}/subjects", json={"name": "Econ"}, headers=admin_headers).json()
    mk = lambda text, marks, neg=0.0: client.post(f"{V1}/subjects/{subject['id']}/items", json={  # noqa: E731
        "question_text": text, "item_type": "essay", "marks": marks, "negative_marks": neg,
    }, headers=admin_headers).json()
    a, b = mk("Essay A", 5, 0.5), mk("Essay B", 3)
    exam = create_exam(client, admin_headers, subject_id=subject["id"])
    r = client.post(f"{V1}/exams/{exam['id']}/items", json={"item_ids": [a["id"], b["id"]]}, headers=admin_headers)
    assert r.status_code == 200, r.json()
    assert client.get(f"{V1}/exams/{exam['id']}", headers=admin_headers).json()["total_marks"] == 8
    with Session(engine) as session:
        from sqlmodel import select
        rows = {str(ei.item_id): ei for ei in session.exec(select(ExamItem).where(ExamItem.exam_id == UUID(exam["id"]))).all()}
    assert (rows[a["id"]].marks, rows[a["id"]].negative_marks) == (5, 0.5)

    # Still a draft: editing the question's marks updates the exam.
    client.patch(f"{V1}/subjects/{subject['id']}/items/{b['id']}", json={"marks": 4}, headers=admin_headers)
    assert client.get(f"{V1}/exams/{exam['id']}", headers=admin_headers).json()["total_marks"] == 9

    # Past draft: the exam keeps its copy.
    with Session(engine) as session:
        e = session.get(ExamModel, UUID(exam["id"]))
        e.status = ExamStatus.APPROVED
        session.add(e)
        session.commit()
    client.patch(f"{V1}/subjects/{subject['id']}/items/{b['id']}", json={"marks": 10}, headers=admin_headers)
    assert client.get(f"{V1}/exams/{exam['id']}", headers=admin_headers).json()["total_marks"] == 9


def test_attempt_reads_say_how_many_answers_await_marking(client: TestClient, exam_setup: dict[str, Any], student_headers: H, admin_headers: H) -> None:
    attempt = _sit_exam(client, student_headers, exam_setup, {"mcq": ["B"], "essay": ["<p>Essay</p>"]})
    assert attempt["awaiting_marking"] == 1 and attempt["passed"] is None
    assert client.get(f"{V1}/attempts/{attempt['id']}", headers=student_headers).json()["awaiting_marking"] == 1
    assert client.get(f"{V1}/attempts/{attempt['id']}/detail", headers=admin_headers).json()["awaiting_marking"] == 1


def test_answers_only_for_questions_on_the_exam_and_linked_server_side(client: TestClient, exam_setup: dict[str, Any], student_headers: H) -> None:
    r = client.post(f"{V1}/attempts", json={"exam_id": str(exam_setup["exam_id"]), "assignment_id": str(exam_setup["assignment_id"])}, headers=student_headers)
    attempt_id = r.json()["id"]
    stranger = uuid4()
    r = client.post(f"{V1}/attempts/{attempt_id}/responses", json={
        "item_id": str(stranger), "exam_item_id": str(stranger), "answer": ["A"],
    }, headers=student_headers)
    assert r.status_code == 422

    # The client used to send the question's id as exam_item_id; it's resolved on the server now.
    q = exam_setup["essay"]
    r = client.post(f"{V1}/attempts/{attempt_id}/responses", json={
        "item_id": str(q["item_id"]), "exam_item_id": str(q["item_id"]), "answer": ["<p>x</p>"],
    }, headers=student_headers)
    assert r.status_code == 200, r.json()
    from cbt_service.database.models.attempt import ResponseModel
    with Session(engine) as session:
        assert session.get(ResponseModel, UUID(r.json()["id"])).exam_item_id == q["exam_item_id"]



def test_by_default_short_answers_wait_for_a_person_with_a_match_hint(client: TestClient, manual_exam: dict[str, Any], student_headers: H, admin_headers: H) -> None:
    attempt = _sit_exam(client, student_headers, manual_exam, {"mcq": ["B"], "capital": ["abuja."], "define": ["x"]})
    assert attempt["final_score"] == 2.0 and attempt["awaiting_marking"] == 2
    overview = client.get(f"{V1}/exams/{manual_exam['exam_id']}/marking", headers=admin_headers).json()
    assert overview["auto_mark_short_answers"] is False and overview["pending"] == 2
    capital_q = manual_exam["capital"]["exam_item_id"]
    answer = client.get(f"{V1}/exams/{manual_exam['exam_id']}/marking/{capital_q}/answers", headers=admin_headers).json()["items"][0]
    assert answer["matches_accepted"] is True and answer["marks_awarded"] is None
    define_q = manual_exam["define"]["exam_item_id"]
    other = client.get(f"{V1}/exams/{manual_exam['exam_id']}/marking/{define_q}/answers", headers=admin_headers).json()["items"][0]
    assert other["matches_accepted"] is None          # no accepted answers on that question


def test_auto_mark_setting_defaults_off_and_locks_after_draft(client: TestClient, admin_headers: H) -> None:
    from .conftest import create_exam
    exam = create_exam(client, admin_headers)
    assert exam["auto_mark_short_answers"] is False
    r = client.patch(f"{V1}/exams/{exam['id']}", json={"auto_mark_short_answers": True}, headers=admin_headers)
    assert r.status_code == 200 and r.json()["auto_mark_short_answers"] is True
    with Session(engine) as session:
        e = session.get(ExamModel, UUID(exam["id"]))
        e.status = ExamStatus.APPROVED
        session.add(e)
        session.commit()
    r = client.patch(f"{V1}/exams/{exam['id']}", json={"auto_mark_short_answers": False}, headers=admin_headers)
    assert r.status_code in (400, 409)                 # approved exams aren't editable; the setting stays


# ------------------------------------------------------------- by student

def test_scripts_list_and_mark_one_student_through(client: TestClient, manual_exam: dict[str, Any], student_headers: H, admin_headers: H) -> None:
    exam_id = manual_exam["exam_id"]
    attempt = _sit_exam(client, student_headers, manual_exam, {
        "mcq": ["B"], "capital": ["Abuja"], "essay": ["<p>My essay</p>"],   # "define" left unanswered
    })
    page = client.get(f"{V1}/exams/{exam_id}/marking/scripts", headers=admin_headers).json()
    assert page["total"] == 1
    script = page["items"][0]
    assert script["attempt_id"] == attempt["id"] and script["student_name"] == "Test User"
    assert (script["written"], script["pending"], script["marked"], script["auto_marked"]) == (2, 2, 0, 0)
    assert script["passed"] is None
    assert client.get(f"{V1}/exams/{exam_id}/marking/scripts?anonymous=true", headers=admin_headers).json()["items"][0]["student_name"] is None

    detail = client.get(f"{V1}/exams/{exam_id}/marking/scripts/{attempt['id']}", headers=admin_headers).json()
    assert detail["total_marks"] == 12
    # Every written question in exam order, unanswered ones included.
    assert [e["question"]["question_text"] for e in detail["entries"]] == ["Q capital", "Q define", "Q essay"]
    capital, define, essay = detail["entries"]
    assert define["answer"] is None
    assert capital["answer"]["matches_accepted"] is True and capital["question"]["accepted_answers"] == ["Abuja", "FCT Abuja"]
    assert essay["question"]["marking_guide"] == "2 marks for X" and essay["answer"]["student_name"] == "Test User"

    for entry, marks in ((capital, 2), (essay, 4)):
        r = client.post(f"{V1}/attempts/responses/{entry['answer']['response_id']}/review", json={"marks_awarded": marks}, headers=admin_headers)
        assert r.status_code == 200, r.json()
    assert r.json()["attempt_final_score"] == 8.0 and r.json()["attempt_pending"] == 0

    assert client.get(f"{V1}/exams/{exam_id}/marking/scripts?status=pending", headers=admin_headers).json()["total"] == 0
    done = client.get(f"{V1}/exams/{exam_id}/marking/scripts?status=done", headers=admin_headers).json()["items"][0]
    assert (done["pending"], done["marked"], done["final_score"], done["passed"]) == (0, 2, 8.0, True)


def test_script_must_be_submitted_and_on_this_exam(client: TestClient, manual_exam: dict[str, Any], student_headers: H, admin_headers: H) -> None:
    exam_id = manual_exam["exam_id"]
    r = client.post(f"{V1}/attempts", json={"exam_id": str(exam_id), "assignment_id": str(manual_exam["assignment_id"])}, headers=student_headers)
    attempt_id = r.json()["id"]
    assert client.get(f"{V1}/exams/{exam_id}/marking/scripts", headers=admin_headers).json()["total"] == 0
    assert client.get(f"{V1}/exams/{exam_id}/marking/scripts/{attempt_id}", headers=admin_headers).status_code == 409
    assert client.get(f"{V1}/exams/{exam_id}/marking/scripts/{uuid4()}", headers=admin_headers).status_code == 404
    assert client.get(f"{V1}/exams/{exam_id}/marking/scripts", headers=student_headers).status_code == 403


def _script(n: int, pending: int, name: str, number: str | None = None) -> MarkingScript:
    return MarkingScript(
        attempt_id=uuid4(), student_id=uuid4(), student_name=name, student_number=number, number=n,
        status="submitted", written=2, pending=pending, marked=2 - pending,
    )


def test_page_scripts_filters_searches_and_pages() -> None:
    from cbt_service.schemas.marking_schemas import ScriptFilter
    from cbt_service.services.marking.marking_service import MarkingService
    scripts = [
        _script(1, 2, "Ada Obi", "JSS1-001"), _script(3, 1, "Bola Ade"),
        _script(2, 0, "Chidi Obi", "JSS1-002"), _script(4, 0, "Dayo Eze"),
    ]
    page = MarkingService.page_scripts(scripts, ScriptFilter.ALL, None, page=1, page_size=3)
    assert (page.total, page.total_pages, len(page.items)) == (4, 2, 3)
    assert (page.counts.pending, page.counts.done, page.counts.all) == (2, 2, 4)
    assert len(MarkingService.page_scripts(scripts, ScriptFilter.ALL, None, page=2, page_size=3).items) == 1

    def names(status: ScriptFilter, search: str) -> list[str | None]:
        return [s.student_name for s in MarkingService.page_scripts(scripts, status, search).items]

    assert names(ScriptFilter.ALL, "obi") == ["Ada Obi", "Chidi Obi"]
    assert names(ScriptFilter.PENDING, "obi") == ["Ada Obi"]
    assert names(ScriptFilter.ALL, "jss1-002") == ["Chidi Obi"]
    assert names(ScriptFilter.ALL, "obi ada") == ["Ada Obi"]           # every word, any order
    assert names(ScriptFilter.ALL, "script 4") == ["Dayo Eze"]
    # Counts are for the whole exam, whatever the filter or search.
    assert MarkingService.page_scripts(scripts, ScriptFilter.DONE, "zzz").counts.all == 4


def test_scripts_endpoint_pages_numbers_and_searches(
    client: TestClient, manual_exam: dict[str, Any], admin_headers: H, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import datetime, timedelta, timezone
    from cbt_service.clients.auth_client import AuthClient
    from cbt_service.database.models.attempt import AttemptModel, ResponseModel
    from cbt_service.database.models.enums.attempt_enum import AttemptStatus
    from cbt_service.schemas.item_subject_schemas import UserSummary

    exam_id = manual_exam["exam_id"]
    people: dict[UUID, tuple[str, str]] = {}
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with Session(engine) as session:
        for n in range(7):
            student = uuid4()
            people[student] = (f"Student{n}", f"No-{n:03d}")
            attempt = AttemptModel(
                exam_id=exam_id, student_id=student, org_id=ORG_ID, assignment_id=uuid4(),
                status=AttemptStatus.SUBMITTED, submitted_at=start + timedelta(minutes=n),
            )
            session.add(attempt)
            session.flush()
            # Even-numbered students are fully marked; odd ones have an essay waiting.
            session.add(ResponseModel(
                attempt_id=attempt.id, item_id=manual_exam["essay"]["item_id"],
                exam_item_id=manual_exam["essay"]["exam_item_id"], org_id=ORG_ID, answer=["<p>x</p>"],
                marks_awarded=3.0 if n % 2 == 0 else None,
                reviewed_at=start if n % 2 == 0 else None,
            ))
        session.commit()

    async def fake_bulk(self: AuthClient, user_ids: list[UUID], org_id: UUID | None = None) -> dict[str, UserSummary]:
        return {
            str(u): UserSummary(id=u, firstname=people[u][0], lastname="Test", email=None, role="student",
                                institution_id=people[u][1])
            for u in user_ids if u in people
        }
    monkeypatch.setattr(AuthClient, "get_users_bulk", fake_bulk)

    url = f"{V1}/exams/{exam_id}/marking/scripts"
    first = client.get(f"{url}?page_size=3", headers=admin_headers).json()
    assert (first["total"], first["total_pages"]) == (7, 3)
    assert first["counts"] == {"pending": 3, "done": 4, "all": 7}
    # Waiting scripts first, each in submission order; numbers follow submission order.
    assert [s["number"] for s in first["items"]] == [2, 4, 6]
    assert first["items"][0]["student_name"] == "Student1 Test" and first["items"][0]["student_number"] == "No-001"
    rest = client.get(f"{url}?page_size=3&page=3", headers=admin_headers).json()
    assert [s["number"] for s in rest["items"]] == [7]

    found = client.get(f"{url}?search=no-004", headers=admin_headers).json()
    assert [s["student_name"] for s in found["items"]] == ["Student4 Test"] and found["counts"]["all"] == 7
    assert client.get(f"{url}?search=student5&status=done", headers=admin_headers).json()["total"] == 0
    # Blind marking: names never come back, and only "Script N" is searchable.
    blind = client.get(f"{url}?search=student5&anonymous=true", headers=admin_headers).json()
    assert blind["total"] == 0
    blind = client.get(f"{url}?search=script 6&anonymous=true", headers=admin_headers).json()
    assert [(s["number"], s["student_name"], s["student_number"]) for s in blind["items"]] == [(6, None, None)]

    detail = client.get(f"{url}/{first['items'][1]['attempt_id']}", headers=admin_headers).json()
    assert (detail["number"], detail["student_number"]) == (4, "No-003")
