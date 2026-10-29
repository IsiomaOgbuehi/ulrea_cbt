"""
Exam settings are honoured for candidates: shuffling, attempts allowed,
showing results and reviewing answers. Plus changing attempts allowed after
approval (admins only).
"""
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlmodel import Session, select
from starlette.testclient import TestClient

from cbt_service.database.models.enums.exam_enum import AssignmentStatus, ExamStatus
from cbt_service.database.models.enums.item_subject_enums import ItemStatus, ItemType
from cbt_service.database.models.exam import ExamAssignment, ExamAuditLog, ExamItem, ExamModel, ExamSection
from cbt_service.database.models.item import ItemModel
from cbt_service.database.models.subject import SubjectModel
from .conftest import ORG_ID, STUDENT_ID, TEACHER_ID, engine

H = dict[str, str]
V1 = "/api/v1"
OPTIONS: list[dict[str, str]] = [{"key": k, "text": f"Option {k}"} for k in "ABCDE"]


def _exam(
    *, n_mcq: int = 8, status: ExamStatus = ExamStatus.ACTIVE, **settings: Any,
) -> dict[str, Any]:
    """An exam of n MCQs (answer A, 1 mark each), a True/False and an essay; assigned to the student."""
    subject_id, exam_id, section_id = uuid4(), uuid4(), uuid4()
    out: dict[str, Any] = {"exam_id": exam_id, "items": []}
    with Session(engine) as session:
        session.add(SubjectModel(id=subject_id, org_id=ORG_ID, name="Maths", created_by=TEACHER_ID))
        session.add(ExamModel(
            id=exam_id, org_id=ORG_ID, subject_id=subject_id, created_by=TEACHER_ID, title="Settings exam",
            status=status, pass_mark=50, total_marks=n_mcq + 6, **settings,
        ))
        session.add(ExamSection(id=section_id, exam_id=exam_id, org_id=ORG_ID, title="All", order=0))
        specs: list[tuple[ItemType, list[str] | None, list[dict[str, str]] | None, float]] = (
            [(ItemType.MCQ_SINGLE, ["A"], OPTIONS, 1.0)] * n_mcq
            + [(ItemType.TRUE_FALSE, ["True"], [{"key": "True", "text": "True"}, {"key": "False", "text": "False"}], 1.0)]
            + [(ItemType.ESSAY, None, None, 5.0)]
        )
        for n, (item_type, correct, options, marks) in enumerate(specs):
            item = ItemModel(
                org_id=ORG_ID, subject_id=subject_id, created_by=TEACHER_ID, question_text=f"Q{n + 1}",
                item_type=item_type, correct_answers=correct, options=options, marks=marks,
                status=ItemStatus.ACTIVE, marking_guide="Secret guide" if item_type == ItemType.ESSAY else None,
            )
            session.add(item)
            session.flush()
            session.add(ExamItem(exam_id=exam_id, section_id=section_id, item_id=item.id, org_id=ORG_ID, order=n, marks=marks))
            out["items"].append({"item_id": item.id, "type": item_type.value})
        assignment = ExamAssignment(
            exam_id=exam_id, student_id=STUDENT_ID, org_id=ORG_ID, assigned_by=TEACHER_ID,
            status=AssignmentStatus.ASSIGNED,
        )
        session.add(assignment)
        session.commit()
        out["assignment_id"] = assignment.id
    return out


def _start(client: TestClient, h: H, exam: dict[str, Any]) -> dict[str, Any]:
    r = client.post(f"{V1}/attempts", json={"exam_id": str(exam["exam_id"]), "assignment_id": str(exam["assignment_id"])}, headers=h)
    assert r.status_code == 200, r.json()
    return r.json()


def _content(client: TestClient, h: H, attempt_id: str) -> list[dict[str, Any]]:
    content = client.get(f"{V1}/attempts/{attempt_id}/exam", headers=h).json()
    return [i for s in content["sections"] for i in s["items"]]


def _answer_all(client: TestClient, h: H, attempt_id: str) -> None:
    for i in _content(client, h, attempt_id):
        answer = ["A"] if i["item_type"] == "mcq_single" else ["True"] if i["item_type"] == "true_false" else ["<p>My essay</p>"]
        r = client.post(f"{V1}/attempts/{attempt_id}/responses", json={"item_id": i["id"], "exam_item_id": i["exam_item_id"], "answer": answer}, headers=h)
        assert r.status_code == 200, r.json()


