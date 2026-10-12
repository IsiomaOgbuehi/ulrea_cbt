# cbt_service/tests/test_violations.py

import pytest
from uuid import uuid4, UUID
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient

from sqlmodel import Session, select

from .conftest import (
    STUDENT_ID,
    TEACHER_ID,
    EXAM_ID,
    ASSIGNMENT_ID,
    ORG_ID,
    engine,
    make_auth_header,
)

from cbt_service.database.models.exam import ExamModel
from cbt_service.database.models.attempt import AttemptModel
from cbt_service.database.models.violation import ExamViolation
from cbt_service.database.models.subject import SubjectAssignment
from cbt_service.database.models.enums.attempt_enum import AttemptStatus

from .test_attempts import start_attempt, save_response, seed_attempt_exam  # reuse existing helpers


@pytest.fixture(autouse=True)
def seed_subject_assignment():
    """
    Links conftest's TEACHER_ID (the identity behind teacher_headers) to the
    seeded exam's subject, so ExamService._assert_can_view grants access.
    The exam itself is created by test_attempts.py's own local TEACHER_ID
    constant — a different, unrelated uuid4() — so without this assignment
    teacher_headers is neither the creator nor subject-assigned, and every
    view/review endpoint correctly 403s.
    """
    with Session(engine) as session:
        exam = session.exec(select(ExamModel).where(ExamModel.id == EXAM_ID)).first()
        if not exam:
            return  # seed_attempt_exam runs first via fixture ordering; guard just in case

        existing = session.exec(
            select(SubjectAssignment).where(
                SubjectAssignment.subject_id == exam.subject_id,
                SubjectAssignment.assigned_to == TEACHER_ID,
            )
        ).first()
        if not existing:
            session.add(SubjectAssignment(
                subject_id=exam.subject_id,
                org_id=ORG_ID,
                assigned_to=TEACHER_ID,
                assigned_by=exam.created_by,
            ))
            session.commit()


def report_violation(
    client,
    headers,
    attempt_id,
    violation_type="tab_switch",
    severity="low",
    description=None,
):
    response = client.post(
        f"/api/v1/attempts/{attempt_id}/violations",
        json={
            "violation_type": violation_type,
            "severity": severity,
            "description": description,
            "occurred_at": datetime.now(timezone.utc).isoformat(),
        },
        headers=headers,
    )
    return response


def set_max_violations(exam_id, max_violations):
    """Directly update the seeded exam's max_violations for a specific test."""
    with Session(engine) as session:
        exam = session.exec(select(ExamModel).where(ExamModel.id == exam_id)).first()
        exam.max_violations = max_violations
        session.add(exam)
        session.commit()


# ------------------------------------------------------------------
# Basic reporting
# ------------------------------------------------------------------

def test_student_can_report_violation(client, student_headers):
    attempt = start_attempt(client, student_headers)

    response = report_violation(client, student_headers, attempt["id"])

    assert response.status_code == 201, response.json()
    data = response.json()
    assert data["violation"]["violation_type"] == "tab_switch"
    assert data["violation"]["attempt_id"] == attempt["id"]
    assert data["violation_count"] == 1
    assert data["exam_terminated"] is False


def test_violation_count_increments_across_reports(client, student_headers):
    attempt = start_attempt(client, student_headers)

    first = report_violation(client, student_headers, attempt["id"])
    second = report_violation(client, student_headers, attempt["id"])

    assert first.json()["violation_count"] == 1
    assert second.json()["violation_count"] == 2


def test_cannot_report_violation_for_other_students_attempt(client, student_headers):
    attempt = start_attempt(client, student_headers)

    other_payload = {
        "sub": str(uuid4()),
        "org_id": str(ORG_ID),
        "role": "student",
        "verified": True,
        "type": "access",
        "jti": str(uuid4()),
        "exp": 9999999999,
    }
    other_headers = make_auth_header(other_payload)

    response = report_violation(client, other_headers, attempt["id"])
    assert response.status_code == 404


def test_cannot_report_violation_after_submission(
    client: TestClient, student_headers: dict[str, str]
) -> None:
    attempt = start_attempt(client, student_headers)

    submit_response = client.post(
        f"/api/v1/attempts/{attempt['id']}/submit",
        headers=student_headers,
    )
    assert submit_response.status_code == 200

    response = report_violation(client, student_headers, attempt["id"])

    # Closed attempt is a normal outcome: 200, nothing recorded, final state returned.
    assert response.status_code == 200, response.json()
    data = response.json()
    assert data["violation"] is None
    assert data["violation_count"] == 0
    assert data["exam_terminated"] is False
    assert data["attempt_status"] == AttemptStatus.SUBMITTED.value

    with Session(engine) as session:
        recorded = session.exec(
            select(ExamViolation).where(ExamViolation.attempt_id == UUID(attempt["id"]))
        ).all()
        assert recorded == []

# def test_cannot_report_violation_after_submission(client, student_headers):
#     attempt = start_attempt(client, student_headers)

