import pytest
from uuid import uuid4

from sqlmodel import Session, select

from .conftest import (
    STUDENT_ID,
    EXAM_ID,
    ASSIGNMENT_ID,
    ORG_ID,
    engine,
    make_auth_header,
)

from cbt_service.database.models.exam import (
    ExamModel,
    ExamAssignment,
)
from cbt_service.database.models.attempt import AttemptModel
from cbt_service.database.models.enums.exam_enum import (
    ExamStatus,
    AssignmentStatus,
)


TEACHER_ID = uuid4()


@pytest.fixture(autouse=True)
def seed_attempt_exam(client):
    """
    Seed the exam AND its assignment required by the attempt API.

    EXAM_ID and ASSIGNMENT_ID in conftest are UUID constants only;
    they do not automatically create database records.
    """

    with Session(engine) as session:

        # ---------------------------------------------------------
        # 1. Seed exam
        # ---------------------------------------------------------
        exam = session.exec(
            select(ExamModel).where(
                ExamModel.id == EXAM_ID
            )
        ).first()

        if not exam:
            exam = ExamModel(
                id=EXAM_ID,
                org_id=ORG_ID,
                subject_id=uuid4(),
                created_by=TEACHER_ID,
                title="Seeded Attempt Test Exam",
                duration_minutes=60,
                status=ExamStatus.ACTIVE,
                max_attempts=1,
            )

            session.add(exam)
            session.commit()
            session.refresh(exam)

        # ---------------------------------------------------------
        # 2. Seed assignment
        # ---------------------------------------------------------
        assignment = session.exec(
            select(ExamAssignment).where(
                ExamAssignment.id == ASSIGNMENT_ID
            )
        ).first()

        if not assignment:
            assignment = ExamAssignment(
                id=ASSIGNMENT_ID,
                exam_id=EXAM_ID,
                student_id=STUDENT_ID,
                org_id=ORG_ID,
                assigned_by=TEACHER_ID,
                status=AssignmentStatus.ASSIGNED,
            )

            session.add(assignment)
            session.commit()


def start_attempt(
    client,
    headers,
    exam_id=None,
    assignment_id=None,
):
    response = client.post(
        "/api/v1/attempts",
        json={
            "exam_id": str(exam_id or EXAM_ID),
            "assignment_id": str(
                assignment_id or ASSIGNMENT_ID
            ),
        },
        headers=headers,
    )

    assert response.status_code == 200, response.json()

    return response.json()


def save_response(
    client,
    headers,
    attempt_id,
    item_id=None,
    answer=None,
):
    iid = str(item_id or uuid4())

    response = client.post(
        f"/api/v1/attempts/{attempt_id}/responses",
        json={
            "item_id": iid,
            "exam_item_id": iid,
            "answer": answer or ["A"],
            "time_spent_seconds": 30,
            "is_flagged": False,
        },
        headers=headers,
    )

    assert response.status_code == 200, response.json()

    return response.json()


def test_student_can_start_attempt(
    client,
    student_headers,
):
    attempt = start_attempt(
        client,
        student_headers,
    )

    assert attempt["status"] == "started"
    assert attempt["attempt_number"] == 1
    assert attempt["exam_id"] == str(EXAM_ID)
    assert attempt["assignment_id"] == str(ASSIGNMENT_ID)


def test_starting_attempt_again_resumes_existing(
    client,
    student_headers,
):
    """
    POST /attempts while an attempt is already STARTED
    returns the same attempt.

    This supports refresh/reconnect during an exam.
    """

    first = start_attempt(
        client,
        student_headers,
    )

    response = client.post(
        "/api/v1/attempts",
        json={
            "exam_id": str(EXAM_ID),
            "assignment_id": str(ASSIGNMENT_ID),
        },
        headers=student_headers,
    )

    assert response.status_code == 200

    second = response.json()

    assert second["id"] == first["id"]
    assert second["status"] == "started"


def test_cannot_start_new_attempt_after_max_attempts_reached(
    client,
    student_headers,
):
    attempt = start_attempt(
        client,
        student_headers,
    )

    submit_response = client.post(
        f"/api/v1/attempts/{attempt['id']}/submit",
        headers=student_headers,
    )

    assert submit_response.status_code == 200

    response = client.post(
        "/api/v1/attempts",
        json={
            "exam_id": str(EXAM_ID),
            "assignment_id": str(ASSIGNMENT_ID),
        },
        headers=student_headers,
    )

    assert response.status_code == 400
    assert "maximum attempts" in (
        response.json()["detail"].lower()
    )


def test_student_can_save_response(
    client,
    student_headers,
):
    attempt = start_attempt(
        client,
        student_headers,
    )

    item_id = uuid4()

    response = save_response(
        client,
        student_headers,
        attempt["id"],
        item_id,
        ["B"],
    )

    assert response["answer"] == ["B"]
    assert response["attempt_id"] == attempt["id"]