def _mine(client: TestClient, h: H, exam: dict[str, Any]) -> dict[str, Any]:
    rows = client.get(f"{V1}/exams/my-assignments", headers=h).json()
    return next(r for r in rows if r["exam_id"] == str(exam["exam_id"]))


# ------------------------------------------------------------- shuffling

def test_questions_and_options_keep_exam_order_when_not_shuffled(client: TestClient, student_headers: H) -> None:
    exam = _exam()
    attempt = _start(client, student_headers, exam)
    items = _content(client, student_headers, attempt["id"])
    assert [i["question_text"] for i in items] == [f"Q{n}" for n in range(1, 11)]
    assert [o["key"] for o in items[0]["options"]] == list("ABCDE")


def test_shuffled_questions_and_options_are_stable_per_attempt_and_differ_between_attempts(client: TestClient, student_headers: H) -> None:
    exam = _exam(shuffle_questions=True, shuffle_options=True, max_attempts=3)
    orders: list[list[str]] = []
    option_orders: list[list[str]] = []
    for _ in range(3):
        attempt = _start(client, student_headers, exam)
        first = _content(client, student_headers, attempt["id"])
        again = _content(client, student_headers, attempt["id"])
        assert first == again                                   # a refresh keeps the order
        orders.append([i["question_text"] for i in first])
        assert sorted(orders[-1]) == sorted(f"Q{n}" for n in range(1, 11))
        mcqs = [i for i in first if i["item_type"] == "mcq_single"]
        option_orders += [[o["key"] for o in i["options"]] for i in mcqs]
        tf = next(i for i in first if i["item_type"] == "true_false")
        assert [o["key"] for o in tf["options"]] == ["True", "False"]   # True/False never shuffled
        client.post(f"{V1}/attempts/{attempt['id']}/submit", headers=student_headers)
    assert any(o != [f"Q{n}" for n in range(1, 11)] for o in orders)   # questions really move
    assert len({tuple(o) for o in orders}) > 1                          # and differ per attempt
    assert any(o != list("ABCDE") for o in option_orders)


# ------------------------------------------------------------- attempts allowed

def test_attempts_allowed_and_my_exams_list(client: TestClient, student_headers: H) -> None:
    exam = _exam(max_attempts=2, show_attempts_used=True)
    row = _mine(client, student_headers, exam)
    assert (row["attempts_used"], row["max_attempts"], row["can_retake"], row["has_attempted"]) == (0, 2, False, False)

    first = _start(client, student_headers, exam)
    row = _mine(client, student_headers, exam)
    assert row["status"] == "started" and row["in_progress_attempt_id"] == first["id"]
    # Starting again resumes the same attempt.
    assert _start(client, student_headers, exam)["id"] == first["id"]

    client.post(f"{V1}/attempts/{first['id']}/submit", headers=student_headers)
    row = _mine(client, student_headers, exam)
    assert row["status"] == "submitted" and row["latest_attempt_id"] == first["id"]
    assert (row["attempts_used"], row["can_retake"], row["in_progress_attempt_id"]) == (1, True, None)

    second = _start(client, student_headers, exam)
    assert second["id"] != first["id"] and second["attempt_number"] == 2
    client.post(f"{V1}/attempts/{second['id']}/submit", headers=student_headers)
    row = _mine(client, student_headers, exam)
    assert (row["attempts_used"], row["can_retake"], row["latest_attempt_id"]) == (2, False, second["id"])

    r = client.post(f"{V1}/attempts", json={"exam_id": str(exam["exam_id"]), "assignment_id": str(exam["assignment_id"])}, headers=student_headers)
    assert r.status_code == 400 and "Maximum attempts" in r.json()["detail"]


def test_exam_counts_submitted_candidates(client: TestClient, student_headers: H, admin_headers: H) -> None:
    exam = _exam()
    attempt = _start(client, student_headers, exam)
    client.post(f"{V1}/attempts/{attempt['id']}/submit", headers=student_headers)
    read = client.get(f"{V1}/exams/{exam['exam_id']}", headers=admin_headers).json()
    assert (read["assigned_count"], read["submitted_count"]) == (1, 1)