#     submit_response = client.post(
#         f"/api/v1/attempts/{attempt['id']}/submit",
#         headers=student_headers,
#     )
#     assert submit_response.status_code == 200

#     response = report_violation(client, student_headers, attempt["id"])
#     assert response.status_code == 400


# ------------------------------------------------------------------
# max_violations enforcement / termination
# ------------------------------------------------------------------

def test_exam_terminates_when_max_violations_reached(client, student_headers):
    set_max_violations(EXAM_ID, max_violations=2)

    attempt = start_attempt(client, student_headers)
    save_response(client, student_headers, attempt["id"])  # so there's something to score

    first = report_violation(client, student_headers, attempt["id"])
    assert first.json()["exam_terminated"] is False

    second = report_violation(client, student_headers, attempt["id"])
    assert second.status_code == 201
    data = second.json()
    assert data["violation_count"] == 2
    assert data["exam_terminated"] is True

    with Session(engine) as session:
        db_attempt = session.exec(
            select(AttemptModel).where(AttemptModel.id == UUID(attempt["id"]))
        ).first()
        assert db_attempt.status == AttemptStatus.TERMINATED
        assert db_attempt.submitted_at is not None
        assert db_attempt.scored_at is not None
        assert db_attempt.scored_by == "violation_termination"


def test_terminated_attempt_still_gets_scored(client, student_headers):
    set_max_violations(EXAM_ID, max_violations=1)

    attempt = start_attempt(client, student_headers)
    save_response(client, student_headers, attempt["id"], answer=["A"])

    response = report_violation(client, student_headers, attempt["id"])
    assert response.json()["exam_terminated"] is True

    with Session(engine) as session:
        db_attempt = session.exec(
            select(AttemptModel).where(AttemptModel.id == UUID(attempt["id"]))
        ).first()
        # final_score/percentage should be populated, not left null,
        # confirming _finalize_submission ran rather than a bare status flip
        assert db_attempt.final_score is not None
        assert db_attempt.percentage is not None


def test_cannot_save_response_or_report_violation_after_termination(
    client: TestClient, student_headers: dict[str, str]
) -> None:
    set_max_violations(EXAM_ID, max_violations=1)

    attempt = start_attempt(client, student_headers)
    report_violation(client, student_headers, attempt["id"])  # triggers termination

    save_attempt_response = client.post(
        f"/api/v1/attempts/{attempt['id']}/responses",
        json={
            "item_id": str(uuid4()),
            "exam_item_id": str(uuid4()),
            "answer": ["A"],
            "time_spent_seconds": 5,
            "is_flagged": False,
        },
        headers=student_headers,
    )
    assert save_attempt_response.status_code == 409
    assert save_attempt_response.json()["detail"] == {
        "code": "ATTEMPT_CLOSED",
        "status": AttemptStatus.TERMINATED.value,
    }


def test_no_termination_when_max_violations_not_set(client, student_headers):
    set_max_violations(EXAM_ID, max_violations=None)

    attempt = start_attempt(client, student_headers)

    for _ in range(5):
        response = report_violation(client, student_headers, attempt["id"])
        assert response.json()["exam_terminated"] is False

    with Session(engine) as session:
        db_attempt = session.exec(
            select(AttemptModel).where(AttemptModel.id == UUID(attempt["id"]))
        ).first()
        assert db_attempt.status == AttemptStatus.STARTED


# ------------------------------------------------------------------
# Staff visibility
# ------------------------------------------------------------------