def test_save_response_is_upsert(
    client,
    student_headers,
):
    """
    Saving the same item twice should update
    the existing response instead of creating a duplicate.
    """

    attempt = start_attempt(
        client,
        student_headers,
    )

    item_id = uuid4()

    save_response(
        client,
        student_headers,
        attempt["id"],
        item_id,
        ["A"],
    )

    save_response(
        client,
        student_headers,
        attempt["id"],
        item_id,
        ["B"],
    )

    responses = client.get(
        f"/api/v1/attempts/{attempt['id']}/responses",
        headers=student_headers,
    ).json()

    assert len(responses) == 1
    assert responses[0]["answer"] == ["B"]


def test_can_flag_response_for_review(
    client,
    student_headers,
):
    attempt = start_attempt(
        client,
        student_headers,
    )

    item_id = uuid4()
    iid = str(item_id)

    response = client.post(
        f"/api/v1/attempts/{attempt['id']}/responses",
        json={
            "item_id": iid,
            "exam_item_id": iid,
            "answer": ["A"],
            "time_spent_seconds": 10,
            "is_flagged": True,
        },
        headers=student_headers,
    )

    assert response.status_code == 200
    assert response.json()["is_flagged"] is True


def test_cannot_save_response_to_submitted_attempt(
    client,
    student_headers,
):
    attempt = start_attempt(
        client,
        student_headers,
    )

    submit_response = client.post(
        f"/api/v1/attempts/{attempt['id']}/submit",
        headers=student_headers,
    )

    assert submit_response.status_code == 200

    item_id = str(uuid4())

    response = client.post(
        f"/api/v1/attempts/{attempt['id']}/responses",
        json={
            "item_id": item_id,
            "exam_item_id": item_id,
            "answer": ["A"],
            "time_spent_seconds": 5,
            "is_flagged": False,
        },
        headers=student_headers,
    )

    assert response.status_code == 400


def test_submit_attempt(
    client,
    student_headers,
):
    attempt = start_attempt(
        client,
        student_headers,
    )

    save_response(
        client,
        student_headers,
        attempt["id"],
    )

    response = client.post(
        f"/api/v1/attempts/{attempt['id']}/submit",
        headers=student_headers,
    )

    assert response.status_code == 200

    data = response.json()

    assert data["status"] == "submitted"
    assert data["submitted_at"] is not None


def test_student_cannot_view_other_students_attempt(
    client,
    student_headers,
):
    attempt = start_attempt(
        client,
        student_headers,
    )

    other_payload = {
        "sub": str(uuid4()),
        "org_id": str(ORG_ID),
        "role": "student",
        "verified": True,
        "type": "access",
        "jti": str(uuid4()),
        "exp": 9999999999,
    }

    other_headers = make_auth_header(
        other_payload
    )

    response = client.get(
        f"/api/v1/attempts/{attempt['id']}",
        headers=other_headers,
    )

    assert response.status_code == 404


def test_teacher_can_manually_review_response(
    client,
    student_headers,
    teacher_headers,
):
    attempt = start_attempt(
        client,
        student_headers,
    )

    resp = save_response(
        client,
        student_headers,
        attempt["id"],
    )

    submit_response = client.post(
        f"/api/v1/attempts/{attempt['id']}/submit",
        headers=student_headers,
    )

    assert submit_response.status_code == 200

    review_response = client.post(
        f"/api/v1/attempts/responses/{resp['id']}/review",
        json={
            "response_id": resp["id"],
            "marks_awarded": 4.5,
            "review_notes": "Good answer.",
        },
        headers=teacher_headers,
    )

    assert review_response.status_code == 200

    data = review_response.json()

    assert data["marks_awarded"] == 4.5
    assert data["is_correct"] is True


def test_student_cannot_manually_review(
    client,
    student_headers,
):
    attempt = start_attempt(
        client,
        student_headers,
    )

    resp = save_response(
        client,
        student_headers,
        attempt["id"],
    )

    response = client.post(
        f"/api/v1/attempts/responses/{resp['id']}/review",
        json={
            "response_id": resp["id"],
            "marks_awarded": 5.0,
        },
        headers=student_headers,
    )

    assert response.status_code == 403


def test_restarting_after_reset_reactivates_same_attempt(
    client,
    admin_headers,
    student_headers,
):
    attempt = start_attempt(
        client,
        student_headers,
    )

    original_id = attempt["id"]

    reset_response = client.post(
        f"/api/v1/attempts/{original_id}/reset",
        json={
            "reason": "Technical issue during exam",
        },
        headers=admin_headers,
    )

    assert reset_response.status_code == 200

    resumed = start_attempt(
        client,
        student_headers,
    )

    assert resumed["id"] == original_id
    assert resumed["status"] == "started"

    submit_response = client.post(
        f"/api/v1/attempts/{original_id}/submit",
        headers=student_headers,
    )

    assert submit_response.status_code == 200
    assert submit_response.json()["id"] == original_id

    # Verify that resetting/restarting did not create
    # another attempt record.
    with Session(engine) as session:
        attempts = session.exec(
            select(AttemptModel).where(
                AttemptModel.exam_id == EXAM_ID,
                AttemptModel.student_id == STUDENT_ID,
            )
        ).all()

        assert len(attempts) == 1