# ------------------------------------------------------------- results & review

def test_hidden_results_never_reach_the_student(client: TestClient, student_headers: H, admin_headers: H) -> None:
    exam = _exam(show_result_immediately=False, allow_review=True)
    attempt = _start(client, student_headers, exam)
    assert (attempt["show_result"], attempt["allow_review"]) == (False, True)
    _answer_all(client, student_headers, attempt["id"])
    submitted = client.post(f"{V1}/attempts/{attempt['id']}/submit", headers=student_headers).json()
    for read in (submitted, client.get(f"{V1}/attempts/{attempt['id']}", headers=student_headers).json()):
        assert (read["final_score"], read["percentage"], read["passed"], read["raw_score"]) == (None, None, None, None)
    responses = client.get(f"{V1}/attempts/{attempt['id']}/responses", headers=student_headers).json()
    assert responses and all(r["marks_awarded"] is None and r["is_correct"] is None for r in responses)
    # Review shows their answers, but no marks or correct answers.
    review = client.get(f"{V1}/attempts/{attempt['id']}/review", headers=student_headers).json()
    assert review["show_result"] is False and review["final_score"] is None
    assert all(i["answer"] and i["marks_awarded"] is None and i["correct_answers"] is None for i in review["items"])
    # Staff still see the score.
    detail = client.get(f"{V1}/attempts/{attempt['id']}/detail", headers=admin_headers).json()
    assert detail["final_score"] == 9.0


def test_review_with_results(client: TestClient, student_headers: H) -> None:
    exam = _exam(n_mcq=2, show_result_immediately=True, allow_review=True)
    attempt = _start(client, student_headers, exam)
    assert client.get(f"{V1}/attempts/{attempt['id']}/review", headers=student_headers).status_code == 409   # not yet submitted
    _answer_all(client, student_headers, attempt["id"])
    client.post(f"{V1}/attempts/{attempt['id']}/submit", headers=student_headers)
    review = client.get(f"{V1}/attempts/{attempt['id']}/review", headers=student_headers).json()
    assert [i["question_text"] for i in review["items"]] == ["Q1", "Q2", "Q3", "Q4"]   # exam order
    mcq, _, tf, essay = review["items"]
    assert (mcq["marks_awarded"], mcq["is_correct"], mcq["correct_answers"]) == (1.0, True, ["A"])
    assert [o["text"] for o in mcq["options"]][0] == "Option A"
    assert essay["awaiting_marking"] is True and essay["correct_answers"] is None
    assert "Secret guide" not in str(review)
    assert review["awaiting_marking"] == 1


def test_review_refused_when_not_allowed(client: TestClient, student_headers: H) -> None:
    exam = _exam(allow_review=False)
    attempt = _start(client, student_headers, exam)
    client.post(f"{V1}/attempts/{attempt['id']}/submit", headers=student_headers)
    assert client.get(f"{V1}/attempts/{attempt['id']}/review", headers=student_headers).status_code == 403


# ------------------------------------------------------------- changing attempts after approval

@pytest.mark.parametrize("status,expected", [
    (ExamStatus.APPROVED, 200), (ExamStatus.ACTIVE, 200),
    (ExamStatus.DRAFT, 409), (ExamStatus.PENDING_APPROVAL, 409), (ExamStatus.CLOSED, 409),
])
def test_admins_change_attempts_only_once_approved_or_live(client: TestClient, admin_headers: H, status: ExamStatus, expected: int) -> None:
    exam = _exam(status=status)
    r = client.patch(f"{V1}/exams/{exam['exam_id']}/max-attempts", json={"max_attempts": 3}, headers=admin_headers)
    assert r.status_code == expected, r.json()
    if expected == 200:
        assert r.json()["max_attempts"] == 3
        with Session(engine) as session:
            log = session.exec(select(ExamAuditLog).where(ExamAuditLog.exam_id == exam["exam_id"])).all()
        assert any(entry.extra_data == {"field": "max_attempts", "from": 1, "to": 3} for entry in log)