def test_teacher_can_view_attempt_violations(client, student_headers, teacher_headers):
    attempt = start_attempt(client, student_headers)
    report_violation(client, student_headers, attempt["id"], violation_type="tab_switch")
    report_violation(client, student_headers, attempt["id"], violation_type="fullscreen_exit")

    response = client.get(
        f"/api/v1/attempts/{attempt['id']}/violations",
        headers=teacher_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert {v["violation_type"] for v in data} == {"tab_switch", "fullscreen_exit"}


def test_student_cannot_view_attempt_violations(client, student_headers):
    attempt = start_attempt(client, student_headers)
    report_violation(client, student_headers, attempt["id"])

    response = client.get(
        f"/api/v1/attempts/{attempt['id']}/violations",
        headers=student_headers,
    )

    assert response.status_code == 403


def test_admin_can_view_exam_level_violations(client, student_headers, admin_headers):
    attempt = start_attempt(client, student_headers)
    report_violation(client, student_headers, attempt["id"])

    response = client.get(
        f"/api/v1/exams/{EXAM_ID}/violations",
        headers=admin_headers,
    )

    assert response.status_code == 200
    assert len(response.json()) == 1


def test_admin_can_view_attempt_violations(client, student_headers, admin_headers):
    attempt = start_attempt(client, student_headers)
    report_violation(client, student_headers, attempt["id"], violation_type="tab_switch")
    report_violation(client, student_headers, attempt["id"], violation_type="fullscreen_exit")

    response = client.get(
        f"/api/v1/attempts/{attempt['id']}/violations",
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert len(response.json()) == 2


def test_admin_can_mark_violation_reviewed(client, student_headers, teacher_headers):
    attempt = start_attempt(client, student_headers)
    report = report_violation(client, student_headers, attempt["id"])
    violation_id = report.json()["violation"]["id"]

    response = client.patch(
        f"/api/v1/exams/{EXAM_ID}/violations/{violation_id}/review",
        headers=teacher_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["reviewed"] is True
    assert data["reviewed_by"] is not None
    assert data["reviewed_at"] is not None



def get_status(
    client: TestClient, headers: dict[str, str], attempt_id: str
):
    return client.get(f"/api/v1/attempts/{attempt_id}/status", headers=headers)


# ------------------------------------------------------------------
# Attempt status endpoint (heartbeat)
# ------------------------------------------------------------------

def test_status_for_active_attempt(
    client: TestClient, student_headers: dict[str, str]
) -> None:
    set_max_violations(EXAM_ID, max_violations=3)
    attempt = start_attempt(client, student_headers)

    response = get_status(client, student_headers, attempt["id"])

    assert response.status_code == 200, response.json()
    data = response.json()
    assert data["status"] == AttemptStatus.STARTED.value
    assert data["remaining_seconds"] > 0
    assert data["violation_count"] == 0
    assert data["max_violations"] == 3
    assert data["server_time"] is not None


def test_status_reflects_violation_count(
    client: TestClient, student_headers: dict[str, str]
) -> None:
    set_max_violations(EXAM_ID, max_violations=5)
    attempt = start_attempt(client, student_headers)

    report_violation(client, student_headers, attempt["id"])
    report_violation(client, student_headers, attempt["id"])

    data = get_status(client, student_headers, attempt["id"]).json()
    assert data["violation_count"] == 2
    assert data["max_violations"] == 5
    assert data["status"] == AttemptStatus.STARTED.value


def test_status_max_violations_null_when_not_set(
    client: TestClient, student_headers: dict[str, str]
) -> None:
    set_max_violations(EXAM_ID, max_violations=None)
    attempt = start_attempt(client, student_headers)

    data = get_status(client, student_headers, attempt["id"]).json()
    assert data["max_violations"] is None


def test_status_after_termination(
    client: TestClient, student_headers: dict[str, str]
) -> None:
    set_max_violations(EXAM_ID, max_violations=1)
    attempt = start_attempt(client, student_headers)
    report_violation(client, student_headers, attempt["id"])  # triggers termination

    response = get_status(client, student_headers, attempt["id"])

    assert response.status_code == 200, response.json()
    data = response.json()
    assert data["status"] == AttemptStatus.TERMINATED.value
    assert data["violation_count"] == 1
    assert data["remaining_seconds"] == 0


def test_status_after_submission(
    client: TestClient, student_headers: dict[str, str]
) -> None:
    attempt = start_attempt(client, student_headers)
    client.post(f"/api/v1/attempts/{attempt['id']}/submit", headers=student_headers)

    data = get_status(client, student_headers, attempt["id"]).json()
    assert data["status"] == AttemptStatus.SUBMITTED.value
    assert data["remaining_seconds"] == 0


def test_status_auto_submits_expired_attempt(
    client: TestClient, student_headers: dict[str, str]
) -> None:
    """Polling after the time limit must close the attempt, not report it as live."""
    attempt = start_attempt(client, student_headers)

    # Backdate the start so the exam duration has long since elapsed
    with Session(engine) as session:
        db_attempt = session.exec(
            select(AttemptModel).where(AttemptModel.id == UUID(attempt["id"]))
        ).first()
        assert db_attempt is not None
        db_attempt.started_at = datetime.now(timezone.utc) - timedelta(days=1)
        session.add(db_attempt)
        session.commit()

    response = get_status(client, student_headers, attempt["id"])

    assert response.status_code == 200, response.json()
    data = response.json()
    assert data["status"] == AttemptStatus.SUBMITTED.value
    assert data["remaining_seconds"] == 0

    with Session(engine) as session:
        db_attempt = session.exec(
            select(AttemptModel).where(AttemptModel.id == UUID(attempt["id"]))
        ).first()
        assert db_attempt is not None
        assert db_attempt.scored_by == "auto_expired"


def test_status_cannot_be_read_by_other_student(
    client: TestClient, student_headers: dict[str, str]
) -> None:
    attempt = start_attempt(client, student_headers)

    other_headers = make_auth_header({
        "sub": str(uuid4()),
        "org_id": str(ORG_ID),
        "role": "student",
        "verified": True,
        "type": "access",
        "jti": str(uuid4()),
        "exp": 9999999999,
    })

    response = get_status(client, other_headers, attempt["id"])
    assert response.status_code == 404


def test_status_requires_authentication(
    client: TestClient, student_headers: dict[str, str]
) -> None:
    attempt = start_attempt(client, student_headers)

    response = client.get(f"/api/v1/attempts/{attempt['id']}/status")
    assert response.status_code in (401, 403)


def test_status_unknown_attempt_returns_404(
    client: TestClient, student_headers: dict[str, str]
) -> None:
    response = get_status(client, student_headers, str(uuid4()))
    assert response.status_code == 404