def test_changing_attempts_needs_the_permission_and_a_sensible_number(client: TestClient, admin_headers: H, teacher_headers: H, student_headers: H) -> None:
    exam = _exam(status=ExamStatus.APPROVED)
    url = f"{V1}/exams/{exam['exam_id']}/max-attempts"
    assert client.patch(url, json={"max_attempts": 3}, headers=teacher_headers).status_code == 403
    assert client.patch(url, json={"max_attempts": 3}, headers=student_headers).status_code == 403
    assert client.patch(url, json={"max_attempts": 0}, headers=admin_headers).status_code == 422


def test_raising_attempts_lets_a_finished_student_retake(client: TestClient, student_headers: H, admin_headers: H) -> None:
    exam = _exam()
    attempt = _start(client, student_headers, exam)
    client.post(f"{V1}/attempts/{attempt['id']}/submit", headers=student_headers)
    assert _mine(client, student_headers, exam)["can_retake"] is False
    client.patch(f"{V1}/exams/{exam['exam_id']}/max-attempts", json={"max_attempts": 2}, headers=admin_headers)
    assert _mine(client, student_headers, exam)["can_retake"] is True
    assert _start(client, student_headers, exam)["attempt_number"] == 2


# ------------------------------------------------------------- releasing results after approval

@pytest.mark.parametrize("status,expected", [
    (ExamStatus.APPROVED, 200), (ExamStatus.ACTIVE, 200), (ExamStatus.CLOSED, 200),
    (ExamStatus.DRAFT, 409), (ExamStatus.PENDING_APPROVAL, 409), (ExamStatus.ARCHIVED, 409),
])
def test_results_can_be_released_from_approval_on(client: TestClient, admin_headers: H, status: ExamStatus, expected: int) -> None:
    exam = _exam(status=status, show_result_immediately=False)
    r = client.patch(f"{V1}/exams/{exam['exam_id']}/show-results", json={"show_result_immediately": True}, headers=admin_headers)
    assert r.status_code == expected, r.json()
    if expected == 200:
        assert r.json()["show_result_immediately"] is True


def test_releasing_results_shows_scores_to_students(client: TestClient, student_headers: H, admin_headers: H, teacher_headers: H) -> None:
    exam = _exam(show_result_immediately=False, allow_review=True)
    attempt = _start(client, student_headers, exam)
    _answer_all(client, student_headers, attempt["id"])
    client.post(f"{V1}/attempts/{attempt['id']}/submit", headers=student_headers)
    assert client.get(f"{V1}/attempts/{attempt['id']}", headers=student_headers).json()["final_score"] is None

    url = f"{V1}/exams/{exam['exam_id']}/show-results"
    assert client.patch(url, json={"show_result_immediately": True}, headers=teacher_headers).status_code == 403
    assert client.patch(url, json={"show_result_immediately": True}, headers=admin_headers).status_code == 200

    read = client.get(f"{V1}/attempts/{attempt['id']}", headers=student_headers).json()
    assert (read["show_result"], read["final_score"]) == (True, 9.0)
    review = client.get(f"{V1}/attempts/{attempt['id']}/review", headers=student_headers).json()
    assert review["items"][0]["marks_awarded"] == 1.0
    assert _mine(client, student_headers, exam)["show_result"] is True
    with Session(engine) as session:
        log = session.exec(select(ExamAuditLog).where(ExamAuditLog.exam_id == exam["exam_id"])).all()
    assert any(e.extra_data == {"field": "show_result_immediately", "from": False, "to": True} for e in log)
    # And hidden again.
    client.patch(url, json={"show_result_immediately": False}, headers=admin_headers)
    assert client.get(f"{V1}/attempts/{attempt['id']}", headers=student_headers).json()["final_score"] is None


# ------------------------------------------------------------- slow connections

def _age(attempt_id: str, minutes: int, seconds: int) -> None:
    """Pretend the attempt started minutes + seconds ago (its saves move back in time too)."""
    from datetime import datetime, timedelta, timezone
    from cbt_service.database.models.attempt import AttemptModel, ResponseModel
    with Session(engine) as session:
        row = session.get(AttemptModel, UUID(attempt_id))
        assert row is not None
        started = row.started_at if row.started_at.tzinfo else row.started_at.replace(tzinfo=timezone.utc)
        new_start = datetime.now(timezone.utc) - timedelta(minutes=minutes, seconds=seconds)
        shift = new_start - started
        row.started_at = new_start
        session.add(row)
        for r in session.exec(select(ResponseModel).where(ResponseModel.attempt_id == row.id)).all():
            updated = r.updated_at if r.updated_at.tzinfo else r.updated_at.replace(tzinfo=timezone.utc)
            r.updated_at = updated + shift
            session.add(r)
        session.commit()


def _save(client: TestClient, h: H, attempt_id: str, item: dict[str, Any], answer: list[str]) -> Any:
    return client.post(f"{V1}/attempts/{attempt_id}/responses", json={"item_id": item["id"], "exam_item_id": item["exam_item_id"], "answer": answer}, headers=h)


def test_time_is_strict_and_late_answers_never_change_the_score(client: TestClient, student_headers: H, admin_headers: H) -> None:
    exam = _exam(duration_minutes=10)
    attempt = _start(client, student_headers, exam)
    q1, q2 = _content(client, student_headers, attempt["id"])[:2]
    assert _save(client, student_headers, attempt["id"], q1, ["B"]).status_code == 200   # on time, wrong

    _age(attempt["id"], 10, 5)          # 5 s after the deadline
    late = _save(client, student_headers, attempt["id"], q1, ["A"])
    assert late.status_code == 202 and late.json()["held_for_review"] is True
    _save(client, student_headers, attempt["id"], q2, ["A"])
    read = client.get(f"{V1}/attempts/{attempt['id']}", headers=student_headers).json()
    assert read["status"] == "submitted" and read["final_score"] == 0.0      # strict: late answers don't count

    detail = client.get(f"{V1}/attempts/{attempt['id']}/detail", headers=admin_headers).json()
    lates = {l["question_text"]: l for l in detail["late_answers"]}
    assert set(lates) == {"Q1", "Q2"}
    assert lates["Q1"]["answer"] == ["A"] and lates["Q1"]["current_answer"] == ["B"]
    assert lates["Q1"]["seconds_late"] >= 5 and lates["Q1"]["last_on_time_save_at"] is not None
    assert lates["Q2"]["current_answer"] is None

    # Accepting one re-scores; rejecting leaves the score alone.
    r = client.post(f"{V1}/attempts/late-answers/{lates['Q1']['id']}/decision", json={"accept": True}, headers=admin_headers)
    assert r.status_code == 200 and r.json()["final_score"] == 1.0
    r = client.post(f"{V1}/attempts/late-answers/{lates['Q2']['id']}/decision", json={"accept": False}, headers=admin_headers)
    assert r.json()["final_score"] == 1.0
    statuses = {l["question_text"]: l["status"] for l in r.json()["late_answers"]}
    assert statuses == {"Q1": "accepted", "Q2": "rejected"}
    assert client.post(f"{V1}/attempts/late-answers/{lates['Q1']['id']}/decision", json={"accept": True}, headers=admin_headers).status_code == 409


def test_late_answers_only_for_attempts_closed_by_time_and_within_the_window(client: TestClient, student_headers: H, teacher_headers: H) -> None:
    # Submitted early by the student: anything after is refused outright.
    exam = _exam(duration_minutes=10, max_attempts=2)
    attempt = _start(client, student_headers, exam)
    q1 = _content(client, student_headers, attempt["id"])[0]
    client.post(f"{V1}/attempts/{attempt['id']}/submit", headers=student_headers)
    assert _save(client, student_headers, attempt["id"], q1, ["A"]).status_code == 409

    # Far too late (past the 10-minute window): refused.
    second = _start(client, student_headers, exam)
    _age(second["id"], 21, 0)
    assert _save(client, student_headers, second["id"], q1, ["A"]).status_code == 409


def test_late_answer_decisions_need_marking_permission(client: TestClient, student_headers: H, admin_headers: H) -> None:
    exam = _exam(duration_minutes=10)
    attempt = _start(client, student_headers, exam)
    q1 = _content(client, student_headers, attempt["id"])[0]
    _age(attempt["id"], 10, 5)
    _save(client, student_headers, attempt["id"], q1, ["A"])
    late_id = client.get(f"{V1}/attempts/{attempt['id']}/detail", headers=admin_headers).json()["late_answers"][0]["id"]
    assert client.post(f"{V1}/attempts/late-answers/{late_id}/decision", json={"accept": True}, headers=student_headers).status_code == 403


def test_connection_check_endpoints(client: TestClient, student_headers: H) -> None:
    ping = client.get(f"{V1}/network/ping", headers=student_headers)
    assert ping.status_code == 200 and ping.json()["ok"] is True
    assert "no-store" in ping.headers["cache-control"]
    down = client.get(f"{V1}/network/download?size_kb=64", headers=student_headers)
    assert down.status_code == 200 and len(down.content) == 64 * 1024
    assert client.get(f"{V1}/network/download?size_kb=999999", headers=student_headers).status_code == 422
    up = client.post(f"{V1}/network/upload", content=b"x" * 50_000, headers={**student_headers, "Content-Type": "application/octet-stream"})
    assert up.json() == {"received_bytes": 50_000}
    too_big = client.post(f"{V1}/network/upload", content=b"x" * (1024 * 1024 + 1), headers={**student_headers, "Content-Type": "application/octet-stream"})
    assert too_big.status_code == 413
    assert client.get(f"{V1}/network/ping").status_code == 401


def test_results_list_students_assigned_one_by_one(client: TestClient, student_headers: H, admin_headers: H) -> None:
    exam = _exam()
    attempt = _start(client, student_headers, exam)
    client.post(f"{V1}/attempts/{attempt['id']}/submit", headers=student_headers)
    rows = client.get(f"{V1}/attempts/exams/{exam['exam_id']}/individual/attempts", headers=admin_headers).json()
    assert [r["id"] for r in rows] == [attempt["id"]] and rows[0]["student_name"] == "Test User"
    assert client.get(f"{V1}/attempts/exams/{exam['exam_id']}/individual/attempts", headers=student_headers).status_code == 403


def test_attempts_used_are_hidden_from_candidates_unless_shown(client: TestClient, student_headers: H, admin_headers: H) -> None:
    exam = _exam(max_attempts=2)
    attempt = _start(client, student_headers, exam)
    client.post(f"{V1}/attempts/{attempt['id']}/submit", headers=student_headers)
    row = _mine(client, student_headers, exam)
    # Hidden by default, but a retake is still offered.
    assert (row["show_attempts_used"], row["attempts_used"], row["max_attempts"], row["can_retake"]) == (False, None, None, True)

    url = f"{V1}/exams/{exam['exam_id']}/max-attempts"
    r = client.patch(url, json={"show_attempts_used": True}, headers=admin_headers)
    assert r.status_code == 200 and r.json()["show_attempts_used"] is True and r.json()["max_attempts"] == 2
    row = _mine(client, student_headers, exam)
    assert (row["attempts_used"], row["max_attempts"]) == (1, 2)
    assert client.patch(url, json={}, headers=admin_headers).status_code == 422
    with Session(engine) as session:
        log = session.exec(select(ExamAuditLog).where(ExamAuditLog.exam_id == exam["exam_id"])).all()
    assert any(e.extra_data == {"field": "show_attempts_used", "from": False, "to": True} for e in log)


def test_new_exams_hide_attempts_used_by_default(client: TestClient, admin_headers: H) -> None:
    with Session(engine) as session:
        subject = SubjectModel(org_id=ORG_ID, name="Physics", created_by=TEACHER_ID)
        session.add(subject)
        session.commit()
        subject_id = str(subject.id)
    r = client.post(f"{V1}/exams", json={"title": "Defaults", "subject_id": subject_id}, headers=admin_headers)
    assert r.status_code == 200 and r.json()["show_attempts_used"] is False
    r = client.patch(f"{V1}/exams/{r.json()['id']}", json={"show_attempts_used": True}, headers=admin_headers)
    assert r.json()["show_attempts_used"] is True


def test_a_reset_attempt_is_offered_as_a_fresh_start(client: TestClient, student_headers: H, admin_headers: H) -> None:
    exam = _exam()
    attempt = _start(client, student_headers, exam)
    assert _mine(client, student_headers, exam)["restart"] is False          # a normal resume
    client.post(f"{V1}/attempts/{attempt['id']}/submit", headers=student_headers)
    r = client.post(f"{V1}/attempts/{attempt['id']}/reset", json={"reason": "Power cut"}, headers=admin_headers)
    assert r.status_code == 200, r.json()
    row = _mine(client, student_headers, exam)
    assert (row["restart"], row["in_progress_attempt_id"], row["status"]) == (True, attempt["id"], "assigned")
